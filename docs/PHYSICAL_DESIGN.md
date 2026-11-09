# Physical architecture recommendation

Status: engineering proposal, 9 November 2026. No placement, routing, package extraction, silicon implementation or thermal qualification has been completed. Numbers derived from the repository are analytical bounds. External examples establish available technology classes, not access to compatible chiplets or a manufacturing quotation.

## Recommended direction

Build a modular accelerator around **compute and HBM islands with explicit memory locality**, then select the inter-island network using application traces and a costed floorplan. Keep the geodesic network as a candidate. Evaluate a physical spherical shell independently, after the network and memory architecture earn their cost.

This preserves the research contribution while allowing manufacturable packaging to improve application performance. A coordinate transform can support scheduling on either physical layout; changing latitude/longitude does not increase bandwidth or move data.

A concrete planning reference is four islands, each containing two compute chiplets, one memory/IO controller complex and one local HBM stack. Eight compute chiplets and four stacks are a manageable architecture study point, not committed silicon specifications. Local compute accesses the stack without crossing the inter-island network. The controller owns that island's address range; remote access uses explicit DMA/message passing. Choose stack capacity, compute precision, die count and port width only after workload and floorplan measurements. Scale island count after the four-island case passes the gates below.

TSMC documents 2.5D CoWoS integration of logic and HBM. AMD documents a deployed chiplet accelerator containing compute dies and local HBM. These support the feasibility of the technology class; neither validates this proposed four-island implementation. [TSMC packaging](https://3dfabric.tsmc.com/english/dedicatedFoundry/technology/cowos.htm), [AMD architecture](https://instinct.docs.amd.com/projects/system-acceptance/en/latest/gpus/mi300x.html).

## The memory bottleneck that must change

The original reference assigns one 1.2 TB/s HBM stack to each of 25 remote memory-controller tiles, each with six 256 Gbit/s links per direction. In decimal units:

| Bound | Calculation | Result |
|---|---|---:|
| One link, one direction | 256 / 8 | 32 GB/s |
| One memory tile's maximum outbound supply | 6 × 32 | 192 GB/s |
| All 25 memory tiles' maximum remote supply | 25 × 192 | 4.8 TB/s |
| Fraction of nominal 30 TB/s able to leave these tiles | 4.8 / 30 | 16% |
| Rate per port needed for one full stack through six ports | 1.2 × 8 / 6 | 1.6 Tbit/s |
| 256 Gbit/s ports needed per stack | ceiling(1,200 / 32) | 38 |

These are optimistic endpoint ceilings before headers, contention, cuts, controller arbitration or failures. A better graph cannot remove the endpoint ceiling. Local compute/HBM islands address this directly: expert weights and KV-cache shards stay with their consumers; activation transfers and collectives use the fabric. Remote bandwidth remains explicitly limited.

If six-port connectivity is retained, admit a workload only after measuring its remote byte rate and hotspot distribution. Adding ports changes die-edge area, PHY power, wires and switch size and must be costed together. The revised `compute_aggregate()` exposes the 4.8 TB/s bound instead of treating all local HBM bandwidth as uniform remote supply.

## Physical sphere versus logical geodesic network

| Choice | Potential value to test | Engineering cost that needs evidence |
|---|---|---|
| Planar islands with short local HBM connections | Conventional package construction and cold-plate contact | Placement and routing may make geodesic long links expensive |
| Geodesic logical inter-island graph | Multiple paths and more uniform degree | More ports and wires than a four-degree torus at equal node count |
| Hierarchical local network plus global links | Match local reuse and infrequent remote traffic | Global links can become bottlenecks; hierarchy changes collective schedules |
| Physical faceted sphere | A distinct mechanical distribution/cooling experiment | Rigid-flex bends, assembly yield, HBM placement, coolant plumbing and serviceability |

The radius-40 mm Sphere-162 graph has approximately 11.07–13.05 mm surface-neighbor arcs, as calculated by `make_icosphere(2, radius_mm=40)`. The gross shell area is 20,106 mm², or about 124 mm² per graph vertex. This is not a package floorplan: no allowance has been made for actual die/HBM dimensions, keep-outs, PHY banks, connectors, coolant interfaces or flex bend radii. Twenty planar facets also do not make 162 flat chiplets conform to a smooth shell automatically.

The UCIe Consortium's published UCIe 1.0 overview distinguishes standard-package channels up to 25 mm and advanced-package channels up to 2 mm. Consequently, the current 11–13 mm arcs cannot simply inherit advanced-package energy or latency assumptions. Select a specific standard/version, PHY implementation and channel construction, then extract the entire route. Board-to-board demonstrators need an appropriate board-link PHY; labeling an arbitrary eight-lane link “UCIe” does not establish compliance. [UCIe implementation overview](https://www.uciexpress.org/post/how-ucie-will-enable-broad-chiplet-adoption), [specification scope](https://www.uciexpress.org/specifications).

## Compute, power and cooling budgets

The 500 TFLOPS dense-FP8-per-tile assumption needs an implementation. At 2 GHz it requires 125,000 fused multiply-adds per cycle under the convention of two FLOPs per FMA. A single 256 × 256 array issuing one FMA per cell per cycle would supply 262.144 TFLOPS at that frequency. Array count, accumulation precision, data movement and utilization must therefore be specified before accepting a 500 TFLOPS peak. A tile power assumption of 15 W cannot establish that peak's efficiency.

Use separate budgets for compute, SRAM, HBM, memory PHY/controller, fabric dynamic and static power, clock/reset, power conversion, host/external IO and cooling. The revised analytical model reports a component subtotal and leaves full-system power unknown. It is insufficient for a PFLOPS/kW product claim.

For the planar prototype, use a cold plate and instrument each compute and HBM region. For a later shell experiment, use segmented, removable thermal interfaces with verified contact pressure. Compare both at the same coolant inlet temperature and flow, workload, allowable junction temperatures and measured wall power. Average shell heat flux is insufficient: localized die hotspots, HBM limits, pressure drop and package stress control acceptance.

The power-distribution model must include rail-specific load steps, allowable droop, plane impedance and regulator placement. Size upstream supply from the completed worst-case rail budget, efficiency and transient margin. The historical demonstrator's 24 V input is a study assumption, not a qualified supply requirement for a multi-kilowatt design.

## Reliability and software contract

- Give every tile a stable physical ID and every memory range a single owner. Expose locality and remote bandwidth to the runtime; avoid implying uniform memory latency or coherence.
- Put link training, health counters, CRC/retry and bounded timeout semantics in the interface contract. Static graph-edge removal in the current simulator does not validate live failover.
- Verify a deadlock-free escape network, finite-buffer credits, request/response channel dependencies and starvation bounds in RTL before adaptive routing is enabled. The Python BFS simulator does not establish those properties.
- Keep a management path available when the data network degrades. Define safe clock/power sequencing, link isolation, reset and error containment. Define the fate of outstanding DMA on every reset.
- Design for known-good-die testing, boundary access, replaceable modules, calibration records and traceable faults. Obtain packaging/assembly yield and rework assumptions before computing cost per usable accelerator.

## Prototype and acceptance gates

| Stage | Build or evidence | Acceptance evidence required before advancing |
|---|---|---|
| P0: workload and architecture | Trace-based model of local/remote memory and communication, with equal-budget alternatives | Correct outputs; complete byte accounting; no endpoint-cap violation; results across seeds, offered load, message sizes and placement; report cases where each design loses |
| P1: router and controller | Synthesizable RTL plus FPGA emulation of a small island network | Correctness against a software oracle; finite-buffer backpressure; credit conservation; reset/retry/fault handling; formal deadlock/ordering properties or an explicit unresolved-proof list |
| P2: electrical coupon | One worst-case local channel and one worst-case global channel using selected PHYs | Extracted SI/PI across process/voltage/temperature; vendor compliance requirements met; BER confidence and test duration reported; error recovery exercised |
| P3: thermal/mechanical vehicle | Heater dies, representative HBM-height structures, real interfaces and coolant path | Junction surrogate temperatures within chosen component limits; load-step and pump/fan-failure response; pressure/leak and thermal-cycle evidence; fit and assembly access verified |
| P4: island prototype | Four-island planning design, or a smaller equivalent if component access requires it | Measured application speed/energy at a fixed accuracy and latency target; local/remote bandwidth; failure-mode behaviour; signed-off manufacturable floorplan and BOM |
| P5: scale and packaging decision | Increase island count; compare planar and optional shell packaging | Application benefit exceeds additional wiring, power, assembly and service costs under a pre-registered decision rule |

FPGA time-multiplexing can validate protocols and scheduling but cannot establish ASIC link bandwidth, silicon area, timing closure or power. No gate above has been claimed complete by this document. The implemented Python regression suite supports P0 correctness only; hardware work remains a separate deliverable.
