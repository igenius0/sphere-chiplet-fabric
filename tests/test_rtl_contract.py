"""Contract and actual-tool coverage for the independent local-word RTL track."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rtl_validation", ROOT / "scripts/run_rtl_validation.py")
rtl = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rtl
SPEC.loader.exec_module(rtl)


class ReferenceContractTests(unittest.TestCase):
    def test_parameters_require_integers_in_the_supported_range(self):
        for ports, depth in ((1, 1), (4, 0), (4, -1), (4.0, 1), (4, 1.0), (4, True)):
            with self.subTest(ports=ports, depth=depth), self.assertRaises(ValueError):
                rtl.ReferenceSwitch(ports, depth)

    def test_depth_one_full_replacement_and_no_empty_bypass(self):
        model = rtl.ReferenceSwitch(2, 1)
        old = rtl.Word(0, 0, 65535, 1, 7)
        new = rtl.Word(0, 1, 0, 1, 9)
        ready, outputs = model.step(False, [old, None], 3)
        self.assertEqual(ready, 3)
        self.assertEqual(outputs, [None, None])
        ready, outputs = model.step(False, [new, None], 3)
        self.assertEqual(ready, 3)
        self.assertEqual(outputs, [old, None])
        _, outputs = model.step(False, [None, None], 3)
        self.assertEqual(outputs, [None, new])
        self.assertEqual(len(model.delivered), 2)
        self.assertEqual(model.full_replacements, 1)

    def test_stalled_owner_and_fair_service(self):
        model = rtl.ReferenceSwitch(4, 2)
        words = [rtl.Word(source, 0, 0, 1, source) for source in range(4)]
        model.step(False, words, 0)
        _, before = model.step(False, [None] * 4, 0)
        _, after = model.step(False, [None] * 4, 0)
        self.assertEqual(before, after)
        self.assertEqual(before[0], words[0])
        sources = []
        for _ in range(4):
            _, outputs = model.step(False, [None] * 4, 1)
            sources.append(outputs[0].source)
        self.assertEqual(sources, [0, 1, 2, 3])

    def test_new_contender_does_not_displace_a_stalled_owner(self):
        model = rtl.ReferenceSwitch(4, 1)
        incumbent = rtl.Word(3, 0, 1, 1, 11)
        contender = rtl.Word(0, 0, 2, 1, 22)
        model.step(False, [None, None, None, incumbent], 0)
        _, output = model.step(False, [contender, None, None, None], 0)
        self.assertEqual(output[0], incumbent)
        _, output = model.step(False, [None] * 4, 0)
        self.assertEqual(output[0], incumbent)
        _, output = model.step(False, [None] * 4, 1)
        self.assertEqual(output[0], incumbent)
        _, output = model.step(False, [None] * 4, 1)
        self.assertEqual(output[0], contender)

    def test_reset_discards_old_generation_and_invalid_destinations(self):
        model = rtl.ReferenceSwitch(3, 3)
        old = rtl.Word(0, 0, 0, 0, 100)
        new = rtl.Word(0, 0, 0, 1, 100)
        model.step(False, [old, None, None], 0)
        ready, outputs = model.step(True, [None] * 3, 7)
        self.assertEqual((ready, outputs), (0, [None] * 3))
        invalid = rtl.Word(0, 3, 1, 1, 300)
        ready, outputs = model.step(False, [invalid, None, None], 7)
        self.assertEqual(ready & 1, 0)
        self.assertFalse(any(outputs))
        model.step(False, [new, None, None], 7)
        _, outputs = model.step(False, [None] * 3, 7)
        self.assertEqual(outputs[0], new)
        self.assertEqual(model.flushed, {old})
        self.assertEqual(model.delivered, {new})

    def test_trace_generation_is_deterministic_and_covers_conservation(self):
        first, counts = rtl.generate_fabric_trace(3, 3)
        second, repeated = rtl.generate_fabric_trace(3, 3)
        self.assertEqual((first, counts), (second, repeated))
        self.assertGreater(counts["delivered"], 1000)
        self.assertEqual(counts["reset_flushed"], 9)
        self.assertEqual(counts["invalid_destination_rejections"], 8)
        self.assertEqual(counts["accepted"], counts["delivered"] + counts["reset_flushed"])

    def test_empty_or_malformed_saved_reports_cannot_pass(self):
        for report in ({}, [], {"schema_version": True, "status": "passed",
                                "prototype": "board_agnostic_word_fabric"}):
            with self.subTest(report=report), self.assertRaises(ValueError):
                rtl.check_report(report)


@unittest.skipUnless(all(shutil.which(name) for name in ("iverilog", "vvp", "yosys")),
                     "actual RTL regression requires iverilog, vvp and yosys; dedicated CI installs them")
class ActualRTLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = rtl.run_validation()

    def test_actual_icarus_and_yosys_contract(self):
        rtl.check_report(self.report)
        self.assertEqual(self.report["status"], "passed")
        self.assertEqual(len(self.report["fifo_simulations"]), 3)
        self.assertEqual(len(self.report["simulations"]), 4)
        for case in self.report["simulations"]:
            self.assertGreaterEqual(case["hotspot_delivered"], 1000)
            self.assertLess(case["max_fairness_bypasses"], case["ports"])
        self.assertGreater(self.report["synthesis"]["sequential_cell_count"], 0)
        self.assertEqual(len(self.report["parameter_rejections"]), len(rtl.PARAMETER_REJECTIONS))

    def test_check_rejects_stale_sources_and_missing_evidence(self):
        stale = deepcopy(self.report)
        stale["source_hashes"]["rtl/sync_fifo.sv"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "stale"):
            rtl.check_report(stale)
        incomplete = deepcopy(self.report)
        incomplete["simulations"][0]["hotspot_delivered"] = 0
        with self.assertRaisesRegex(ValueError, "coverage"):
            rtl.check_report(incomplete)

    def test_check_rejects_inconsistent_tool_and_synthesis_evidence(self):
        for field in ("empty_synthesis", "wrong_sequential_total", "empty_tool_version", "missing_rejections"):
            report = deepcopy(self.report)
            if field == "empty_synthesis":
                report["synthesis"]["cells_by_type"] = {}
                report["synthesis"]["cell_count"] = 0
            elif field == "wrong_sequential_total":
                report["synthesis"]["sequential_cell_count"] += 1
            elif field == "empty_tool_version":
                report["tool_versions"]["yosys"] = ""
            else:
                report["parameter_rejections"] = []
            with self.subTest(field=field), self.assertRaises(ValueError):
                rtl.check_report(report)

    def test_check_rejects_changed_trace_and_invented_coverage_counts(self):
        for field in ("trace_sha256", "stall_checks", "cycles"):
            report = deepcopy(self.report)
            if field == "trace_sha256":
                report["simulations"][0][field] = "0" * 64
            else:
                report["simulations"][0][field] += 1
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "trace|reference"):
                rtl.check_report(report)


if __name__ == "__main__":
    unittest.main()
