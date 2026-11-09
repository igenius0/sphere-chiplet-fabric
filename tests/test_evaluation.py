"""Reproducible evaluation entry-point and comparison contract checks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sim.topology import make_icosphere, make_torus


ROOT = Path(__file__).resolve().parents[1]


class EvaluationImportTests(unittest.TestCase):
    def test_import_is_silent_without_path_or_output_side_effects(self):
        code = """
import sys
sys.path.insert(0, sys.argv[1])
before = list(sys.path)
import sim.run_evaluation
assert sys.path == before, 'import changed the module search path'
"""
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([sys.executable, "-c", code, str(ROOT)],
                                       cwd=directory, text=True,
                                       capture_output=True, timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(list(Path(directory).iterdir()), [])


class CoordinateCutTests(unittest.TestCase):
    def test_known_ring_cut_counts_each_crossing_physical_link_once(self):
        from sim.run_evaluation import balanced_coordinate_cut
        result = balanced_coordinate_cut(make_torus(1, 4), link_bw_gbps=256)
        self.assertEqual(result["partition_sizes"], [2, 2])
        self.assertEqual(result["crossing_links"], 2)
        self.assertAlmostEqual(result["cut_bandwidth_gbps"], 512)

    def test_sphere_partition_is_balanced_even_with_equatorial_vertices(self):
        from sim.run_evaluation import balanced_coordinate_cut
        for subdivisions, count in ((1, 42), (2, 162)):
            with self.subTest(subdivisions=subdivisions):
                cut = balanced_coordinate_cut(make_icosphere(subdivisions))
                self.assertEqual(cut["partition_sizes"], [count//2, count//2])
                self.assertGreater(cut["crossing_links"], 0)

    def test_odd_node_partition_sizes_differ_by_only_one(self):
        from sim.run_evaluation import balanced_coordinate_cut
        cut = balanced_coordinate_cut(make_torus(1, 5))
        self.assertEqual(cut["partition_sizes"], [2, 3])


class EvaluationCommandLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.output = Path(cls.directory.name)/"evaluation"
        completed = subprocess.run(
            [sys.executable, str(ROOT/"sim"/"run_evaluation.py"),
             "--output-dir", str(cls.output), "--n-packets", "12",
             "--subdivisions", "1", "--seed", "7"],
            cwd=cls.directory.name, text=True, capture_output=True, timeout=60)
        if completed.returncode:
            raise AssertionError(completed.stderr)
        cls.report = json.loads((cls.output/"results.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_outputs_go_to_requested_directory_and_all_figures_are_created(self):
        figures = self.output/"figures"
        pngs = list(figures.glob("fig*.png"))
        pdfs = list(figures.glob("fig*.pdf"))
        self.assertEqual(len(pngs), 8)
        self.assertEqual(len(pdfs), 8)
        self.assertTrue(all(path.stat().st_size > 0 for path in pngs+pdfs))

    def test_evaluation_uses_equal_nodes_and_equal_total_baseline_bandwidth(self):
        candidates = self.report["candidates"]
        self.assertEqual(len(candidates), 3)
        budgets = [candidate["resource_budget"] for candidate in candidates]
        self.assertEqual([budget["nodes"] for budget in budgets], [42, 42, 42])
        for budget in budgets[1:]:
            self.assertAlmostEqual(budget["aggregate_directed_bandwidth_gbps"],
                                   budgets[0]["aggregate_directed_bandwidth_gbps"])

    def test_collective_comparison_matches_full_per_rank_payload(self):
        for candidate in self.report["candidates"]:
            collective = candidate["all_reduce"]
            count = candidate["resource_budget"]["nodes"]
            self.assertEqual(collective["per_rank_payload_flits"], count*8)
            self.assertEqual(collective["ring_chunk_flits"], 8)
            self.assertEqual(collective["tree_message_flits"], count*8)
            self.assertGreater(collective["ring_time_ns"], 0)
            self.assertGreater(collective["tree_time_ns"], 0)

    def test_no_figures_mode_writes_only_requested_json(self):
        output = Path(self.directory.name)/"no-figures"
        completed = subprocess.run(
            [sys.executable, "-m", "sim.run_evaluation", "--output-dir", str(output),
             "--n-packets", "4", "--subdivisions", "1", "--no-figures"],
            cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((output/"results.json").is_file())
        self.assertFalse((output/"figures").exists())

    def test_invalid_packet_count_fails_without_traceback(self):
        output = Path(self.directory.name)/"invalid"
        completed = subprocess.run(
            [sys.executable, "-m", "sim.run_evaluation", "--output-dir", str(output),
             "--n-packets", "0", "--no-figures"],
            cwd=ROOT, text=True, capture_output=True, timeout=20)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("error:", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
