# Validation and iteration plan

Publication/evidence revision **v1.2.0, 10 November 2026**; see the [revision and evidence map](PUBLICATION_REVISION.md).
This plan defines how to select a useful accelerator architecture from the
spherical/geodesic concept and its alternatives. The thresholds below are
**proposed engineering decision gates**, not achieved results or a claim that
one topology is universally best. Keep failed experiments and counterexamples.

## Immediate execution packages, 13 November 2026

Use the [four-island plan](FOUR_ISLAND_EXECUTION_PLAN.md) for the next functional
prototype and the [external-cooling protocol](EXTERNAL_COOLING_EXPERIMENT.md)
for the separate heater experiment. Flow, pressure, leak integrity, mechanical
contact and cooling-power comparisons require additional captures and review;
the current physical measurement checker does not assess all these criteria.
These additions define pending work and do not advance any hardware gate.

## What the current repository can establish

The baseline model checks graph connectivity, routes, packet serialization,
directed-link contention and static link-removal sensitivity. Its original
workloads remain communication proxies. Revision v1.2.0 adds a separate
finite-buffer/credit model, tiny numerical MoE decoder, bounded transport
reference, word-switch RTL and measurement harness. Their tested contracts
are listed in the [implementation status](IMPLEMENTATION_STATUS.md).
These do not supply FPGA-board, electrical, full-system power or cooling
measurements or establish product performance and silicon feasibility.

The revised 42-rank example reports, at the same aggregate link-bandwidth
budget, MoE-dispatch p99 latency of approximately 277.21 ns for the sphere and
230.11 ns for the torus. The periodic-grid stencil is approximately 287.17 ns
and 34.03 ns respectively. These are one seeded configuration, not a ranking
of real accelerators. The stencil's fixed rank mapping matches the torus
wiring directly; use it as a locality control. Reproduce the values from the
example configurations before comparing a later revision.

The first architectural constraint is independent of topology selection:

- One assumed HBM stack supplies 1.2 TB/s locally.
- Six outgoing 256 Gbit/s ports provide at most 192 GB/s per controller.
- Twenty-five such controllers provide at most 4.8 TB/s remotely, before
  cuts, protocol overhead and contention, versus 30 TB/s summed local HBM.
- An architecture trying to consume all 30 TB/s must keep at least 84% of
  those bytes local under this endpoint ceiling, or increase remote bandwidth.
  This is a necessary bandwidth condition, not a sufficient performance model.

Validate the compute-near-HBM alternative before increasing aggregate compute
peaks or selecting spherical packaging.

## First five affordable experiment bundles

Rebuild the baseline evidence with `python scripts/rebuild_publication.py`, the executable prototype evidence with `python scripts/run_prototype_validation.py`, and then the paper with `python scripts/build_paper.py`. Verify all three with `--check` and inspect their separate manifests. Run the experiments below from the repository root after installing `requirements.txt`.
The commands are starting points; the complete matrices below are planned
validation work and are not represented as already executed.

| Bundle | Reproducible starting command | Required comparison and decision |
| --- | --- | --- |
| 1. Correctness and reference bounds | `python -m unittest discover -s tests -v` and `python sim/performance_model.py --output generated/validation/capacity.json` | All tests pass; hand-check bit/byte and decimal-unit conversions. Confirm the 4.8 TB/s remote ceiling and precision-specific roofline. Record failures before fixing them. |
| 2. Equal-resource MoE dispatch | `python sim/run_workloads.py --config examples/moe_dispatch.json --bandwidth-policy equal_total --seed 100 --output generated/validation/moe-equal-total-s100.json` | Repeat for 42/162 ranks, both bandwidth policies, packet sizes 4/16/256 flits, and aggregate rates 0.01/0.1/0.5/1 packets/ns. Retain baseline and failed-link results separately. |
| 3. Locality and skew controls | `python sim/run_workloads.py --config examples/stencil.json --seed 100 --output generated/validation/stencil-s100.json` | Compare the current torus-local stencil against MoE hotspot shares 0/0.5/0.9. Keep identical source/destination traces within each comparison. Do not optimize one topology's placement while freezing its competitor. |
| 4. Static failure sensitivity | `python sim/run_workloads.py --config examples/moe_dispatch.json --failed-links 5 --seed 100 --output generated/validation/moe-f5-s100.json` | Sweep 0/1/5/20 links and multiple seeds. Report removed-link fraction as well as count, all unreachable packets, surviving throughput and latency. An apparent p99 improvement after failure can reflect route redistribution or selection of surviving traffic. |
| 5. Geometry, load and collectives | `python sim/run_evaluation.py --subdivisions 1 --n-packets 2000 --seed 100 --output-dir generated/validation/evaluation-s100` | Inspect the hop/latency distributions and offered-load curves. Compare ring/tree with equal full per-rank payload, reporting ring chunk size. A selected balanced coordinate cut is a cut-capacity estimate, not a minimum-bisection proof. |

The original workload CLI supports fixed placement and seeded static failures.
It does **not** yet import application traces, optimize placement, inject
failures during execution, or simulate an MoE dispatch-compute-combine step.
The separate tiny decoder and transport reference cover scoped numerical and fault cases; they do not make this legacy CLI a full application-trace simulator.

