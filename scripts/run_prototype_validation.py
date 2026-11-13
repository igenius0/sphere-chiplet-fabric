"""Build the v1.2.0 prototype evidence bundle; --check verifies saved hashes.

This is a software/RTL experiment runner. It cannot produce physical hardware
measurements, an FPGA bitstream, device timing closure or a compliance result.
Importing this module does not run experiments or write files.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.0"
PUBLICATION_DATE = "2026-11-10"
MANIFEST = Path("artifacts/prototype_manifest.json")
OUTPUTS = frozenset(("artifacts/prototype_validation.json",
                     "artifacts/rtl_validation.json", "paper/prototype_results.tex"))


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sources():
    paths = set((ROOT / "sim").glob("*.py"))
    paths.update((ROOT / "tests").glob("*.py"))
    paths.update(path for path in (ROOT / "rtl").rglob("*")
                 if path.is_file() and path.suffix in (".v", ".sv", ".json", ".ys"))
    paths.update(path for path in (ROOT / "examples" / "measurements").rglob("*")
                 if path.is_file() and path.suffix in (".csv", ".json"))
    paths.update((Path(__file__).resolve(), ROOT / "scripts/run_rtl_validation.py",
                  ROOT / "examples/moe_inference.json", ROOT / "requirements.txt"))
    return {path.relative_to(ROOT).as_posix(): _hash(path) for path in sorted(paths)}


def _read(path):
    def reject(value):
        raise ValueError("non-finite JSON number: " + value)
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _network_experiments():
    from sim.finite_buffer import NetworkConfig, Packet, simulate_network
    topology = {"nodes": [0, 1, 2, 3],
                "adj": {0: [1, 2, 3], 1: [0], 2: [0], 3: [0]}}
    packets = [Packet(index, index % 4, (index + 1) % 4,
                      2 + index % 5, index // 4, index % 2) for index in range(24)]
    baseline = simulate_network(topology, packets, NetworkConfig(buffer_flits_per_vc=2))
    paused = simulate_network(topology, packets, NetworkConfig(
        buffer_flits_per_vc=2, paused_sinks=((1, 0, 40),)))
    cutoff = simulate_network(topology, [Packet(0, 0, 1, 12)], NetworkConfig(
        buffer_flits_per_vc=1, max_cycles=12, paused_sinks=((1, 0, None),)))
    partitioned = simulate_network(
        {"nodes": [0, 1, 2, 3], "adj": {0: [1], 1: [0], 2: [3], 3: [2]}},
        [Packet(0, 0, 3, 3)], NetworkConfig())
    for label, report in (("baseline", baseline), ("paused_sink", paused)):
        if (report["status"] != "completed" or not report["all_requested_delivered"]
                or not report["idle"] or not report["routing_certificate"]["acyclic"]
                or not report["accounting"]["conservation_ok"]):
            raise ValueError("finite-buffer experiment failed: " + label)
        if any(credit["available"] != credit["capacity"] for credit in report["credits"]):
            raise ValueError("network stopped before all credits returned")
    if paused["elapsed_cycles"] <= baseline["elapsed_cycles"]:
        raise ValueError("sink-pause experiment did not exercise its expected backpressure")
    if cutoff["status"] != "max_cycles" or not cutoff["pending_packets"]:
        raise ValueError("bounded cutoff must report incomplete packets")
    if not cutoff["accounting"]["conservation_ok"]:
        raise ValueError("cutoff lost flits")
    if len(partitioned["rejected_packets"]) != 1 or partitioned["all_requested_delivered"]:
        raise ValueError("partition must reject the unreachable request explicitly")
    return {"topology": topology, "baseline": baseline, "paused_sink": paused,
            "bounded_cutoff": cutoff, "partition": partitioned}


def _validate_transport(report):
    scenarios = report["scenarios"]
    for name in ("healthy", "recover_drop_corrupt_duplicate_reorder"):
        case = scenarios[name]
        if (case["completed"] != 1 or case["pending"] or case["reserved_bytes"] or case["queued_events"]
                or case["sender_verified_numeric_result"] != case["reference_numeric_sum"]):
            raise ValueError("transport numeric recovery failed: " + name)
    for name in ("partition", "all_acks_lost"):
        case = scenarios[name]
        if case["aborted"] != 1 or case["pending"] or case["reserved_bytes"] or case["queued_events"]:
            raise ValueError("transport failure did not terminate and release resources: " + name)
        if case["sender_verified_numeric_result"] is not None:
            raise ValueError("aborted transport falsely reported a verified output")
    lost = scenarios["all_acks_lost"]["requests"][0]
    if not lost["uncertain_delivery"]:
        raise ValueError("ACK exhaustion must preserve the uncertain sender outcome")
    barrier = scenarios["generation_barrier"]
    if (barrier["aborted"] != 1 or barrier["completed"] != 1
            or barrier["pending"] or barrier["reserved_bytes"] or barrier["queued_events"]):
        raise ValueError("generation barrier did not abort old and complete new work")


def _latex(report):
    network = report["finite_buffer"]["baseline"]
    inference, rtl = report["inference"], report["rtl"]
    error = inference["numerical_validation"]["maximum_absolute_logit_error"]
    metrics = {
        "PrototypeVersion": VERSION,
        "PrototypeDate": PUBLICATION_DATE,
        "PrototypePackets": network["accounting"]["delivered_packets"],
        "PrototypeNetworkCycles": network["elapsed_cycles"],
        "PrototypeTokens": len(inference["execution"]["processed_tokens"]),
        "PrototypeContributions": inference["execution"]["contributions_completed"],
        "PrototypeLogitError": f"{error:.3g}",
        "PrototypeRtlConfigurations": len(rtl["simulations"]),
        "PrototypeRtlTransfers": sum(case["delivered"] for case in rtl["simulations"]),
        "PrototypeRtlCells": rtl["synthesis"]["cell_count"],
    }
    lines = ["% Generated by scripts/run_prototype_validation.py; do not edit.",
             "% Actual CPU numerics and RTL simulation are not physical measurements."]
    lines.extend("\\newcommand{\\" + key + "}{" + str(value) + "}" for key, value in metrics.items())
    lines.extend([r"\newcommand{\PrototypeStatusRows}{",
                  r"Finite-buffer model & Credit conservation, fixed-route DAG and bounded cutoff verified \\",
                  r"Tiny inference & Independent CPU numerical reference and contribution accounting pass \\",
                  r"Transport reference & Recovery, duplicate suppression, abort and generation cases pass \\",
                  r"RTL prototype & Simulation and generic synthesis pass; no FPGA board result \\",
                  r"Physical validation & Harness implemented; no hardware measurements supplied \\", "}"])
    return "\n".join(lines) + "\n"


def build_prototypes(output_root=ROOT):
    before = _sources()
    original_path = list(sys.path)
    sys.path.insert(0, str(ROOT))
    try:
        import numpy as np
        from sim.moe_inference import run_inference_demo, run_inference_fault_demo
        from sim.physical_validation import assess_measurements
        from sim.transport import demonstrate_transport
        network = _network_experiments()
        inference = run_inference_demo(_read(ROOT / "examples/moe_inference.json"))
        if (not inference["numerical_validation"]["all_logits_close"]
                or not inference["numerical_validation"]["greedy_tokens_match"]
                or inference["execution"]["contributions_expected"]
                != inference["execution"]["contributions_completed"]):
            raise ValueError("numerical inference validation failed")
        inference_faults = run_inference_fault_demo()
        numeric = inference_faults["numerical_validation"]
        uncertain = inference_faults["all_acks_lost"]
        if (not numeric["all_logits_close"]
                or not numeric["remote_contributions_executed_once"]
                or not numeric["weighted_contributions_combined_once"]
                or not uncertain["aborted_without_cache_commit"]
                or uncertain["logits_accepted"] or uncertain["cache_length"]
                or uncertain["combined_contributions"]):
            raise ValueError("actual expert-output fault recovery or abort validation failed")
        recovered, aborted = inference_faults["recovery"], uncertain["transport"]
        if (recovered["completed"] != 3 or recovered["aborted"] or recovered["pending"]
                or recovered["reserved_bytes"] or recovered["queued_events"]
                or recovered["stats"]["receiver_executions"] != 3
                or any(request["sender_outcome"] != "acknowledged"
                       or not request["reservation_released"] for request in recovered["requests"])
                or aborted["submitted"] != 1 or aborted["aborted"] != 1 or aborted["completed"]
                or aborted["pending"] or aborted["reserved_bytes"] or aborted["queued_events"]
                or aborted["stats"]["receiver_executions"] != 1
                or not aborted["requests"][0]["uncertain_delivery"]):
            raise ValueError("expert-output transport did not terminate with exact accounting")
        transport = demonstrate_transport()
        _validate_transport(transport)
        physical = assess_measurements(ROOT / "examples/measurements/missing_hardware.json")
        if physical["status"] != "INSUFFICIENT_EVIDENCE":
            raise ValueError("missing hardware must not be treated as physically validated")
        with tempfile.TemporaryDirectory(prefix="sphere-prototype-") as temporary:
            staging = Path(temporary)
            rtl_path = staging / "artifacts/rtl_validation.json"
            rtl_path.parent.mkdir(parents=True)
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_rtl_validation.py"), "--output", str(rtl_path)],
                cwd=ROOT, capture_output=True, text=True, timeout=240)
            if completed.returncode:
                raise ValueError("RTL validation failed:\n" + completed.stdout[-6000:] + completed.stderr[-6000:])
            rtl = _read(rtl_path)
            if rtl["status"] != "passed":
                raise ValueError("RTL runner did not report successful simulation and synthesis")
            report = {
                "schema_version": 1, "publication_version": VERSION,
                "date": PUBLICATION_DATE, "status": "software_and_rtl_checks_passed",
                "physical_validation_complete": False, "fpga_bringup_complete": False,
                "finite_buffer": network, "inference": inference,
                "inference_fault_recovery": inference_faults, "transport": transport,
                "rtl": rtl, "physical_measurement_assessment": physical,
                "remaining_evidence": ["FPGA board integration and timing constraints",
                    "Real PHY/BER, voltage, power and thermal measurements",
                    "Calibrated compute/HBM and full workload performance",
                    "Adaptive routing, wormhole VC transitions and formal RTL proofs",
                    "Packaging, manufacturing and silicon signoff"],
                "scope": "Actual small CPU numerical execution, logical network/protocol simulations and RTL simulation/generic synthesis. No silicon, FPGA-board, power, temperature or compliance measurement is claimed.",
            }
            _write(staging / "artifacts/prototype_validation.json", report)
            (staging / "paper").mkdir()
            (staging / "paper/prototype_results.tex").write_text(_latex(report), encoding="utf-8")
            after = _sources()
            if before != after:
                raise ValueError("prototype source changed during validation; retry after edits finish")
            manifest = {
                "schema_version": 1, "publication_version": VERSION, "date": PUBLICATION_DATE,
                "environment": {"python_version": platform.python_version(), "numpy_version": np.__version__},
                "source_sha256": after,
                "output_sha256": {name: _hash(staging / name) for name in sorted(OUTPUTS)},
                "rebuild_command": "python scripts/run_prototype_validation.py",
                "scope": "Generated prototype reports and paper macros, exact model/RTL/config/test source bytes and tool versions. Unit-test sources are identified, not represented as executed by this runner; CI executes the suite separately. No physical evidence is synthesized.",
            }
            _write(staging / MANIFEST, manifest)
            for relative in sorted(OUTPUTS | {MANIFEST.as_posix()}):
                target = Path(output_root) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staging / relative, target)
            return manifest
    finally:
        sys.path[:] = original_path


def check_prototypes(output_root=ROOT):
    root = Path(output_root).resolve()
    manifest = _read(root / MANIFEST)
    if (manifest.get("schema_version") != 1 or manifest.get("publication_version") != VERSION
            or manifest.get("date") != PUBLICATION_DATE):
        raise ValueError("unsupported or stale prototype publication manifest")
    if manifest.get("source_sha256") != _sources():
        raise ValueError("prototype source hashes changed; rebuild the evidence")
    if set(manifest.get("output_sha256", {})) != OUTPUTS:
        raise ValueError("prototype manifest must list every expected output")
    for relative, expected in manifest["output_sha256"].items():
        if _hash(root / relative) != expected:
            raise ValueError("stale prototype output: " + relative)
    report = _read(root / "artifacts/prototype_validation.json")
    if (report.get("publication_version") != VERSION
            or report.get("status") != "software_and_rtl_checks_passed"
            or report.get("physical_validation_complete") is not False
            or report.get("fpga_bringup_complete") is not False
            or report["physical_measurement_assessment"]["status"] != "INSUFFICIENT_EVIDENCE"):
        raise ValueError("prototype evidence scope does not match this experiment")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = check_prototypes(args.output_root) if args.check else build_prototypes(args.output_root)
    except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    print(("Verified" if args.check else "Built") + " prototype evidence v" + manifest["publication_version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
