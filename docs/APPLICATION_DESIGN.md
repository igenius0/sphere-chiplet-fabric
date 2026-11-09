# Application design: local-memory MoE inference accelerator

Publication/evidence revision **v1.2.0, 10 November 2026**; see the [revision and evidence map](PUBLICATION_REVISION.md).
**Status:** product architecture and validation contract, with a scoped tiny CPU implementation added on 10 November 2026. The [inference prototype](INFERENCE_PROTOTYPE.md) executes causal attention, KV cache, expert dispatch/compute/combine and generation with synthetic weights and logical island ownership. Production runtime, trained-model quality and hardware performance remain unvalidated.

## Recommended lead application

Develop a modular accelerator for **mixture-of-experts (MoE) inference**, with expert weights resident beside the compute that consumes them. Use explicit memory ownership and message passing between compute-memory islands. Evaluate the geodesic graph as one candidate for moving activations between islands; select packaging and interconnect after testing application traces and implementation cost.

This target gives the project a concrete problem: control dispatch/combine latency under skewed expert demand while preserving useful throughput and request latency. [DeepEP](https://github.com/deepseek-ai/DeepEP) provides an existing expert-parallel dispatch/combine implementation to study and use as a reference on compatible systems. It does not supply a backend for hypothetical Sphere hardware.

The strongest immediate change is memory locality. The reference design assumes 1,200 GB/s of local HBM bandwidth per memory tile, but six 256 Gbit/s outward links provide only 192 GB/s per direction. Reaching the full local-memory rate therefore requires at least 84% of those HBM bytes to be consumed locally under this simplified budget; routing, protocol traffic and contention increase the locality requirement. This is a necessary bandwidth condition, not a throughput prediction.

## Island and runtime contract

| Component | Proposed contract | Reason and validation |
|---|---|---|
| Compute-memory island | Matrix/vector compute, local SRAM, HBM controller and DMA share a local bandwidth domain | Expert weight reads avoid the inter-island links; measure achieved local bandwidth and matrix utilization together |
| Memory ownership | Allocations carry `island_id`, owner, size, access rights and generation | The runtime can budget local/remote bytes and reject stale or unauthorized DMA |
| Inter-island transport | Explicit asynchronous send/receive and completion events | Exposes communication dependencies and allows measured overlap with independent work |
| Collective layer | Dispatch/combine first; reduce-scatter/all-gather/all-reduce as required by model partitioning | Match datatype, payload, ranks and reduction semantics against a reference |
| Placement | Experts stay near their resident weights; hot experts may have budgeted replicas | Reduces remote weight traffic; replicas consume capacity and require consistent model versions |
| Scheduler | Admit by local free memory, expert queue capacity and request deadline | Prevents overload from becoming silent packet or token loss |
| Identity and coordinates | Stable physical endpoint IDs; optional geodesic coordinates for mapping and diagnostics | Rotating a display frame must preserve allocation ownership and in-flight destinations |
| Host interface | Load signed/versioned model artifacts, submit requests, collect completion/error records | Keeps deployment, model version and request accounting observable |

A collective operation is a distributed agreement about counts, datatypes and participants. [NCCL's collective definitions](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/usage/collectives.html) are a useful correctness reference; they do not imply NCCL compatibility for new silicon.

## End-to-end inference flow

1. The admission controller reserves KV pages and bounded activation/communication workspace on the assigned islands. It rejects or queues a request when any required island cannot reserve capacity.
2. Attention and shared layers execute against local shards. KV cache pages remain with their attention owner; cross-island attention traffic is traced explicitly when the partition requires it.
3. The gate selects top-k experts. The runtime groups token activations by destination and attaches source-token ID, expert ID, model generation and routing generation.
4. Dispatch transfers activations to expert owners. The receiver checks bounds, generation and payload integrity before releasing the expert kernel.
5. Expert computation reads resident weights, executes a precision-specific kernel, and records completion. Local expert selections bypass network transport.
6. Combine returns outputs to the original token owner. The owner applies the agreed weighting/reduction exactly once and verifies that all expected contributions arrived.
7. The scheduler advances dependent work and returns the token. It records queueing, attention, dispatch, expert, combine and host overhead separately.

A failure during steps 3–6 aborts the affected execution generation. The host can retry the request from a known state after rebuilding the communicator and reservations. Automatic rerouting alone does not make duplicate expert outputs or partially completed collectives correct. The bounded [transport reference](TRANSPORT_PROTOCOL.md) now tests recovery, replay suppression, generation abort and uncertain delivery. Distributed crash recovery and the production runtime still require their own protocol/RTL validation.

## Capacity plan: illustrative workload, not a model or chip specification

Use a synthetic sizing case before selecting a real model: 64 billion total parameters in BF16, eight islands, 32 GiB usable HBM per island, 64 attention layers, eight KV heads, head dimension 128, 16 concurrent sequences and 4,096 cached tokens per sequence. The parameters include all experts and shared layers. These values are design inputs; no physical implementation or model accuracy is implied.

Weights occupy `64e9 × 2 = 128 GB = 119.21 GiB`, before any replicas or extra representations. Perfectly balanced eight-way placement uses 14.90 GiB per island. A real expert granularity can make the maximum island larger than this average.

For ordinary dense KV storage:

`KV_bytes = 2 × layers × KV_heads × head_dimension × bytes_per_element × total_cached_tokens × replication_factor`

The example has 256 KiB per cached token and 16 GiB total KV storage at replication factor one. Ideal eight-way KV sharding uses 2 GiB per island. When tensor-parallel placement replicates KV heads, charge that replication explicitly. [PagedAttention](https://arxiv.org/abs/2309.06180) motivates paged allocation for dynamically growing KV caches; paging does not remove the bytes required by the model.

| Per-island budget | Illustrative GiB | How to validate |
|---|---:|---|
| BF16 resident weights | 14.90 | Sum actual tensor allocations, padding and expert imbalance |
| KV cache | 2.00 | Include context growth, sharing, sharding and replication |
| Activation/kernel workspace | 4.00 | Profile peak live allocations during prefill and decode |
| Communication staging | 1.00 | Include send, receive and double-buffer lifetimes |
| Runtime and metadata | 1.00 | Measure allocator, page tables and model metadata |
| Admission reserve | 4.00 | Keep unavailable to normal admission |
| Total committed | 26.90 | Check every island, not aggregate free capacity |
| Unassigned expansion margin | 5.10 | Absorbs measured imbalance or larger workspace |

FP8 weights would require a separate numerical acceptance test, scales and any additional stored representations. BF16/FP8 peak rates cannot be substituted for FP64 requirements. Training requires a different budget for gradients, optimizer state and saved activations.

## Communication sizing and bottleneck test

For 128 tokens originating at one rank, hidden width 8,192, top-k two and BF16 activations, dispatch creates at most 4 MiB of activation payload when all assignments are remote. Returning equal-width BF16 expert outputs adds 4 MiB. Metadata, alignment, packet headers and retries add bytes. At an ideal 192 GB/s of endpoint bandwidth, the sequential dispatch-plus-combine payload alone takes at least **43.7 microseconds**, before expert compute, queueing or software overhead. This illustrates why tiny-packet nanosecond results cannot be converted directly into layer latency or tokens/s.

Track the byte ledger separately for weights, KV access, dispatch, combine, tensor-parallel collectives and host traffic. For each phase, compare required bytes against endpoint, cut and memory-controller capacities. Measure the fraction served locally, the busiest link, and the busiest expert rather than only their averages.

## What can be reproduced now

From the repository root:

```bash
python scripts/rebuild_publication.py
python -m sim.run_workloads --config examples/moe_dispatch.json --output generated/moe.json
python -m sim.run_workloads --config examples/stencil.json --output generated/stencil.json
python -m sim.performance_model --output generated/capacity_bounds.json
python -m unittest discover -s tests -v
```

The new tiny decoder can be run with `python -m sim.moe_inference --config examples/moe_inference.json --output generated/inference.json`; it is checked against an independent full-prefix numerical reference. The aggregate `python scripts/run_prototype_validation.py` also exercises actual expert-output recovery, bounded network, transport and RTL scenarios. Its expert-output fault case permits a token to commit only after all remote outputs are acknowledged; ACK exhaustion leaves the token's cache and logits uncommitted. Transport ticks do not contribute to the modeled network-cycle performance estimate.

The original MoE proxy generates seeded skewed dispatch pairs. It excludes combine, gating, compute, memory access, tensor-parallel phases and request scheduling. The stencil proxy is a control workload whose logical grid directly matches torus wiring. Equal-total bandwidth normalizes the sum of link capacities; it does not normalize wiring, area, power or port cost.

For the supplied 42-rank, seed-42 MoE example, the original baseline model gives approximately 277.21 ns p99 for sphere and 230.11 ns for torus. These are finite-length synthetic traces with infinite output queues and fixed rank placement. They establish a baseline to improve; they do not demonstrate a spherical advantage or validate the separate bounded model. Re-run the committed configuration after simulator changes rather than treating these rounded values as a performance guarantee.

## Experiment and iteration contract

| Experiment | Reproducible factors | Required interpretation |
|---|---|---|
| Current dispatch proxy | 42/162 ranks; seeds 0–9; 64 B/1 KiB/16 KiB/1 MiB packets; aggregate rates 0.001/0.01/0.1/1 packet/ns; hotspot shares 0/0.5/0.9 | Report each candidate's delivery, p50/p95/p99, offered load and resource budget |
| Current static failure proxy | Zero, one and three seeded link removals; identical traffic before/after | Report lost deliveries separately; this is not a live recovery test |
| Placement sensitivity extension | Identity mapping, random mappings and topology-aware mappings with identical optimization effort | Prevent favorable placement from being credited entirely to topology |
| Full MoE trace extension | Dispatch, expert duration, combine, tensor-parallel phases; prefill/decode; top-k 1/2/4; measured request-length mix | Preserve per-token dependencies and compare numerical outputs against a reference |
| Memory-locality extension | Local/remote HBM bytes; hot-expert replicas; per-island memory fit; KV head replication | Evaluate bandwidth and capacity together; reject overcommitted placement |
| Hardware/emulator calibration | Pairwise size sweeps, concurrent injection, queue limits, local-memory bandwidth and power at wall input | Fit model parameters on one data set and validate against held-out traces |

Run small cases first, then the matrix. Record configuration, software revision, model revision, seeds, warm-up policy, measured interval and failures. The original proxy uses infinite output queues and finite traces. The new bounded model permits queue/backpressure experiments, but finite drain-inclusive rates alone still do not establish sustained steady-state capacity.

Proposed go/no-go thresholds for the next design iteration:

- **Correctness:** exact token/contribution accounting and no silent corruption; numerical output meets a model-specific tolerance fixed before performance tuning.
- **Reproducibility:** repeatable seeded simulation and independent reference checks on small graphs; preserve every regression that found a defect.
- **Application value:** use the common proposed gate of at least 15% more useful application throughput/J against the strongest cost-matched baseline, at the same accuracy and service objective, with no more than 5% p99 regression in required held-out cases. The paired confidence interval must exclude no improvement. Freeze or revise this goal before held-out testing; estimated power supports screening only. A latency-only objective requires an explicitly agreed alternative gate when energy cannot be measured.
- **Resource feasibility:** memory fits on every island, measured endpoint/cut bandwidth supports the admitted load, and full-system power and temperature remain inside the separately approved hardware envelope.
- **Failure behavior:** injected faults produce a bounded, visible error or verified recovery; every admitted request ends in one accounted completion or explicit failure.

If a geodesic candidate misses these gates, keep the compute-memory/runtime improvements and select the better-performing fabric. The architecture decision should follow useful application results and physical implementation cost.