Use seeds 0–9 for development and seeds 100–109 for the first frozen validation
matrix. New seed ranges are cheap additional checks; truly held-out workload
shapes and application traces are also necessary because seed separation alone
does not prevent overfitting to a traffic generator. Store exact configs,
source commit, dependency versions and output JSON together. Keep the final
validation cases unchanged during tuning and repeat them after each design
revision.

## Fairness and statistical discipline

Every comparison must declare the following budgets; matching one does not
match the others.

| Budget | Required record |
| --- | --- |
| Compute and memory | Same participating ranks, usable capacity, precision, numerical accuracy, compute engines and local/remote data placement. Record reserved memory and replication overhead. |
| Link capacity | Per-direction payload rate, lane count, protocol overhead, aggregate directed bandwidth, and aggregate endpoint injection/ejection limits. Publish both equal-per-link and equal-total results. |
| Ports and area | Router radix, SerDes count, buffer SRAM, controller/compute area and package routing feasibility. A higher-degree graph consumes additional physical resources. |
| Geometry | Actual routed lengths, crossings, layer count, vias/connectors, retimers and escape routing; surface arcs are only a geometry assumption. |
| Power and cooling | Measured or explicitly assumed compute, HBM, PHY, router, regulators, host and cooling terms; same sustained power limit and thermal boundary conditions. |
| Software | Same application, batch/sequence lengths, sharding, compiler effort, placement-search budget, warm-up and measurement windows. |
| Reliability | Equivalent fault fractions and representative locations; include partitions, controller loss, retries, duplicated packets and recovery cost when supported. |

Use paired runs: each candidate receives the same workload trace and injection
schedule. Report the median and spread across independent seeds, and a 95%
confidence interval on each paired candidate/baseline ratio. Resample whole
runs or independent trace windows; treating correlated packets as independent
samples can give misleading confidence. Plot mean, p50, p95, p99, maximum,
delivery fraction and completed useful work. Show all validation cases, not
only a geometric average or selected wins.

A finite trace with infinite queues eventually drains. Its drain-inclusive
payload rate is not a sustained, bounded-queue saturation guarantee. The new
bounded model exposes queue occupancy and backpressure, but its drain-inclusive
rate is also not a steady-state capacity measurement. Add explicit warm-up and
steady-state measurement windows, then calibrate the modeled service before
quoting saturation capacity. Latency calculated
only for surviving packets must always appear beside loss/rejection counts.

## Model and application cross-checks

Before the next hardware gate:

1. Compare small graphs against hand-derived timings and a separately
   implemented reference scheduler. Check injection-time translation,
   event causality, full duplex and bounded-link service capacity.
