"""Publication checks for the executed prototype bundle, without RTL tools."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class PrototypePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = read("artifacts/prototype_manifest.json")
        cls.report = read("artifacts/prototype_validation.json")
        cls.rtl = read("artifacts/rtl_validation.json")

    def test_exact_inputs_and_generated_outputs_are_current(self):
        sources = self.manifest["source_sha256"]
        required = {"sim/finite_buffer.py", "sim/moe_inference.py", "sim/transport.py",
                    "sim/physical_validation.py", "scripts/run_prototype_validation.py",
                    "scripts/run_rtl_validation.py", "rtl/sync_fifo.sv",
                    "rtl/word_fabric_top.sv", "tests/test_prototype_integration.py",
                    "tests/test_prototype_publication.py", "examples/moe_inference.json",
                    "examples/measurements/missing_hardware.json"}
        self.assertTrue(required <= set(sources))
        outputs = self.manifest["output_sha256"]
        self.assertEqual(set(outputs), {"artifacts/prototype_validation.json",
                                       "artifacts/rtl_validation.json", "paper/prototype_results.tex"})
        for relative, expected in list(sources.items()) + list(outputs.items()):
            with self.subTest(path=relative):
                path = Path(relative)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), expected)

    def test_success_is_explicitly_scoped_away_from_hardware_measurements(self):
        self.assertEqual(self.report["publication_version"], self.manifest["publication_version"])
        self.assertEqual(self.report["status"], "software_and_rtl_checks_passed")
        self.assertIs(self.report["physical_validation_complete"], False)
        self.assertIs(self.report["fpga_bringup_complete"], False)
        self.assertEqual(self.report["physical_measurement_assessment"]["status"],
                         "INSUFFICIENT_EVIDENCE")
        self.assertGreaterEqual(len(self.report["remaining_evidence"]), 4)

    def test_finite_network_terminates_only_with_complete_credit_and_flit_ledgers(self):
        network = self.report["finite_buffer"]
        for scenario in ("baseline", "paused_sink"):
            result = network[scenario]
            self.assertTrue(result["idle"])
            self.assertTrue(result["all_requested_delivered"])
            self.assertTrue(result["routing_certificate"]["acyclic"])
            self.assertEqual(result["status"], "completed")
            accounting = result["accounting"]
            self.assertEqual(accounting["requested_flits"], accounting["delivered_flits"])
            self.assertEqual(accounting["requested_packets"], accounting["delivered_packets"])
            self.assertFalse(result["pending_packets"] or result["rejected_packets"])
            for credit in result["credits"]:
                self.assertEqual(credit["available"], credit["capacity"])
                self.assertEqual(credit["resident"] + credit["inflight"] + credit["return_pending"], 0)
        self.assertGreater(network["paused_sink"]["elapsed_cycles"], network["baseline"]["elapsed_cycles"])
        self.assertEqual(network["bounded_cutoff"]["status"], "max_cycles")
        self.assertTrue(network["bounded_cutoff"]["accounting"]["conservation_ok"])
        self.assertTrue(network["bounded_cutoff"]["pending_packets"])
        self.assertEqual(len(network["partition"]["rejected_packets"]), 1)

    def test_actual_inference_fault_recovery_and_ack_uncertainty_are_recorded(self):
        normal = self.report["inference"]
        self.assertTrue(normal["numerical_validation"]["all_logits_close"])
        self.assertTrue(normal["numerical_validation"]["greedy_tokens_match"])
        self.assertEqual(normal["execution"]["contributions_expected"],
                         normal["execution"]["contributions_completed"])
        faults = self.report["inference_fault_recovery"]
        for field in ("all_logits_close", "remote_contributions_executed_once",
                      "weighted_contributions_combined_once"):
            self.assertTrue(faults["numerical_validation"][field])
        self.assertTrue(faults["all_acks_lost"]["aborted_without_cache_commit"])
        self.assertFalse(faults["all_acks_lost"]["logits_accepted"])
        self.assertEqual(faults["all_acks_lost"]["cache_length"], 0)
        self.assertEqual(faults["all_acks_lost"]["combined_contributions"], 0)

    def test_rtl_evidence_is_the_actual_runner_result_with_consistent_counts(self):
        self.assertEqual(self.report["rtl"], self.rtl)
        self.assertEqual(self.rtl["status"], "passed")
        self.assertEqual(set(self.rtl["tool_versions"]), {"iverilog", "vvp", "yosys"})
        self.assertGreaterEqual(len(self.rtl["simulations"]), 4)
        for case in self.rtl["simulations"]:
            self.assertEqual(case["accepted"], case["delivered"] + case["reset_flushed"])
            self.assertGreaterEqual(case["hotspot_delivered"], 1000)
            self.assertLess(case["max_fairness_bypasses"], case["ports"])
        synthesis = self.rtl["synthesis"]
        self.assertEqual(synthesis["cell_count"], sum(synthesis["cells_by_type"].values()))
        self.assertGreater(synthesis["sequential_cell_count"], 0)

    def test_paper_macro_values_are_derived_from_saved_reports(self):
        text = (ROOT / "paper/prototype_results.tex").read_text(encoding="utf-8")
        macros = dict(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{([^{}\n]*)\}", text))
        names = re.findall(r"\\newcommand\{\\([^}]+)\}", text)
        self.assertTrue(all(re.fullmatch("[A-Za-z]+", name) for name in names))
        self.assertEqual(macros["PrototypeVersion"], self.manifest["publication_version"])
        self.assertEqual(macros["PrototypeDate"], self.manifest["date"])
        expected = {"PrototypePackets": self.report["finite_buffer"]["baseline"]["accounting"]["delivered_packets"],
                    "PrototypeNetworkCycles": self.report["finite_buffer"]["baseline"]["elapsed_cycles"],
                    "PrototypeTokens": len(self.report["inference"]["execution"]["processed_tokens"]),
                    "PrototypeContributions": self.report["inference"]["execution"]["contributions_completed"],
                    "PrototypeRtlConfigurations": len(self.rtl["simulations"]),
                    "PrototypeRtlTransfers": sum(case["delivered"] for case in self.rtl["simulations"]),
                    "PrototypeRtlCells": self.rtl["synthesis"]["cell_count"]}
        for name, value in expected.items():
            self.assertEqual(int(macros[name]), value)
        error = self.report["inference"]["numerical_validation"]["maximum_absolute_logit_error"]
        self.assertEqual(float(macros["PrototypeLogitError"]), float(f"{error:.3g}"))
        paper = (ROOT / "paper/main.tex").read_text(encoding="utf-8")
        self.assertIn(r"\input{prototype_results.tex}", paper)
        self.assertIn(r"\PrototypeStatusRows", paper)

    def test_checker_is_read_only_and_works_outside_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/run_prototype_validation.py"), "--check"],
                                    cwd=temporary, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_false_hardware_success_is_rejected_even_with_updated_output_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in list(self.manifest["output_sha256"]) + ["artifacts/prototype_manifest.json"]:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            report = self.report.copy()
            report["physical_validation_complete"] = True
            path = root / "artifacts/prototype_validation.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            manifest = json.loads(json.dumps(self.manifest))
            manifest["output_sha256"]["artifacts/prototype_validation.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
            (root / "artifacts/prototype_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/run_prototype_validation.py"),
                                     "--check", "--output-root", str(root)],
                                    cwd=temporary, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("evidence scope", result.stderr)


if __name__ == "__main__":
    unittest.main()
