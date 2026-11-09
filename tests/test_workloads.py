"""Application-traffic comparison contracts and command-line integration."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sim.fabric import LatencyModel
from sim.topology import make_icosphere, make_torus
from sim.workloads import (WorkloadConfig, compare_workloads, make_workload_pairs,
                           remove_links, resource_budget, undirected_edges)


ROOT = Path(__file__).resolve().parents[1]


class WorkloadInputTests(unittest.TestCase):
    def test_invalid_or_unknown_configuration_is_rejected(self):
        for values in ([], "", 0, {"typo": 1}, {"n_packets": 0},
                       {"n_packets": True}, {"packet_size_flits": 1.5},
                       {"subdivisions": 3}, {"failed_links": -1},
                       {"workload": "llama_tokens_per_second"},
                       {"bandwidth_policy": "unspecified"},
                       {"injection_rate_pkts_per_ns": float("nan")},
                       {"link_rate_gbps": float("inf")},
                       {"expert_hotspot_share": 1.1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                WorkloadConfig.from_dict(values)

    def test_compare_rejects_falsey_non_object_input(self):
        for value in ([], "", 0, False):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compare_workloads(value)

    def test_generated_rank_pairs_are_seeded_and_in_range(self):
        for subdivisions, rank_count in ((1, 42), (2, 162)):
            for workload in ("moe_dispatch", "stencil"):
                with self.subTest(subdivisions=subdivisions, workload=workload):
                    config = WorkloadConfig(subdivisions=subdivisions,
                                            workload=workload, n_packets=200)
                    pairs = make_workload_pairs(config)
                    self.assertEqual(pairs, make_workload_pairs(config))
                    self.assertEqual(len(pairs), 200)
                    self.assertTrue(all(0 <= src < rank_count and
                                        0 <= dst < rank_count and src != dst
                                        for src, dst in pairs))

    def test_stencil_only_uses_logical_periodic_grid_neighbors(self):
        config = WorkloadConfig(workload="stencil", n_packets=400)
        grid = make_torus(6, 7)
        for src, dst in make_workload_pairs(config):
            self.assertIn(dst, grid["adj"][src])


class ResourceAndFailureTests(unittest.TestCase):
    def test_resource_units_count_both_directed_capacities(self):
        budget = resource_budget(make_torus(6, 7), LatencyModel())
        self.assertEqual(budget["nodes"], 42)
        self.assertEqual(budget["undirected_links"], 84)
        self.assertEqual(budget["aggregate_directed_bandwidth_gbps"], 84*2*256)
        self.assertEqual(budget["total_link_length_mm"], 84*4)

    def test_failure_removes_both_directions_without_mutating_original(self):
        original = make_icosphere(1)
        original_edges = undirected_edges(original)
        degraded, removed = remove_links(original, 5, seed=9)
        self.assertEqual(undirected_edges(original), original_edges)
        self.assertEqual(len(removed), 5)
        self.assertEqual(len(undirected_edges(degraded)), len(original_edges)-5)
        self.assertEqual(remove_links(original, 5, seed=9)[1], removed)
        for src, dst in removed:
            self.assertNotIn(dst, degraded["adj"][src])
            self.assertNotIn(src, degraded["adj"][dst])
            self.assertNotIn((src, dst), degraded["link_length"])
            self.assertNotIn((dst, src), degraded["link_length"])

    def test_zero_failure_preserves_graph_and_exhaustive_failure_cuts_all_edges(self):
        topo = make_torus(3, 3)
        unchanged, removed = remove_links(topo, 0, seed=42)
        self.assertEqual(unchanged, topo)
        self.assertEqual(removed, [])
        empty, removed = remove_links(topo, len(undirected_edges(topo)), seed=42)
        self.assertTrue(all(not neighbors for neighbors in empty["adj"].values()))
        self.assertEqual(empty["link_length"], {})
        self.assertEqual(len(removed), 18)


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = WorkloadConfig(n_packets=80, failed_links=3)
        cls.report = compare_workloads(cls.config)

    def test_report_is_reproducible_and_strict_json_serializable(self):
        second = compare_workloads(self.config)
        self.assertEqual(self.report, second)
        self.assertEqual(json.loads(json.dumps(self.report, allow_nan=False)), self.report)

    def test_candidates_have_equal_nodes_and_equal_total_baseline_bandwidth(self):
        budgets = [c["resource_budget"] for c in self.report["candidates"]]
        self.assertEqual(len(budgets), 2)
        self.assertEqual([b["nodes"] for b in budgets], [42, 42])
        self.assertAlmostEqual(budgets[0]["aggregate_directed_bandwidth_gbps"],
                               budgets[1]["aggregate_directed_bandwidth_gbps"])
        self.assertNotEqual(budgets[0]["undirected_links"], budgets[1]["undirected_links"])

    def test_all_generated_packets_are_accounted_for(self):
        for candidate in self.report["candidates"]:
            for scenario in (candidate["baseline"], candidate["failure"]["metrics"]):
                with self.subTest(candidate=candidate["candidate"], scenario=scenario):
                    self.assertEqual(scenario["generated_packets"], 80)
                    self.assertEqual(scenario["delivered_packets"]+
                                     scenario["unreachable_packets"], 80)
                    self.assertEqual(scenario["delivery_fraction"],
                                     scenario["delivered_packets"]/80)
                    self.assertGreater(scenario["observation_window_ns"], 0)

    def test_failure_budget_matches_removed_links(self):
        for candidate in self.report["candidates"]:
            failure = candidate["failure"]
            self.assertEqual(len(failure["removed_undirected_links"]), 3)
            self.assertEqual(candidate["resource_budget"]["undirected_links"]-
                             failure["resource_budget"]["undirected_links"], 3)

    def test_zero_failures_yield_identical_metrics(self):
        report = compare_workloads(WorkloadConfig(n_packets=20, failed_links=0))
        for candidate in report["candidates"]:
            self.assertEqual(candidate["baseline"], candidate["failure"]["metrics"])

    def test_no_deliveries_reports_unreachable_and_no_latency_estimate(self):
        # This fixed seed removes the links needed by the torus rank-0 to
        # rank-2 packet. Missing deliveries must never look like zero latency.
        report = compare_workloads(WorkloadConfig(n_packets=1, failed_links=20, seed=6))
        torus = next(c for c in report["candidates"] if c["candidate"] == "torus")
        metrics = torus["failure"]["metrics"]
        self.assertEqual(metrics["delivered_packets"], 0)
        self.assertEqual(metrics["unreachable_packets"], 1)
        self.assertEqual(metrics["delivery_fraction"], 0)
        self.assertIsNone(metrics["p99_latency_ns"])
        self.assertIsNone(metrics["mean_latency_ns"])
        self.assertIsNone(torus["failure"]["p99_latency_ratio_vs_baseline"])
        json.dumps(report, allow_nan=False)

    def test_per_link_policy_exposes_different_aggregate_bandwidth(self):
        report = compare_workloads(WorkloadConfig(n_packets=10, bandwidth_policy="per_link"))
        sphere, torus = [c["resource_budget"] for c in report["candidates"]]
        self.assertEqual(sphere["link_bandwidth_gbps_per_direction"],
                         torus["link_bandwidth_gbps_per_direction"])
        self.assertGreater(sphere["aggregate_directed_bandwidth_gbps"],
                           torus["aggregate_directed_bandwidth_gbps"])


class CommandLineTests(unittest.TestCase):
    def test_direct_script_works_from_an_arbitrary_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"nested"/"report.json"
            completed = subprocess.run(
                [sys.executable, str(ROOT/"sim"/"run_workloads.py"),
                 "--n-packets", "12", "--json", "--output", str(output)],
                cwd=directory, text=True, capture_output=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            printed = json.loads(completed.stdout)
            self.assertEqual(printed, json.loads(output.read_text()))
            self.assertEqual(printed["workload_summary"]["generated_packets"], 12)

    def test_module_supports_stencil_and_162_ranks(self):
        completed = subprocess.run(
            [sys.executable, "-m", "sim.run_workloads", "--n-packets", "12",
             "--workload", "stencil", "--subdivisions", "2", "--json"],
            cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["config"]["workload"], "stencil")
        self.assertEqual(report["workload_summary"]["rank_count"], 162)

    def test_cli_invalid_json_unknown_keys_and_values_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"config.json"
            for content in ("{", '[]', '{"typo": 1}', '{"n_packets": 0}'):
                with self.subTest(content=content):
                    path.write_text(content)
                    completed = subprocess.run(
                        [sys.executable, "-m", "sim.run_workloads", "--config", str(path)],
                        cwd=ROOT, text=True, capture_output=True, timeout=30)
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("error:", completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)

    def test_cli_overrides_file_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"config.json"
            path.write_text('{"n_packets": 3, "workload": "stencil"}')
            completed = subprocess.run(
                [sys.executable, "-m", "sim.run_workloads", "--config", str(path),
                 "--n-packets", "8", "--json"],
                cwd=ROOT, text=True, capture_output=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["config"]["n_packets"], 8)
            self.assertEqual(report["config"]["workload"], "stencil")


if __name__ == "__main__":
    unittest.main()
