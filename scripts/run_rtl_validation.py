#!/usr/bin/env python3
"""Run real Icarus simulation and generic Yosys synthesis of the word switch.

The Python oracle uses deques and abstract arbitration state, not the RTL's
pointer/memory implementation. It supplies cycle-by-cycle expected transfers to
a self-checking SystemVerilog testbench. --check checks saved provenance and
coverage only; it does not substitute for executing the external tools.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    "rtl/sync_fifo.sv",
    "rtl/word_fabric_top.sv",
    "rtl/tb_sync_fifo.sv",
    "rtl/tb_word_fabric.sv",
    "scripts/run_rtl_validation.py",
    "tests/test_rtl_contract.py",
    "docs/RTL_PROTOTYPE.md",
)
FABRIC_CASES = ((4, 1), (4, 3), (4, 4), (3, 3))
FIFO_DEPTHS = (1, 3, 4)
SYNTHESIS_PARAMETERS = {"PORTS": 4, "FIFO_DEPTH": 4, "PAYLOAD_WIDTH": 32,
                        "SEQUENCE_WIDTH": 16, "GENERATION_WIDTH": 8}
PARAMETER_REJECTIONS = (
    ("sync_fifo", "WIDTH", 0),
    ("sync_fifo", "DEPTH", 0),
    ("sync_fifo", "DEPTH", -1),
    ("sync_fifo", "PTR_WIDTH", 1),
    ("sync_fifo", "COUNT_WIDTH", 1),
    ("word_fabric_top", "PORTS", 1),
    ("word_fabric_top", "FIFO_DEPTH", 0),
    ("word_fabric_top", "PAYLOAD_WIDTH", 0),
    ("word_fabric_top", "SEQUENCE_WIDTH", 0),
    ("word_fabric_top", "GENERATION_WIDTH", 0),
    ("word_fabric_top", "PORT_WIDTH", 1),
)
REQUIRED_CHECKS = (
    "independent_cycle_reference",
    "exact_once_identity_and_order",
    "reset_flush_and_generation",
    "backpressure_stable_output",
    "round_robin_no_starvation",
    "fifo_full_simultaneous_push_pop",
    "non_power_of_two_wraparound",
    "invalid_destination_rejected",
    "invalid_parameters_rejected",
    "yosys_no_latches_multidrivers_or_combinational_loops",
)


def source_hashes(root: Path = ROOT) -> dict[str, str]:
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_PATHS}


@dataclass(frozen=True)
class Word:
    source: int
    destination: int
    sequence: int
    generation: int
    payload: int

    def fields(self) -> tuple[int, ...]:
        return self.source, self.destination, self.sequence, self.generation, self.payload


class ReferenceSwitch:
    """Unbounded Python deques constrained to the explicit hardware depth."""

    def __init__(self, ports: int, depth: int):
        if type(ports) is not int or type(depth) is not int or ports < 2 or depth < 1:
            raise ValueError("validation model requires ports >= 2 and depth >= 1")
        self.ports, self.depth = ports, depth
        self.queues: list[deque[Word]] = [deque() for _ in range(ports)]
        self.next_source = [0] * ports
        self.owner: list[int | None] = [None] * ports
        self.accepted: set[Word] = set()
        self.delivered: set[Word] = set()
        self.flushed: set[Word] = set()
        self.expected_delivery: list[deque[Word]] = [deque() for _ in range(ports)]
        self.full_replacements = 0
        self.invalid_rejections = 0
        self.cycles = 0

    def step(self, reset: bool, inputs: list[Word | None], output_ready: int) -> tuple[int, list[Word | None]]:
        if len(inputs) != self.ports:
            raise ValueError("one input slot per port is required")
        self.cycles += 1
        if reset:
            self.flushed.update(word for queue in self.queues for word in queue)
            self.queues = [deque() for _ in range(self.ports)]
            self.expected_delivery = [deque() for _ in range(self.ports)]
            self.next_source = [0] * self.ports
            self.owner = [None] * self.ports
            return 0, [None] * self.ports

        # An output only sees words that were stored before this clock edge.
        outputs: list[Word | None] = [None] * self.ports
        selected: list[int | None] = [None] * self.ports
        for destination in range(self.ports):
            owner = self.owner[destination]
            if owner is None:
                eligible = {source for source, queue in enumerate(self.queues)
                            if queue and queue[0].destination == destination}
                priority = [(self.next_source[destination] + offset) % self.ports
                            for offset in range(self.ports)]
                owner = next((source for source in priority if source in eligible), None)
            if owner is not None:
                assert self.queues[owner] and self.queues[owner][0].destination == destination
                selected[destination] = owner
                outputs[destination] = self.queues[owner][0]

        removed = {source for destination, source in enumerate(selected)
                   if source is not None and output_ready & (1 << destination)}
        ready = 0
        for source, incoming in enumerate(inputs):
            # Even an inactive source presents destination zero at the interface.
            legal = incoming is None or 0 <= incoming.destination < self.ports
            if legal and (len(self.queues[source]) < self.depth or source in removed):
                ready |= 1 << source
            if incoming is not None and not legal:
                self.invalid_rejections += 1

        for destination, source in enumerate(selected):
            if source is None:
                continue
            if output_ready & (1 << destination):
                word = self.queues[source].popleft()
                assert word in self.accepted and word not in self.delivered and word not in self.flushed
                assert word == self.expected_delivery[source].popleft(), "per-source order violated"
                self.delivered.add(word)
                self.next_source[destination] = (source + 1) % self.ports
                self.owner[destination] = None
            else:
                self.owner[destination] = source

        for source, incoming in enumerate(inputs):
            if incoming is not None and ready & (1 << source):
                assert incoming.source == source
                assert incoming not in self.accepted, "duplicate accepted identity"
                if source in removed and len(self.queues[source]) == self.depth - 1:
                    self.full_replacements += 1
                self.queues[source].append(incoming)
                self.expected_delivery[source].append(incoming)
                self.accepted.add(incoming)
        resident = {word for queue in self.queues for word in queue}
        assert self.accepted == self.delivered | self.flushed | resident
        assert not (self.delivered & self.flushed or self.delivered & resident or self.flushed & resident)
        assert all(len(queue) <= self.depth for queue in self.queues)
        return ready, outputs


class XorShift32:
    def __init__(self, seed: int):
        self.state = seed & 0xFFFFFFFF

    def next(self) -> int:
        value = self.state
        value ^= (value << 13) & 0xFFFFFFFF
        value ^= value >> 17
        value ^= (value << 5) & 0xFFFFFFFF
        self.state = value & 0xFFFFFFFF
        return self.state


def generate_fabric_trace(ports: int, depth: int) -> tuple[str, dict[str, int]]:
    model = ReferenceSwitch(ports, depth)
    rng = XorShift32(0xC0FFEE + ports * 101 + depth)
    pending: list[Word | None] = [None] * ports
    # Cross the 16-bit metadata boundary; ordering is acceptance order, not a
    # numeric comparison that would incorrectly reject wrapped sequence values.
    sequence = [0xFFFC] * ports
    lines: list[str] = []
    all_ready = (1 << ports) - 1
    previous_stalled = 0
    fairness_age = [0] * ports
    coverage = {"stall_checks": 0, "hotspot_delivered": 0, "max_fairness_bypasses": 0}

    def emit(*, reset: bool = False, phase: int = 0, ready: int = 0,
             offer: bool = True, random_destinations: bool = False,
             generation: int = 1, invalid: bool = False, offer_sources: int | None = None) -> None:
        nonlocal previous_stalled
        if reset:
            pending[:] = [None] * ports
        elif invalid:
            assert all(word is None for word in pending)
            pending[0] = Word(0, ports, sequence[0], generation, rng.next())
        elif offer:
            for source in range(ports):
                if (pending[source] is None and (offer_sources is None or offer_sources & (1 << source)) and
                        (not random_destinations or rng.next() % 100 < 87)):
                    destination = rng.next() % ports if random_destinations else 0
                    pending[source] = Word(source, destination, sequence[source], generation, rng.next())
        inputs = pending.copy()
        input_valid = sum(1 << source for source, word in enumerate(inputs) if word is not None)
        input_ready, outputs = model.step(reset, inputs, ready)
        output_valid = sum(1 << destination for destination, word in enumerate(outputs) if word is not None)
        if reset:
            previous_stalled = 0
            fairness_age[:] = [0] * ports
        else:
            coverage["stall_checks"] += previous_stalled.bit_count()
            if phase == 2 and outputs[0] is not None and ready & 1:
                coverage["hotspot_delivered"] += 1
                for source in range(ports):
                    fairness_age[source] = 0 if outputs[0].source == source else fairness_age[source] + 1
                coverage["max_fairness_bypasses"] = max(coverage["max_fairness_bypasses"], *fairness_age)
            elif phase != 2:
                fairness_age[:] = [0] * ports
            previous_stalled = output_valid & ~ready
        fields = [int(reset), phase, input_valid, ready, input_ready, output_valid]
        for word in inputs:
            fields.extend((word.destination, word.sequence, word.generation, word.payload) if word else (0, 0, 0, 0))
        for word in outputs:
            fields.extend(word.fields() if word else (0, 0, 0, 0, 0))
        lines.append(" ".join(f"{field:x}" for field in fields))
        if not reset:
            for source, word in enumerate(inputs):
                if word is not None and input_ready & (1 << source):
                    pending[source] = None
                    sequence[source] = (sequence[source] + 1) & 0xFFFF
        if invalid:
            pending[0] = None

    for _ in range(2):
        emit(reset=True)
    # Reset must flush both full queues and a locked, stalled output word.
    for _ in range(12 + depth):
        emit(generation=0)
    for _ in range(2):
        emit(reset=True)
    # A later, higher-priority contender must not displace a stalled winner.
    emit(offer_sources=1 << (ports - 1))
    emit(offer_sources=1)
    for _ in range(2):
        emit(offer=False)
    for _ in range(3):
        emit(ready=all_ready, offer=False)
    for _ in range(depth + 2):
        emit(phase=1)
    # 1,280 accepted hotspot transfers, including periodic output backpressure.
    for cycle in range(1600):
        emit(phase=2, ready=all_ready if cycle % 5 != 1 else all_ready & ~1)
    for _ in range(37):
        emit(ready=0)
    for _ in range(1800):
        ready = sum((int(rng.next() % 100 < 71) << port) for port in range(ports))
        emit(phase=3, ready=ready, random_destinations=True)
    # Keep any already offered input stable until accepted, then drain all FIFOs.
    for _ in range(ports * (depth + 2) + 16):
        emit(phase=4, ready=all_ready, offer=False)
    assert not any(pending) and not any(model.queues), "trace failed to drain"
    if ports & (ports - 1):
        for _ in range(8):
            emit(phase=5, ready=all_ready, offer=False, invalid=True)
    assert len(model.accepted) == len(model.delivered) + len(model.flushed)
    text = "\n".join(lines) + "\n"
    return text, {
        "cycles": model.cycles,
        "accepted": len(model.accepted),
        "delivered": len(model.delivered),
        "reset_flushed": len(model.flushed),
        "full_replacements": model.full_replacements,
        "invalid_destination_rejections": model.invalid_rejections,
        **coverage,
    }


def run_command(command: list[str], *, cwd: Path = ROOT, timeout: int = 120) -> str:
    process = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    output = process.stdout + process.stderr
    if process.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {process.returncode}):\n{output[-12000:]}")
    return output


def summary_fields(output: str, marker: str) -> dict[str, int]:
    line = next((line for line in output.splitlines() if line.startswith(marker + " ")), None)
    if line is None:
        raise RuntimeError(f"simulation did not print {marker}:\n{output[-4000:]}")
    return {name: int(value) for name, value in re.findall(r"([a-z_]+)=(\d+)", line)}


def require_tools() -> dict[str, str]:
    missing = [name for name in ("iverilog", "vvp", "yosys") if shutil.which(name) is None]
    if missing:
        raise RuntimeError("RTL validation requires external tools: " + ", ".join(missing) +
                           ". Install iverilog and yosys, then rerun; no RTL result was produced.")
    versions = {}
    for name, args in (("iverilog", ["-V"]), ("vvp", ["-V"]), ("yosys", ["-V"])):
        output = run_command([name, *args])
        versions[name] = next(line.strip() for line in output.splitlines() if line.strip())
    return versions


def check_invalid_parameters(work: Path) -> list[dict]:
    evidence = []
    for index, (module, parameter, value) in enumerate(PARAMETER_REJECTIONS):
        executable = work / f"invalid_{index}.vvp"
        compile_process = subprocess.run(
            ["iverilog", "-g2012", "-s", module, "-P", f"{module}.{parameter}={value}",
             "-o", str(executable), "rtl/sync_fifo.sv", "rtl/word_fabric_top.sv"],
            cwd=ROOT, text=True, capture_output=True, timeout=30)
        if compile_process.returncode:
            # Zero-width indexed slices can be rejected during elaboration,
            # before the explicit startup guard can execute.
            if parameter not in compile_process.stdout + compile_process.stderr:
                raise RuntimeError(f"invalid {module}.{parameter}={value} failed without a parameter diagnostic")
        else:
            process = subprocess.run(["vvp", str(executable)], cwd=ROOT, text=True,
                                     capture_output=True, timeout=30)
            if process.returncode == 0 or f"{module} parameter contract violated" not in process.stdout + process.stderr:
                raise RuntimeError(f"invalid {module}.{parameter}={value} did not trigger its parameter guard")
        evidence.append({"module": module, "parameter": parameter, "value": value, "status": "rejected"})
    return evidence


def run_validation() -> dict:
    versions = require_tools()
    fifo_results = []
    simulations = []
    with tempfile.TemporaryDirectory(prefix="word-fabric-rtl-") as temporary:
        work = Path(temporary)
        parameter_rejections = check_invalid_parameters(work)
        for depth in FIFO_DEPTHS:
            executable = work / f"fifo_{depth}.vvp"
            run_command(["iverilog", "-g2012", "-s", "tb_sync_fifo", "-P", f"tb_sync_fifo.DEPTH={depth}",
                         "-o", str(executable), "rtl/sync_fifo.sv", "rtl/tb_sync_fifo.sv"])
            result = summary_fields(run_command(["vvp", str(executable)]), "FIFO_PASS")
            assert result["depth"] == depth and result["simultaneous_full"] > 0 and result["resets_with_data"] > 0
            fifo_results.append(result)
        for ports, depth in FABRIC_CASES:
            trace, expected = generate_fabric_trace(ports, depth)
            stimulus = work / f"fabric_{ports}_{depth}.txt"
            stimulus.write_text(trace)
            executable = work / f"fabric_{ports}_{depth}.vvp"
            run_command(["iverilog", "-g2012", "-s", "tb_word_fabric", "-P", f"tb_word_fabric.PORTS={ports}",
                         "-P", f"tb_word_fabric.FIFO_DEPTH={depth}", "-o", str(executable),
                         "rtl/sync_fifo.sv", "rtl/word_fabric_top.sv", "rtl/tb_word_fabric.sv"])
            actual = summary_fields(run_command(["vvp", str(executable), f"+STIMULUS={stimulus}"]), "FABRIC_PASS")
            for key in ("cycles", "accepted", "delivered", "reset_flushed", "stall_checks", "hotspot_delivered"):
                if actual[key] != expected[key]:
                    raise RuntimeError(f"SV/Python conservation mismatch for {ports}/{depth}: {key}")
            if actual["maximum_fairness"] != expected["max_fairness_bypasses"] or actual["maximum_fairness"] > ports - 1:
                raise RuntimeError("round-robin service bound violated")
            simulations.append({"ports": ports, "fifo_depth": depth, **expected,
                                "trace_sha256": hashlib.sha256(trace.encode()).hexdigest()})

        netlist = work / "generic_netlist.json"
        # Flatten makes resource counts complete rather than omitting FIFO modules.
        script = ("read_verilog -sv rtl/sync_fifo.sv rtl/word_fabric_top.sv; "
                  "hierarchy -check -top word_fabric_top; "
                  "synth -top word_fabric_top -flatten; check -assert; "
                  f"write_json {netlist}")
        synthesis_log = run_command(["yosys", "-Q", "-T", "-p", script])
        warnings = [line.strip() for line in synthesis_log.splitlines() if line.startswith("Warning:")]
        prohibited = [line for line in warnings if re.search(r"latch|multiple.*driver|conflicting.*driver|logic loop", line, re.I)]
        if prohibited:
            raise RuntimeError("prohibited synthesis warnings: " + "; ".join(prohibited))
        module = json.loads(netlist.read_text())["modules"]["word_fabric_top"]
        counts = Counter(cell["type"] for cell in module["cells"].values())
        if any("LATCH" in name.upper() for name in counts):
            raise RuntimeError("synthesis inferred a latch")
        bits = {bit for wire in module["netnames"].values() for bit in wire["bits"] if isinstance(bit, int)}
        sequential = sum(number for name, number in counts.items() if re.search(r"DFF|SDFF", name, re.I))
        if sequential <= 0:
            raise RuntimeError("synthesis produced no sequential state")
        synthesis = {"top": "word_fabric_top", "parameters": dict(SYNTHESIS_PARAMETERS),
                     "wire_bits": len(bits), "cell_count": sum(counts.values()),
                     "sequential_cell_count": sequential,
                     "memory_bits": sum(memory["width"] * memory["size"] for memory in module.get("memories", {}).values()),
                     "cells_by_type": dict(sorted(counts.items())), "check": "passed",
                     "warning_count": len(warnings)}
    return {"schema_version": 1, "status": "passed", "prototype": "board_agnostic_word_fabric",
            "source_hashes": source_hashes(), "tool_versions": versions,
            "checks": {name: "passed" for name in REQUIRED_CHECKS},
            "parameter_rejections": parameter_rejections,
            "fifo_simulations": fifo_results, "simulations": simulations, "synthesis": synthesis,
            "limitations": ["generic synthesis only; no FPGA board build or bitstream",
                            "no placement, routing, timing closure, power, thermal, PHY or silicon validation",
                            "word-level local switch; no HBM protocol, CDC, flit/credit network or complete accelerator"]}


def check_report(report: dict) -> None:
    def count(record: dict, key: str, minimum: int = 0) -> int:
        value = record.get(key)
        if type(value) is not int or value < minimum:
            raise ValueError(f"RTL report has an invalid {key} count")
        return value

    if (not isinstance(report, dict) or type(report.get("schema_version")) is not int or
            report.get("schema_version") != 1 or report.get("status") != "passed" or
            report.get("prototype") != "board_agnostic_word_fabric"):
        raise ValueError("RTL report is not a passing schema-v1 report")
    if report.get("source_hashes") != source_hashes():
        raise ValueError("RTL report sources are stale; rerun actual RTL validation")
    checks = report.get("checks")
    if not isinstance(checks, dict) or any(checks.get(name) != "passed" for name in REQUIRED_CHECKS):
        raise ValueError("RTL report is missing required checks")
    simulations = report.get("simulations")
    if (not isinstance(simulations, list) or not all(isinstance(case, dict) for case in simulations) or
            [(case.get("ports"), case.get("fifo_depth")) for case in simulations] != list(FABRIC_CASES)):
        raise ValueError("RTL report is missing simulation parameter cases")
    for case in simulations:
        ports, depth = count(case, "ports", 2), count(case, "fifo_depth", 1)
        if (count(case, "hotspot_delivered") < 1000 or count(case, "stall_checks") < 1 or
            count(case, "reset_flushed") < 1 or count(case, "full_replacements") < 1 or
            count(case, "max_fairness_bypasses") >= ports or
            count(case, "accepted") != count(case, "delivered") + case["reset_flushed"]):
            raise ValueError("RTL report fails coverage or conservation requirements")
        trace, expected = generate_fabric_trace(ports, depth)
        if case.get("trace_sha256") != hashlib.sha256(trace.encode()).hexdigest():
            raise ValueError("RTL report trace does not match the current independent reference")
        if any(count(case, key) != value for key, value in expected.items()):
            raise ValueError("RTL report reference counts do not match")
    fifo = report.get("fifo_simulations")
    if (not isinstance(fifo, list) or not all(isinstance(case, dict) for case in fifo) or
            [case.get("depth") for case in fifo] != list(FIFO_DEPTHS)):
        raise ValueError("RTL report is missing FIFO edge-case coverage")
    for case in fifo:
        depth = count(case, "depth", 1)
        pushes, pops = count(case, "pushes", 1), count(case, "pops", 1)
        simultaneous = count(case, "simultaneous_full", 1)
        if (count(case, "cycles") != 2048 + 4 * depth or count(case, "resets_with_data") != 1 or
                pushes - pops != depth or pushes >= case["cycles"] or simultaneous > min(pushes, pops)):
            raise ValueError("RTL report has inconsistent FIFO coverage or conservation")
    rejected = report.get("parameter_rejections")
    expected_rejections = [{"module": module, "parameter": parameter, "value": value, "status": "rejected"}
                           for module, parameter, value in PARAMETER_REJECTIONS]
    if (rejected != expected_rejections or
            any(not isinstance(case, dict) or type(case.get("value")) is not int for case in rejected)):
        raise ValueError("RTL report is missing invalid-parameter rejection evidence")
    synthesis = report.get("synthesis")
    if (not isinstance(synthesis, dict) or synthesis.get("check") != "passed" or
            synthesis.get("top") != "word_fabric_top" or synthesis.get("parameters") != SYNTHESIS_PARAMETERS):
        raise ValueError("RTL report has no passing synthesis evidence")
    cells = synthesis.get("cells_by_type")
    if (not isinstance(cells, dict) or not cells or
            any(not isinstance(name, str) or not name or "LATCH" in name.upper() or
                type(number) is not int or number <= 0 for name, number in cells.items())):
        raise ValueError("RTL synthesis cell counts are missing or invalid")
    sequential = sum(number for name, number in cells.items() if re.search(r"DFF|SDFF", name, re.I))
    if (count(synthesis, "cell_count", 1) != sum(cells.values()) or
            count(synthesis, "sequential_cell_count", 1) != sequential or
            count(synthesis, "wire_bits", 1) < sequential or count(synthesis, "memory_bits") != 0):
        raise ValueError("RTL synthesis counts are inconsistent")
    count(synthesis, "warning_count")
    versions = report.get("tool_versions")
    prefixes = {"iverilog": "Icarus Verilog version ", "vvp": "Icarus Verilog runtime version ", "yosys": "Yosys "}
    if (not isinstance(versions, dict) or set(versions) != set(prefixes) or
            any(not isinstance(versions[name], str) or not versions[name].startswith(prefix)
                or not versions[name][len(prefix):].strip() for name, prefix in prefixes.items())):
        raise ValueError("RTL report is missing external tool attribution")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/rtl_validation.json")
    parser.add_argument("--check", action="store_true", help="check saved report provenance/coverage without external tools")
    args = parser.parse_args(argv)
    try:
        if args.check:
            check_report(json.loads(args.output.read_text()))
            print("RTL report provenance and coverage are current (external tools were not rerun).")
        else:
            report = run_validation()
            check_report(report)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
            print(f"RTL validation passed: {len(report['fifo_simulations'])} FIFO simulations, "
                  f"{len(report['simulations'])} switch simulations, generic Yosys synthesis.")
        return 0
    except (OSError, RuntimeError, ValueError, AssertionError, subprocess.TimeoutExpired) as error:
        print(f"RTL validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