2. Cross-check the selected router with a cycle-level model such as
   [BookSim](https://github.com/booksim/booksim2), including finite buffers,
   credits, VC allocation, switch arbitration, pipeline stages and endpoint
   limits. BookSim is an independent modeling option; it has not been
   integrated or used to validate this repository yet.
3. Capture dispatch **and** combine plus expert compute and synchronization
   for a real MoE layer. Validate outputs against a numerical reference and
   account for quantization scales, top-k, overflow/rerouting and padding.
   Measure full step latency, request p99, useful tokens/s and joules/token.
4. Include graph/irregular gather-scatter traces and a geodesic-mesh halo
   workload if these remain target markets. Add a torus-local stencil and a
   switched/hierarchical candidate as controls with equal optimization effort.
5. For collectives, verify actual reduction outputs and matching datatypes and
   full payloads. Follow the distinction between algorithm bandwidth and
   rank-dependent bus-bandwidth normalization documented by
   [NVIDIA's NCCL tests](https://github.com/NVIDIA/nccl-tests/blob/master/doc/PERFORMANCE.md).
   The Python ideal schedules are not NCCL implementations or benchmarks.

## Gates from simulation to hardware

| Gate | Evidence required to advance | Proposed acceptance condition |
| --- | --- | --- |
| G0: software integrity | Versioned regression results, config schemas, strict JSON reports, independent adversarial review | All mandatory checks pass; no unresolved correctness defect, silent loss or precision mismatch. |
| G1: architectural choice | Frozen application traces, multiple seeds, equal-resource controls, locality and controller-bandwidth accounting | On the chosen target workload, aim for at least 15% more useful application throughput/J; the paired 95% interval must exclude no improvement. Preserve numerical accuracy and the declared service SLO, with no more than 5% p99 regression in required held-out cases. If power is only estimated, this is a model-based screening gate, not proof of efficiency. |
| G2: router/transport proof | Parameterized RTL, bounded-queue model, assertions, clock/reset crossing review, channel-dependency analysis and fault-transition tests | All reachable routes have a specified escape path; prove escape-channel acyclicity and freedom from credit underflow/overflow. Establish liveness under explicit fair-service assumptions. No packet duplication/corruption or unexplained timeout. Random tests alone do not prove deadlock freedom. |
| G3: small FPGA demonstrator | Four to eight logical endpoints plus a controller emulator, reusable trace player, scoreboards, counters and fault injection | Bit-exact packet/collective reference outputs, predictable backpressure, documented live-recovery behavior, and bounded completion/rejection under the declared fault model. Match a detailed timing model within a predeclared tolerance after calibrating PHY/clock differences. |
| G4: physical demonstrator | Costed board/package layout, SI/PI extraction, thermal model correlated with measurements, manufacturing/test access and service plan | Chosen PHY's electrical/BER requirements pass across defined process/voltage/temperature corners; supply and junction limits stay within component specifications with agreed margin. Recover or shut down predictably after link, power, sensor or coolant fault. |
| G5: silicon commitment | Reviewed PPA, DFT/coverage, static timing, CDC/RDC, signoff SI/PI/thermal, package yield and production-test plan, audited app results | Every product requirement has an owner, test and passing evidence. Unvalidated peak FLOPS or simulated nanoseconds do not substitute for signoff. |

The application and p99 thresholds are proposed starting criteria for design
selection. Freeze or revise them openly with the product requirements before
examining the held-out results. A successful application result does not waive
physical signoff or transport correctness.

The current support for these gates is deliberately narrower than completion:

| Gate | Evidence present in v1.2.0 | Outstanding evidence |
|---|---|---|
| G0 | Regression/oracle suites, strict reports and source/output manifests | Review the final revision's executed suite and preserve its passing evidence; hashes alone do not execute tests |
| G1 | Tiny numerical inference, logical ownership and communication byte accounting | Trained-model quality, real traces, calibrated compute/HBM and equal-resource application performance/energy |
| G2 | Bounded software credit invariants, fixed-route dependency certificate, bounded transport cases and local RTL tests | Full router/protocol refinement, formal assertions/liveness, CDC/RDC and adaptive/VC transition proof |
| G3 | Board-agnostic synthesizable word switch with actual simulation and generic synthesis | Board choice, constraints, place/route, timing closure, bitstream and measured trace I/O |
| G4 | Strict measurement loader, configured limits and coverage assessment | Real calibrated lane/corner BER, SI/PI, voltage, power and thermal/coolant records |
| G5 | Architecture proposal and prototype evidence | Product PPA, DFT, signoff, package cost/yield and production-test results |

No application throughput/J or physical acceptance gate is claimed complete.
The [implementation status](IMPLEMENTATION_STATUS.md) and
[measurement protocol](PHYSICAL_VALIDATION_PROTOCOL.md) record these boundaries.

For the original paper's proposed escape VC plus adaptive VCs, the specification must
also define legal VC transitions, injection/ejection dependencies, request and
response networks, fairness, and the channel-dependency behavior while failed
links or routing epochs change. A path found by BFS is not a deadlock proof.
Test old/new coordinate-frame packets in flight during prepare/commit and
rollback. A coordinate-frame rotation changes naming; any performance benefit
requires an explicitly modeled placement, data migration or route-policy change.

## Electrical, power and thermal measurements

Select the PHY and reach class before setting compliance thresholds. UCIe
covers package-level die-to-die interfaces and test/manageability features;
consult the selected revision and licensed IP requirements through the
[UCIe Consortium specifications](https://www.uciexpress.org/specifications).
The current configurable links do not establish UCIe compliance or board-level
channel feasibility.

Use PRBS and real traffic with lane-level error counters, lane deskew, training,
retraining and fault logs. Record raw/pre-FEC and post-FEC error criteria
separately when FEC exists. With zero observed errors in N independent bits,
the one-sided 95% BER upper bound is approximately 3/N. Thus 10^9 clean bits
only support a bound near 3×10^-9. An illustrative raw-BER goal of 10^-12
requires roughly 3×10^12 clean bits per lane, approximately 120 seconds at
25 Gbit/s, plus the selected PHY's required margin/corner tests. This
calculation is a sampling bound, not a compliance certificate; burst errors
and correlated interference require additional tests.

Exercise worst-case simultaneous switching and mixed compute/communication
phases, not only a uniform idle load. Measure rail droop, regulator efficiency,
PHY power and full-system wall energy. Correlate per-component temperatures
and coolant inlet/outlet conditions with sustained application operation,
startup, shutdown and throttling. Include manufacturing variation, temperature
cycling, connector/assembly tolerances and repair access for a faceted assembly.

## Required acceptance artifacts

Each iteration produces one reviewable evidence bundle:

- Source commit, design/config revision, workload/data version and test owner.
- Raw per-run JSON/trace/counter data; failed runs retained with failure reasons.
- Independent reproduction command and environment/dependency manifest.
- Resource-budget table, confidence intervals and full workload/failure matrix.
- Numerical-correctness reference and documented precision/accuracy criteria.
- Open-defect list, decisions and regression tests added for each fixed defect.
- At hardware stages: schematics/layout, route/PHY constraints, measured power
  and thermal conditions, instrument setup and calibration, and signoff reports.

The next design revision should be driven by the dominant measured bottleneck.
If memory-tile egress dominates, change data placement or compute/HBM locality
before adding compute. If endpoint ports dominate, revise port budgets. If a
logical topology loses under equal resources, retain the evidence and select
or redesign the topology before paying for a physical spherical assembly.
