# Four-island execution plan — architecture study v0.3

13 November 2026. This is a prospective engineering plan added after publication
v1.2.0. It does not change the published results, manufacture hardware, or report
new thermal, FPGA-board or application-performance measurements.

## Design decisions for this iteration

- Keep compute, expert weights and the associated HBM within each island.
  Start the architecture study with four islands: two proposed compute chiplets,
  one memory/IO complex and one local HBM stack per island. These are sizing
  placeholders; access to compatible dies, controllers and packaging is unresolved.
- Give each memory range one explicit owner. Move activations and necessary
  collectives between islands; account separately for local and remote bytes.
- Keep planar construction as the reference. Evaluate a geodesic logical network
  independently of a physical faceted shell; neither is selected as superior.
- For the shell experiment, replace the proposed internal coolant core with
  segmented external cold plates. This is the cooling candidate for testing,
  not a qualified replacement. Leave internal space for supports, power and wiring.
- Use removable flat module carriers and inspectable thermal interfaces. Orient
  each selected package so its approved heat-transfer surface faces its plate.
  Resolve HBM height differences with package-specific contacts, not one assumed
  flat surface or an arbitrary clamp load.

## Two independent demonstrators

| Workstream | First deliverable | What it cannot establish |
|---|---|---|
| Functional FPGA | Existing four-endpoint word switch on one suitable board, with trace input/output, counters and a host scoreboard | ASIC/HBM performance, multi-hop router correctness, physical sphere feasibility or cooling |
| Thermal vehicle | Representative heater modules on planar and faceted carriers, with segmented external cooling | Working compute/HBM silicon, inference speed or safe junction temperatures without a validated thermal model |

Four logical FPGA endpoints are not four physical compute/HBM modules. Heater
surrogates are not functional accelerators. Keep their configurations and reports
separate and integrate only after each experiment meets its declared criteria.

## Execution sequence and review artifacts

| Step | Work | Required artifact | Current state |
|---|---|---|---|
| A | Freeze one target MoE workload and quality/latency objectives; identify a trained model and usage rights | Model/version, precision, batch, context, top-k, expert placement, reference outputs and held-out traces | Pending; current decoder has synthetic weights |
| B | Budget local/remote traffic for four islands | Per-island live bytes, HBM service, dispatch/combine bytes, endpoint limits, overload policy and power terms | Analytical starting point only |
| C | Compare geodesic, short-wire torus/mesh and switched hierarchy | Same traces, compute, memory, port/wiring budget and optimization effort; retain losing cases | Pending; existing flat sphere/torus results are separate |
| D | Select an accessible FPGA with supported toolchain | Board/device, available logic/BRAM, clock, host I/O, tool licence, constraints and resource estimate | Pending; no board or purchase committed |
| E | Integrate the small switch | Wrapper, legal reset/clock handling, bounded host traffic generator, scoreboard, place/route and bitstream | Pending; generic RTL simulation/synthesis already exists |
| F | Build a heater coupon and compare cooling | Drawings, BOM/quotation, thermal model, frozen limits, calibrated capture and acceptance worksheet | Pending; see cooling protocol |
| G | Decide packaging and scale | Evidence review against the validation gates; cost, serviceability and risks recorded | Pending |

Four endpoints are sufficient for checking functional contracts, not for
establishing a general topology scaling advantage. Use larger matched-resource
simulations for the network decision.

Steps A–C need real application inputs and further software work. Steps D–F need
an engineering/laboratory partner or equipment. No complete real-trace importer,
three-candidate application benchmark, board wrapper or thermal CAD is claimed
by this plan. Request a board-selection review and a small heater-coupon quote
before ordering hardware; custom HBM packaging is outside the first demonstrator.

## Functional acceptance contract

Use the existing [RTL interface](RTL_PROTOTYPE.md) as the board integration
contract: single clock, ready/valid acceptance, stable stalled outputs, identity
preservation and reset flushing. The scoreboard must check accepted, delivered,
queued and explicitly reset-cancelled words, including sequence wrap and stalls.
Do not promise delivery of pre-reset traffic. The word switch does not implement
the Python DATA/ACK retry protocol or multi-hop credit router; those require
separate integration and verification before being tested as hardware features.

Record timing constraints, unconstrained paths, implementation tool versions,
clock achieved, utilization, timing margin, bitstream/source hashes and host
capture hashes. Timing must close at the declared clock before bring-up.
Repeat the documented contention, stall, reset and invalid-destination cases
where applicable. A stuck sink permits blocked traffic; it is not a finite
completion guarantee. Report the exercised scope and every failed case.

## Work available without equipment

Run existing checks from the repository root, preserving logs outside committed
publication outputs. Install requirements and the documented tools first.

```bash
python scripts/rebuild_publication.py --check
python scripts/run_prototype_validation.py --check
python scripts/build_paper.py --check
python -m unittest discover -s tests -v
python scripts/run_rtl_validation.py --output generated/planning-review/rtl.json
python -m sim.moe_inference --config examples/moe_inference.json --output generated/planning-review/inference.json
```

These execute existing contracts, not steps A–G in full. The RTL command needs
Icarus/VVP and Yosys. Optional tool skips must be reported and cannot count as
executed hardware-tool tests. The paper check verifies hashes without TeX.
The missing-hardware measurement example must remain INSUFFICIENT_EVIDENCE.

## Decision rule

Use the [existing validation gates](VALIDATION_PLAN.md), including numerical
quality and application SLOs. The proposed 15% throughput/J improvement and at
most 5% p99 regression remain unfrozen selection goals until workload and
measurement budgets are agreed before held-out runs. Do not derive joules/token
from heater results or partial chip power. Adopt shell packaging only after its
measured benefit justifies routed-link, cooling, assembly and maintenance costs.
See [external cooling experiment](EXTERNAL_COOLING_EXPERIMENT.md) and
[current implementation boundaries](IMPLEMENTATION_STATUS.md).
