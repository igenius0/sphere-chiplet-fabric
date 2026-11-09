# iGenius accelerator: proposed architecture v0.2

Engineering recommendation, 10 November 2026. Publication/evidence revision: [v1.2.0](PUBLICATION_REVISION.md). This is a candidate to build and validate, not a claim to have found the world's fastest processor. The selection objective is useful application throughput and energy at a fixed numerical-accuracy and request-latency target, with manufacturable packaging and defined failure behaviour.

## Recommended architecture

Start with **four compute/HBM islands, explicit NUMA memory ownership and a hierarchical inter-island fabric**. Each study island contains two compute chiplets, a memory/IO complex, local SRAM and one HBM stack. This gives eight compute chiplets and four stacks for initial architecture sizing; the counts are proposals, not a purchasable component list or completed layout. The application document also gives an eight-island scaling/capacity example, after the smaller platform passes validation.

Expert weights and their compute stay together. Attention/KV shards have stable owners. The network carries activations, collective traffic and explicitly budgeted remote accesses. A wide island-local path serves HBM; inter-island links have their own smaller bandwidth budget. The runtime admits work against the most constrained island rather than aggregate capacity.

Use planar package construction as the initial manufacturing study. Compare a geodesic logical graph, a short-wire mesh/torus and a hierarchical switched graph. Select the logical graph and physical packaging independently using equal-resource application evidence. A physical faceted sphere remains an optional demonstrator whose cooling, wiring and assembly cost must be measured. Planet-style coordinates remain useful for mapping and diagnostics; a frame rotation changes naming and requires no movement of silicon.

| Design decision | Recommendation | Evidence required |
|---|---|---|
| Lead application | Memory-resident MoE inference, full dispatch–compute–combine path | Correct inference output, prefill/decode latency, throughput, expert imbalance and communication byte ledger |
| Memory architecture | Local HBM per compute island; explicit owner and locality | Local/remote byte fractions, peak live memory, achieved bandwidth and controller hotspots |
| Compute | Specify matrix dimensions, count, clock, precision and accumulator first | Synthesized/implemented throughput, utilization, numerical error and area/power |
| Network | Local high-bandwidth domain plus budgeted global links | Endpoint and cut limits, finite-buffer simulation, trace replay and port/wiring cost |
| Programming model | Asynchronous DMA/message passing, versioned allocations and completion events | Correct dependencies, bounded queues, ordering and stale-generation rejection |
| Reliability | CRC/replay, ECC where available, timeouts, isolation and generation-based recovery | Fault injection, no silent corruption/duplication, correct collective abort/retry |
| Management | Independent control path and per-island telemetry | Reset/power sequencing, health monitoring and safe thermal/power response |
| Packaging | Costed compute/HBM floorplan and cold-plate study | PHY reach, SI/PI, power delivery, contact pressure, junction limits, yield and serviceability |

## Why memory locality comes first

The historical Sphere-162 model assumes 25 separate memory tiles, 1.2 TB/s per HBM stack and six 256 Gbit/s ports per tile. Those ports provide **192 GB/s per tile and at most 4.8 TB/s across all memory tiles**, before contention or overhead. Only 16% of nominal 30 TB/s local HBM bandwidth can pass through that endpoint budget.

A graph change cannot remove this ceiling. Supplying one entire stack remotely through six ports would require 1.6 Tbit/s per port, or at least 38 ports at 256 Gbit/s. Keeping compute near its HBM avoids repeatedly carrying weights over those links. Under the illustrative bandwidth budget, sustaining 30 TB/s total service requires at least 84% of HBM bytes to be consumed locally; this condition alone does not establish achievable throughput.

The former 500 TFLOPS/tile and 15 W/tile assumptions also require implementation evidence. A single 256×256 FMA array at 2 GHz yields 262.144 TFLOPS if each cell issues one FMA/cycle and an FMA counts as two FLOPs. Choose array count and precision deliberately and include their data supply and power costs.

## Evidence that shapes the next iteration

The committed [paired-seed study](EXPERIMENT_RESULTS.md) compares the existing flat sphere and torus models. At equal total directed bandwidth, sphere's MoE p99 is higher in the tested cohorts. With equal bandwidth per link, sphere's MoE p99 is lower, while its aggregate network bandwidth is approximately 43–48% greater. The torus-local stencil favours torus by construction.

These results make the resource trade-off visible. They do not establish the proposed island hierarchy's performance. Revision v1.2.0 now adds a bounded flit model, a tiny actual CPU inference path with logical four-island ownership, a bounded protocol reference and small RTL. These are implementation starting points, documented in the [implementation status](IMPLEMENTATION_STATUS.md); a calibrated production hierarchy and physical package still need validation.

| Implemented in this revision | Planned hardware/application work |
|---|---|
| Chronological baseline scheduling and isolated probes; separate bounded flit/credit model with fixed-route dependency analysis | Calibrated router pipelines, wormhole VC allocation, adaptive-routing transitions and memory-controller contention |
| Equal-node, configurable network-budget comparisons | Costed and routed island/hierarchical/spherical package alternatives |
| Tiny causal MoE inference with actual CPU arithmetic, KV cache, logical ownership and an independent reference; legacy dispatch/stencil proxies | Real trained-model and scientific-mesh traces, multi-device execution and calibrated application performance |
| Static failed-link comparisons; bounded transport fault injection, replay suppression, generation abort and actual contribution recovery | Physical failure detection, crash recovery, durable replay history and hardware protocol integration |
| Precision-matched capacity/Roofline bounds | Measured kernels, achieved HBM rates and full-system energy |
| Synthesizable local word-switch RTL with independent simulation oracles and generic synthesis | Formal RTL/liveness proof, target constraints, FPGA place/route, timing closure and board bring-up |
| Independent regression/adversarial tests, GitHub CI and strict physical-measurement assessment | Measured SI/PI, BER, voltage, power and thermal evidence; packaging and manufacturing signoff |

## Iteration and go/no-go rule

1. Freeze workload shapes, accuracy/SLO requirements, budgets and held-out traces before tuning.
2. Extend the tiny numerical path to real application traces and calibrated local/remote memory service; compare candidate graphs under the same compute, HBM, usable capacity and priced port/wiring budgets.
3. Change the dominant measured bottleneck, then rerun the same regression and held-out experiments. Retain losing cases and failed runs.
4. Refine the software/protocol contract into the selected router RTL, then advance to FPGA integration and physical coupons after correctness and budget checks pass.
5. Use a proposed selection gate of at least 15% improvement in useful application throughput/J with no more than 5% p99 regression at the same accuracy and SLO on pre-registered target cases. Freeze or revise that goal before held-out results; estimated power supports screening only.

See [application design](APPLICATION_DESIGN.md), [physical design](PHYSICAL_DESIGN.md) and [validation plan](VALIDATION_PLAN.md) for the requirements, byte budgets, experiments and acceptance artifacts. A design may progress on an agreed latency objective instead of energy when no reliable energy measurement exists, but the decision must state that objective explicitly.
