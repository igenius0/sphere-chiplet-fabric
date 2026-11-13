# Research review and evidence status

Review baseline: original main commit `6440633e56af59fbefcb183736994769bd5bcd58`. Corrected publication revision **v1.2.0, 10 November 2026**, updates the paper, its PDF, root `figures/` and `artifacts/` to the current model and evidence. The original April 2026 bundle remains at [Git tag v1.0.1](https://github.com/igenius0/sphere-chiplet-fabric/tree/v1.0.1) and [DOI 10.5281/zenodo.19918238](https://doi.org/10.5281/zenodo.19918238). The revised evidence supersedes the affected original claims; it does not retroactively validate them. See the [publication revision record](PUBLICATION_REVISION.md).

## Baseline findings addressed

| Finding | Correction in this revision | Consequence |
|---|---|---|
| Packets labelled uncontended were injected as a simultaneous burst | `run_traffic(rate=None)` resets link state for every probe | Regenerate unloaded latency and tail distributions |
| A submitted packet reserved downstream links before earlier-arriving competitors | Chronological hop-arrival `send_batch()` scheduler; used by traffic and collective phases | Load/contention results no longer depend on submission order except explicit simultaneous ties |
| Disconnected collectives silently produced timings; singleton traffic could loop | Explicit validation and unreachable-route handling | Invalid experiments fail; valid delivery/loss accounting remains visible |
| Finite inputs could overflow timing or lose positive delays at huge timestamps | Validate derived timing/service values and floating-point resolution | Unrepresentable experiments fail explicitly |
| Topology dimensions and edge cases were unchecked | Validate dimensions/radius; clean degenerate tori; report unreachable pairs | Exported graphs and statistics are internally consistent |
| Antipodal chord lengths used surface arcs | Straight chords use Euclidean length; surface neighbors keep arcs | Chord latency uses the intended geometric path |
| Comparisons mixed different node counts/network budgets | Equal 42/162-node experiments and equal-total/per-link policies | Exposes extra ports, bandwidth and wiring costs |
| A chosen, sometimes unbalanced cut was called bisection | Report one balanced coordinate-median cut with partition sizes | No minimum-bisection claim |
| Ring chunks and tree full messages were compared as equal payload | Evaluation uses ring 8-flit chunks and tree N×8-flit full payloads | Matched per-rank payload; ideal schedule only |
| FP8 peaks were applied to FP16/FP64 tasks; competitor tables mixed sparsity and SRAM/HBM | Replace comparator with explicit precision-matched Roofline ceiling | No unsupported competitor, efficiency or mixed-precision ranking |
| HBM stack bandwidth bypassed fabric endpoint constraints in the analytical model | Expose 30 TB/s local stack sum and ≤4.8 TB/s remote endpoint ceiling | Makes compute/HBM locality a first-order requirement |
| Component power was presented as system power | Report component subtotal and missing HBM/system terms | Full-system efficiency remains unknown |
| Scripts wrote to an author's absolute path and executed on import | Import-safe CLIs with caller-selected output paths | Reproducible runs from arbitrary working directories |
| Root publication assets and citation metadata referred to the original model | Rebuild current JSON/figures/generated tables and revise paper/citation metadata; record source and output hashes | Current repository publication and code share an explicit evidence trail |
| No automated regressions or CI | Independent analytic, CLI, graph, workload, model and adversarial tests; Python 3.10/3.12 CI | Repeatable software correctness checks |

## Prototype implementation added in v1.2.0

The [implementation status](IMPLEMENTATION_STATUS.md) records the new finite-buffer flit model, tiny actual CPU MoE decoder, bounded CRC/retry/generation protocol, synthesizable four-endpoint RTL and physical-measurement harness. [Independent validation](INDEPENDENT_VALIDATION.md) checks event replay, hand-derived timings, numerical references and fault cases. Actual expert-output payloads pass through the transport reference in a separate recovery case; lost ACKs prevent cache/logit commit rather than silently accepting uncertain work. These additions are separate from the flat-topology plots and do not validate physical hardware claims.

## Claims requiring further work

The original FabricSim baseline has infinite output queues, shortest-hop BFS and independent full-duplex links; its VC count remains metadata. The separate finite-buffer model adds bounded input queues, credits, endpoint limits and a fixed-route dependency certificate. It does not implement arbitrary adaptive VC transitions or wormhole VC allocation. The bounded transport generation barrier is not the original distributed coordinate-frame prepare/commit protocol. Neither model is a calibrated physical router.

The sphere has twelve degree-five vertices and other degree-six vertices at subdivision two; those vertices are not all graph-equivalent under rotation. Coordinate reorientation preserves distances, topology and ownership. Performance improvement requires an actual change to work/data placement or route policy, with its costs measured.

The legacy synthetic dispatch proxy is not full MoE inference. The new tiny decoder actually evaluates causal attention, experts and combine against an independent CPU reference, with synthetic weights and logical four-island ownership. It does not establish trained-model quality, multi-device execution or accelerator throughput. Periodic-grid stencil placement is aligned with torus wiring. Finite-trace average payload delivery is not a sustained saturation measurement. Static failed edges are not live fault recovery. Delivered-only percentiles must always be read with loss counts.

The historical 56.5 PFLOPS, 900 GB, 30 TB/s and approximately 2.3 kW describe assumptions/projections, not an implemented processor. The former efficiency ratio and headline timing comparisons should not be reused as validated results. In particular, NVIDIA's H100 datasheet marks 3,958 FP8 TFLOPS with sparsity; assigning that number to a dense field was inconsistent. [NVIDIA source](https://resources.nvidia.com/en-us-gpu/h100-datasheet-24306).

Use the [design proposal](DESIGN_PROPOSAL.md) for the recommended next architecture and the [validation plan](VALIDATION_PLAN.md) to decide when evidence is sufficient to advance.
