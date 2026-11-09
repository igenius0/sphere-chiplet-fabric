"""Unit conversions and conservative bounds for the analytical reference model."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sim.performance_model import SPHERE_162, compute_aggregate, estimate_roofline


class ReferenceAggregateTests(unittest.TestCase):
    def test_reference_units_and_memory_endpoint_limit(self):
        aggregate = compute_aggregate(SPHERE_162)
        self.assertAlmostEqual(aggregate["fp8_pflops_dense"], 56.5)
        self.assertAlmostEqual(aggregate["hbm_capacity_gb"], 900)
        self.assertAlmostEqual(aggregate["hbm_bw_TBps"], 30)
        # 25 controllers * six links * 256 Gbit/s / eight / 1000.
        self.assertAlmostEqual(aggregate["remote_hbm_fabric_bound_TBps"], 4.8)
        self.assertAlmostEqual(aggregate["compute_fabric_ingress_bound_TBps"], 21.696)
        self.assertAlmostEqual(aggregate["effective_remote_hbm_bw_TBps"], 4.8)
        self.assertLess(aggregate["effective_remote_hbm_bw_TBps"],
                        aggregate["hbm_bw_TBps"])

    def test_component_power_is_not_presented_as_full_system_power(self):
        aggregate = compute_aggregate(SPHERE_162)
        self.assertGreater(aggregate["power_component_subtotal_w"], 0)
        self.assertIsNone(aggregate["full_system_power_w"])
        json.dumps(aggregate, allow_nan=False)


class RooflineTests(unittest.TestCase):
    CHIP = {"compute_tflops_by_precision": {"fp8": 56500},
            "memory_bandwidth_TBps": 4.8}

    def test_memory_bound_uses_terabytes_times_flops_per_byte(self):
        result = estimate_roofline(self.CHIP, {
            "precision": "fp8", "arithmetic_intensity_flops_per_byte": 4})
        self.assertEqual(result["precision"], "fp8")
        self.assertAlmostEqual(result["compute_bound_tflops"], 56500)
        self.assertAlmostEqual(result["memory_bound_tflops"], 19.2)
        self.assertAlmostEqual(result["attainable_tflops_upper_bound"], 19.2)
        self.assertEqual(result["bottleneck"], "memory")

    def test_compute_bound_does_not_exceed_matching_precision_peak(self):
        result = estimate_roofline(self.CHIP, {
            "precision": "fp8", "arithmetic_intensity_flops_per_byte": 1000000})
        self.assertAlmostEqual(result["attainable_tflops_upper_bound"], 56500)
        self.assertEqual(result["bottleneck"], "compute")

    def test_unavailable_precision_does_not_fall_back_to_fp8(self):
        for precision in ("fp16", "fp32", "fp64"):
            with self.subTest(precision=precision), self.assertRaises(ValueError):
                estimate_roofline(self.CHIP, {
                    "precision": precision, "arithmetic_intensity_flops_per_byte": 8})

    def test_invalid_arithmetic_intensity_is_rejected(self):
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                estimate_roofline(self.CHIP, {
                    "precision": "fp8", "arithmetic_intensity_flops_per_byte": value})


class ImportTests(unittest.TestCase):
    def test_import_is_silent_and_creates_no_output_artifacts(self):
        root = Path(__file__).resolve().parents[1]
        code = "import sys; sys.path.insert(0, sys.argv[1]); import sim.performance_model"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-c", code, str(root)],
                                    cwd=directory, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
