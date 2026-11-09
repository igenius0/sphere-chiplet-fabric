"""Guard the released paper against stale data, provenance and figure claims.

These checks inspect the committed evidence bundle. They do not require TeX,
rerun the full experiments, or compare figures across Matplotlib versions.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
from statistics import median
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIGURE_STEMS = {
    "fig1_hop_cdf", "fig2_latency_cdf", "fig3_latency_load",
    "fig4_tail_ratio", "fig5_allreduce", "fig6_balanced_cut",
    "fig7_hotspot", "fig8_energy_proxy",
}


def read_json(relative):
    def reject_nonfinite(value):
        raise ValueError("Non-finite value in publication JSON: " + value)
    return json.loads((ROOT / relative).read_text(encoding="utf-8"),
                      parse_constant=reject_nonfinite)


def source_without_comments(text):
    return re.sub(r"(?<!\\)%.*", "", text)


def numeric_macros(text):
    values = re.findall(
        r"\\(?:newcommand|providecommand)\{\\([A-Za-z]+)\}\{([^{}\n]*)\}", text)
    return {name: value for name, value in values}


class PublicationIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = read_json("artifacts/publication_manifest.json")
        cls.evaluation = read_json("artifacts/results.json")
        cls.capacity = read_json("artifacts/performance_model.json")
        cls.sweep = read_json("examples/results/design_sweep.json")
        cls.generated_tex = (ROOT / "paper/generated_results.tex").read_text(encoding="utf-8")
        cls.macros = numeric_macros(cls.generated_tex)

    def test_recorded_source_fingerprints_match_current_model(self):
        self.assertEqual(self.manifest["schema_version"], 1)
        sources = self.manifest["source_sha256"]
        required = {"sim/fabric.py", "sim/topology.py", "sim/workloads.py",
                    "sim/run_evaluation.py", "sim/run_sweep.py",
                    "sim/performance_model.py", "sim/validation.py",
                    "requirements.txt", "scripts/rebuild_publication.py"}
        self.assertTrue(required <= set(sources))
        for relative, recorded in sources.items():
            with self.subTest(source=relative):
                path = self._safe_path(relative)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), recorded,
                                 "Rebuild publication after changing model sources")
        for relative, recorded in self.sweep["provenance"]["source_sha256"].items():
            with self.subTest(sweep_source=relative):
                self.assertEqual(hashlib.sha256(self._safe_path(relative).read_bytes()).hexdigest(),
                                 recorded)

    def test_manifest_authenticates_all_numerical_tables_and_figures(self):
        outputs = self.manifest["output_sha256"]
        required = {"artifacts/results.json", "artifacts/performance_model.json",
                    "examples/results/design_sweep.json", "paper/generated_results.tex"}
        required |= {f"figures/{stem}.{extension}" for stem in FIGURE_STEMS
                     for extension in ("png", "pdf")}
        self.assertTrue(required <= set(outputs))
        self.assertNotIn("artifacts/publication_manifest.json", outputs)
        for relative, recorded in outputs.items():
            with self.subTest(output=relative):
                path = self._safe_path(relative)
                self.assertGreater(path.stat().st_size, 0)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), recorded,
                                 "Publication output changed without a rebuilt manifest")

    def test_active_figure_bundle_contains_only_supported_model_figures(self):
        expected = {stem + "." + suffix for stem in FIGURE_STEMS
                    for suffix in ("pdf", "png")}
        actual = {path.name for path in (ROOT / "figures").iterdir() if path.is_file()}
        self.assertEqual(actual, expected)
        for relative in self.manifest["retired_figure_paths"]:
            with self.subTest(retired=relative):
                self.assertFalse(self._safe_path(relative).exists())

    def test_paper_references_current_figures_and_generated_evidence(self):
        paper = source_without_comments((ROOT / "paper/main.tex").read_text(encoding="utf-8"))
        self.assertRegex(paper, r"\\input\{(?:\./)?generated_results(?:\.tex)?\}")
        for rows in ("EvaluationSummaryRows", "CollectiveSummaryRows",
                     "SweepSummaryRows", "CapacitySummaryRows"):
            self.assertIn("\\" + rows, paper)
            self.assertIn("\\newcommand{\\" + rows + "}", self.generated_tex)
        graphic_paths = [ROOT / "paper"]
        for group in re.findall(r"\\graphicspath\s*\{((?:\{[^}]*\})+)\}", paper):
            graphic_paths.extend(ROOT / "paper" / item
                                 for item in re.findall(r"\{([^}]*)\}", group))
        referenced = set()
        for reference in re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", paper):
            candidates = []
            for directory in graphic_paths:
                base = directory / reference
                candidates.extend([base] if base.suffix else
                                  [base.with_suffix(".pdf"), base.with_suffix(".png")])
            found = next((path for path in candidates if path.is_file()), None)
            self.assertIsNotNone(found, "Missing paper figure: " + reference)
            referenced.add(found.stem)
        self.assertEqual(referenced, FIGURE_STEMS)

    def test_revision_and_citation_do_not_relabel_the_historical_doi(self):
        publication = self.manifest["publication"]
        self.assertRegex(publication["version"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(publication["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(self.macros["PublicationVersion"], publication["version"])
        self.assertEqual(self.macros["PublicationDate"], publication["date"])
        citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        version = re.search(r"^version:\s*[\"']?([^\s\"']+)", citation, re.MULTILINE)
        date = re.search(r"^date-released:\s*[\"']?([^\s\"']+)", citation, re.MULTILINE)
        self.assertIsNotNone(version)
        self.assertIsNotNone(date)
        self.assertEqual(version.group(1), publication["version"])
        self.assertEqual(date.group(1), publication["date"])
        self.assertNotRegex(citation, r"(?m)^doi:\s*[\"']?10\.5281/zenodo\.19918238")

    def test_evaluation_configuration_and_equal_bandwidth_are_explicit(self):
        config = self.manifest["configuration"]["evaluation"]
        self.assertEqual(self.evaluation["config"], config)
        self.assertEqual(config["bandwidth_policy"], "equal_total")
        candidates = self.evaluation["candidates"]
        self.assertEqual({candidate["name"] for candidate in candidates},
                         {"sphere", "sphere_chords", "torus"})
        budgets = [candidate["resource_budget"] for candidate in candidates]
        self.assertEqual({budget["nodes"] for budget in budgets}, {162})
        target = budgets[0]["aggregate_directed_bandwidth_gbps"]
        for budget in budgets:
            self._assert_budget(budget)
            self.assertAlmostEqual(budget["aggregate_directed_bandwidth_gbps"], target)
        for candidate in candidates:
            collective = candidate["all_reduce"]
            self.assertEqual(collective["per_rank_payload_flits"], 162 * 8)
            self.assertEqual(collective["tree_message_flits"], 162 * 8)
            self.assertEqual(collective["ring_chunk_flits"], 8)

    def test_sweep_matrix_and_packet_accounting_are_complete(self):
        settings = self.sweep["settings"]
        profiles = self.sweep["profiles"]
        configurations = self.manifest["configuration"]["sweep"]
        self.assertEqual(settings["n_packets_per_profile"], configurations["n_packets"])
        self.assertEqual(settings["seeds"], configurations["seeds"])
        expected = {(workload, ranks, policy, seed)
                    for workload in ("moe_dispatch", "stencil")
                    for ranks in (42, 162)
                    for policy in ("equal_total", "per_link")
                    for seed in settings["seeds"]}
        observed = {(p["config"]["workload"], p["rank_count"],
                     p["config"]["bandwidth_policy"], p["config"]["seed"])
                    for p in profiles}
        self.assertEqual(observed, expected)
        self.assertEqual(len(profiles), len(expected))
        self.assertEqual(settings["profile_count"], len(profiles))
        attempts = 0
        for profile in profiles:
            config = profile["config"]
            budgets = []
            for candidate in profile["candidates"]:
                self._assert_budget(candidate["resource_budget"])
                budgets.append(candidate["resource_budget"])
                self.assertEqual(candidate["resource_budget"]["nodes"], profile["rank_count"])
                failure = candidate["failure"]
                self._assert_budget(failure["resource_budget"])
                self.assertEqual(failure["resource_budget"]["undirected_links"],
                                 candidate["resource_budget"]["undirected_links"]
                                 - len(failure["removed_undirected_links"]))
                for metrics in (candidate["baseline"], failure["metrics"]):
                    self.assertEqual(metrics["generated_packets"], config["n_packets"])
                    self.assertEqual(metrics["generated_packets"],
                                     metrics["delivered_packets"] + metrics["unreachable_packets"])
                    self.assertAlmostEqual(metrics["delivery_fraction"],
                                           metrics["delivered_packets"] / metrics["generated_packets"])
                    self.assertGreater(metrics["p99_latency_ns"], 0)
                    attempts += metrics["generated_packets"]
            if config["bandwidth_policy"] == "equal_total":
                self.assertAlmostEqual(budgets[0]["aggregate_directed_bandwidth_gbps"],
                                       budgets[1]["aggregate_directed_bandwidth_gbps"])
            else:
                self.assertAlmostEqual(budgets[0]["link_bandwidth_gbps_per_direction"],
                                       budgets[1]["link_bandwidth_gbps_per_direction"])
                self.assertGreater(budgets[0]["aggregate_directed_bandwidth_gbps"],
                                   budgets[1]["aggregate_directed_bandwidth_gbps"])
        self.assertEqual(attempts, settings["total_packet_attempts"])

    def test_published_sweep_summaries_match_individual_paired_runs(self):
        self.assertEqual(len(self.sweep["cohorts"]), 8)
        for cohort in self.sweep["cohorts"]:
            profiles = [p for p in self.sweep["profiles"]
                        if p["config"]["workload"] == cohort["workload"]
                        and p["rank_count"] == cohort["rank_count"]
                        and p["config"]["bandwidth_policy"] == cohort["bandwidth_policy"]]
            self.assertEqual({p["config"]["seed"] for p in profiles}, set(cohort["seeds"]))
            for scenario in ("baseline", "failure"):
                values = {"sphere": [], "torus": []}
                unreachable = {"sphere": 0, "torus": 0}
                for profile in profiles:
                    for candidate in profile["candidates"]:
                        metrics = candidate["baseline"] if scenario == "baseline" else candidate["failure"]["metrics"]
                        name = candidate["candidate"]
                        values[name].append(metrics["p99_latency_ns"])
                        unreachable[name] += metrics["unreachable_packets"]
                for name in values:
                    self._assert_descriptive_summary(
                        cohort[scenario][name + "_p99_latency_ns"], values[name])
                    self.assertEqual(cohort["total_unreachable_packets"][name + "_" + scenario],
                                     unreachable[name])
                paired_ratios = [s / t for s, t in zip(values["sphere"], values["torus"])]
                self._assert_descriptive_summary(
                    cohort[scenario]["paired_sphere_over_torus_p99_ratio"], paired_ratios)

    def test_generated_numeric_macros_match_raw_evidence_to_display_precision(self):
        for name, value in {
                "EvaluationPacketCount": self.evaluation["config"]["n_packets"],
                "EvaluationSeed": self.evaluation["config"]["seed"],
                "EvaluationRankCount": self.evaluation["candidates"][0]["resource_budget"]["nodes"],
                "EvaluationPayloadBytes": self.evaluation["candidates"][0]["all_reduce"]["per_rank_payload_flits"] * 64,
        }.items():
            self._assert_displayed_number(name, value)
        for candidate in self.evaluation["candidates"]:
            prefix = {"sphere": "Sphere", "sphere_chords": "ChordSphere", "torus": "Torus"}[candidate["name"]]
            latency = candidate["uncontended_latency"]
            budget = candidate["resource_budget"]
            expected = {
                "IsolatedPfiftyNs": latency["p50_latency_ns"],
                "IsolatedPninetyNineNs": latency["p99_latency_ns"],
                "IsolatedTailRatio": latency["p99_latency_ns"] / latency["p50_latency_ns"],
                "AverageHops": candidate["topology_stats"]["avg_hops"],
                "MaxHops": candidate["topology_stats"]["max_hops"],
                "TreeAllReduceUs": candidate["all_reduce"]["tree_time_ns"] / 1000,
                "RingAllReduceUs": candidate["all_reduce"]["ring_time_ns"] / 1000,
                "CutGbps": candidate["balanced_coordinate_cut"]["cut_bandwidth_gbps"],
                "LinkGbps": budget["link_bandwidth_gbps_per_direction"],
                "AggregateBandwidthGbps": budget["aggregate_directed_bandwidth_gbps"],
                "WireLengthMm": budget["total_link_length_mm"],
            }
            for suffix, raw in expected.items():
                self._assert_displayed_number(prefix + suffix, raw)
        bounds = self.capacity["aggregate_bounds"]
        for name, raw in {
                "AssumedDenseFpEightPflops": bounds["fp8_pflops_dense"],
                "AssumedHbmCapacityGb": bounds["hbm_capacity_gb"],
                "LocalHbmTbps": bounds["hbm_bw_TBps"],
                "RemoteHbmTbps": bounds["effective_remote_hbm_bw_TBps"],
                "PartialPowerWatts": bounds["power_component_subtotal_w"],
                "MinimumLocalPercent": 100 * (1 - bounds["effective_remote_hbm_bw_TBps"] / bounds["hbm_bw_TBps"]),
        }.items():
            self._assert_displayed_number(name, raw)
        for cohort in self.sweep["cohorts"]:
            prefix = "Moe" if cohort["workload"] == "moe_dispatch" else "Stencil"
            prefix += "FortyTwo" if cohort["rank_count"] == 42 else "OneSixtyTwo"
            prefix += "EqualTotal" if cohort["bandwidth_policy"] == "equal_total" else "PerLink"
            baseline = cohort["baseline"]
            ratio = baseline["paired_sphere_over_torus_p99_ratio"]
            for suffix, raw in {
                    "Ratio": ratio["median"], "RatioMin": ratio["min"], "RatioMax": ratio["max"],
                    "SpherePninetyNineNs": baseline["sphere_p99_latency_ns"]["median"],
                    "TorusPninetyNineNs": baseline["torus_p99_latency_ns"]["median"],
            }.items():
                self._assert_displayed_number(prefix + suffix, raw)

    def test_generated_tex_uses_valid_latex_control_sequence_names(self):
        names = re.findall(r"\\newcommand\{\\([^}]+)\}", self.generated_tex)
        self.assertGreater(len(names), 40)
        for name in names:
            with self.subTest(command=name):
                self.assertRegex(name, r"^[A-Za-z]+$",
                                 "Digits in a control sequence name break the paper build")

    def test_generated_result_rows_match_raw_evidence(self):
        evaluation_rows = self._table_rows("EvaluationSummaryRows")
        collective_rows = self._table_rows("CollectiveSummaryRows")
        self.assertEqual(len(evaluation_rows), len(self.evaluation["candidates"]))
        self.assertEqual(len(collective_rows), len(self.evaluation["candidates"]))
        labels = {"sphere": "Sphere", "sphere_chords": "Sphere+chords", "torus": "Torus"}
        for index, candidate in enumerate(self.evaluation["candidates"]):
            latency = candidate["uncontended_latency"]
            stats = candidate["topology_stats"]
            evaluation = evaluation_rows[index]
            self.assertEqual(evaluation[0], labels[candidate["name"]])
            self.assertEqual(len(evaluation), 6)
            for displayed, raw in zip(evaluation[1:],
                    [stats["avg_hops"], stats["max_hops"], latency["p50_latency_ns"],
                     latency["p99_latency_ns"], latency["p99_latency_ns"] / latency["p50_latency_ns"]]):
                self._assert_precision(displayed, raw)
            collective = collective_rows[index]
            self.assertEqual(collective[0], labels[candidate["name"]])
            self.assertEqual(len(collective), 4)
            for displayed, raw in zip(collective[1:],
                    [candidate["all_reduce"]["ring_time_ns"] / 1000,
                     candidate["all_reduce"]["tree_time_ns"] / 1000,
                     candidate["balanced_coordinate_cut"]["cut_bandwidth_gbps"]]):
                self._assert_precision(displayed, raw)
        sweep_rows = self._table_rows("SweepSummaryRows")
        self.assertEqual(len(sweep_rows), len(self.sweep["cohorts"]))
        for row, cohort in zip(sweep_rows, self.sweep["cohorts"]):
            self.assertEqual(row[0], "MoE" if cohort["workload"] == "moe_dispatch" else "Stencil")
            self.assertEqual(row[1], str(cohort["rank_count"]))
            self.assertEqual(row[2], "equal total" if cohort["bandwidth_policy"] == "equal_total" else "per link")
            self.assertEqual(len(row), 8)
            ratio = cohort["baseline"]["paired_sphere_over_torus_p99_ratio"]
            for displayed, raw in zip(row[3:],
                    [cohort["baseline"]["sphere_p99_latency_ns"]["median"],
                     cohort["baseline"]["torus_p99_latency_ns"]["median"],
                     ratio["median"], ratio["min"], ratio["max"]]):
                self._assert_precision(displayed, raw)

    def test_publication_checker_rejects_a_tampered_output_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            for relative in list(self.manifest["output_sha256"]) + ["artifacts/publication_manifest.json"]:
                target = bundle / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            changed = bundle / "artifacts/results.json"
            changed.write_text("{}\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/rebuild_publication.py"),
                 "--check", "--output-root", str(bundle)],
                cwd=temporary, text=True, capture_output=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stale publication output", result.stderr)
            self.assertEqual(changed.read_text(encoding="utf-8"), "{}\n")

    def test_committed_paper_pdf_matches_its_recorded_sources(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_paper.py"), "--check"],
            cwd=ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        pdf = (ROOT / "paper/main.pdf").read_bytes()
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 10000)

    def test_capacity_report_exposes_endpoint_bound_and_missing_system_power(self):
        assumed = self.capacity["design_assumptions"]
        bounds = self.capacity["aggregate_bounds"]
        memory_count = assumed["tiles"]["memory_ctrl"]["count"]
        local = memory_count * assumed["hbm_bw_per_stack_gbps"] / 8 / 1000
        remote = memory_count * assumed["fabric_links_per_tile"] * assumed["fabric_link_bw_gbps"] / 8 / 1000
        self.assertAlmostEqual(bounds["hbm_bw_TBps"], local)
        self.assertAlmostEqual(bounds["remote_hbm_fabric_bound_TBps"], remote)
        self.assertLess(bounds["effective_remote_hbm_bw_TBps"], local)
        self.assertIsNone(bounds["full_system_power_w"])
        self.assertIn("HBM stacks", bounds["missing_power_terms"])
        roofline = self.capacity["illustrative_remote_memory_roofline"]
        intensity = self.manifest["configuration"]["capacity"]["arithmetic_intensity_flops_per_byte"]
        self.assertAlmostEqual(roofline["memory_bound_tflops"],
                               bounds["effective_remote_hbm_bw_TBps"] * intensity)

    def _safe_path(self, relative):
        path = Path(relative)
        self.assertFalse(path.is_absolute())
        self.assertNotIn("..", path.parts)
        return ROOT / path

    def _assert_budget(self, budget):
        self.assertAlmostEqual(budget["aggregate_directed_bandwidth_gbps"],
                               2 * budget["undirected_links"] * budget["link_bandwidth_gbps_per_direction"])
        self.assertAlmostEqual(budget["average_degree"],
                               2 * budget["undirected_links"] / budget["nodes"])

    def _assert_descriptive_summary(self, summary, values):
        for key, value in {"median": median(values), "min": min(values), "max": max(values)}.items():
            self.assertAlmostEqual(summary[key], value)

    def _assert_displayed_number(self, macro, raw):
        self.assertIn(macro, self.macros)
        displayed = self.macros[macro]
        self._assert_precision(displayed, raw, macro + " is stale")

    def _assert_precision(self, displayed, raw, message="Published table is stale"):
        self.assertRegex(displayed, r"^-?\d+(?:\.\d+)?$")
        decimals = len(displayed.partition(".")[2])
        self.assertEqual(displayed, f"{raw:.{decimals}f}", message)

    def _table_rows(self, macro):
        match = re.search(r"\\newcommand\{\\" + macro + r"\}\{\n(.*?)\n\}",
                          self.generated_tex, re.DOTALL)
        self.assertIsNotNone(match)
        rows = []
        for line in match.group(1).splitlines():
            self.assertTrue(line.endswith(r"\\"))
            rows.append([cell.strip() for cell in line[:-2].split("&")])
        return rows


if __name__ == "__main__":
    unittest.main()
