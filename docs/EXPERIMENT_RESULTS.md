# Reproducible topology sensitivity experiment

Publication/evidence revision **v1.2.0, 10 November 2026**. This report retains the baseline sensitivity experiment and its numerical interpretation; the new [prototype evidence](IMPLEMENTATION_STATUS.md) is separate.

The existing **flat** sphere model has higher synthetic MoE p99 latency when total baseline link bandwidth is matched, but lower p99 when each link has the same rate and the sphere receives more total links and bandwidth. The choice of resource budget changes the conclusion. These results do not establish a best accelerator architecture.

This experiment does **not** implement or test the proposed hierarchical, co-located-compute/HBM design. The tiny CPU inference and bounded-network prototypes added in v1.2.0 are not used for these tables or the eight baseline plot pairs. This experiment does not measure a physical spherical microprocessor, silicon, or end-to-end application performance.

## Reproduce the report

From the repository root, with Python 3.10+:

```bash
python -m pip install -r requirements.txt
python scripts/rebuild_publication.py
# To rebuild only this sensitivity report:
python -m sim.run_sweep --n-packets 500 --seeds 7 19 42 73 101 --output examples/results/design_sweep.json
```

The direct-script equivalent is `python sim/run_sweep.py` with the same arguments. For a small smoke run, use `--n-packets 2 --seeds 7`. Add `--json` to print the complete report. The runner permits 1–2,000 packets per profile and 1–10 unique seeds. Imports do not execute experiments or write files. The JSON contains no timestamps and repeats deterministically for the same configuration, code, and numerical environment.

The committed run records its exact Python and NumPy versions in the report's `provenance`; the project supports Python 3.10+. The [publication manifest](../artifacts/publication_manifest.json) records the full rebuild environment, configurations, source hashes and generated-output hashes. The [revised paper](../paper/main.pdf) uses tables generated from the same JSON reports.

The committed [JSON report](../examples/results/design_sweep.json) contains exact configurations, compact per-profile metrics, removed link IDs, resource budgets, and cohort summaries. Its `provenance` records the exact Python and NumPy versions and SHA-256 fingerprints of `sim/fabric.py`, `sim/topology.py`, `sim/validation.py`, `sim/workloads.py`, and `sim/run_sweep.py`. Source files are resolved relative to the runner, so fingerprints also work when invoked from another directory. There are no timestamps or self-referential commit IDs. A changed source fingerprint identifies a different code state even if a small smoke trace happens to return identical metrics.

The separate [flat-topology evaluation](../artifacts/results.json) uses 162 nodes, seed 42, 2,000 packet pairs and equal total directed link bandwidth; its eight figure pairs are in `figures/`. The [capacity report](../artifacts/performance_model.json) contains assumed resource ceilings and incomplete component power. Those are distinct experiments and evidence types; values must not be mixed with the five-seed workload medians below.

## Experimental setup

- Workloads: synthetic skewed MoE expert dispatch and periodic four-neighbor 2D stencil traffic.
- Rank counts: 42 (sphere subdivision 1 versus torus 6×7), and 162 (subdivision 2 versus torus 9×18).
- Five paired seeds: 7, 19, 42, 73, 101. Every paired candidate/scenario receives the same rank pairs, message size, and injection times.
- 500 packets per profile, 16 flits per packet: 1,024 payload bytes. Aggregate injection is **0.1 packets/ns**, or 819.2 Gbps of offered payload across all ranks. It is not a per-rank injection rate.
- Two bandwidth policies: `equal_total` matches the baseline sum of directed link capacities; `per_link` assigns 256 Gbps per direction to every link.
- Baseline and pre-failed scenarios: one seeded undirected link is removed in both directions before each failed scenario. This tests static reachability and changed BFS routes, not live fault detection or recovery.
- 40 paired profiles, comprising 160 candidate/scenario runs and **80,000 packet attempts**. All had zero unreachable packets; finite-buffer loss and retransmission are absent from this model.

Each range below is the observed minimum–maximum across five seeds, with the median shown first. These are descriptive spreads, **not confidence intervals**. The paired ratio is computed within each seed before aggregation; it need not equal the ratio of the displayed p99 medians.

## Baseline p99 latency

Ratios greater than 1 indicate higher sphere p99 latency.

| Workload | Ranks | Bandwidth policy | Sphere median p99 (ns) | Torus median p99 (ns) | Paired sphere/torus p99: median [min, max] |
|---|---:|---|---:|---:|---|
| MoE dispatch | 42 | `equal_total` | 280.77 | 224.10 | 1.226 [1.143, 1.315] |
| MoE dispatch | 42 | `per_link` | 182.90 | 224.10 | 0.834 [0.776, 0.914] |
| MoE dispatch | 162 | `equal_total` | 512.36 | 442.35 | 1.158 [1.119, 1.326] |
| MoE dispatch | 162 | `per_link` | 344.78 | 442.35 | 0.779 [0.771, 0.879] |
| 2D stencil | 42 | `equal_total` | 287.17 | 34.03 | 8.440 [8.440, 8.859] |
| 2D stencil | 42 | `per_link` | 204.89 | 34.03 | 6.021 [6.021, 6.021] |
| 2D stencil | 162 | `equal_total` | 542.32 | 34.03 | 15.938 [15.199, 17.453] |
| 2D stencil | 162 | `per_link` | 363.26 | 34.03 | 10.676 [10.037, 12.018] |

For synthetic MoE dispatch, matching total bandwidth yields median sphere/torus ratios of 1.226 at 42 ranks and 1.158 at 162 ranks. Holding each link rate fixed reverses the result to 0.834 and 0.779, while increasing the sphere's total link bandwidth by factors of approximately 1.429 and 1.481. This supports further workload and budget exploration; it does not isolate a resource-free topology advantage.

The stencil's logical rank grid directly matches torus adjacency. Every baseline stencil transfer is a one-hop torus transfer, while sphere rank placement is unoptimized. The large stencil ratios are evidence about this specific placement and workload, not a result against every scientific workload. A spherical-domain or partitioned unstructured-grid workload needs its own trace and placement comparison.

## Resource budgets

Directed bandwidth sums include both full-duplex directions. Wire-length totals are model geometry, not a package routing estimate.

| Ranks | Sphere / torus undirected links | `equal_total` sphere / torus Gbps per direction per link | Matched aggregate directed Gbps | `per_link` sphere / torus aggregate directed Gbps | Sphere / torus modeled wire length (mm) |
|---:|---|---|---:|---|---|
| 42 | 120 / 84 | 179.2 / 256 | 43,008 | 61,440 / 43,008 | 2,836.54 / 336 |
| 162 | 480 / 324 | 172.8 / 256 | 165,888 | 245,760 / 165,888 | 5,769.13 / 1,296 |

Equal aggregate bandwidth does not equalize per-port bandwidth, wiring, PHY area, power, package feasibility, or total cost. Sphere links use surface arcs at a 40 mm radius; torus links are assumed to be 4 mm each. Neither policy is a complete hardware cost normalization.

## Static single-link failure sensitivity

Ratios below compare each candidate's pre-failed p99 with its own baseline on the identical traffic trace.

| Workload | Ranks | Bandwidth policy | Sphere failed/baseline p99: median [min, max] | Torus failed/baseline p99: median [min, max] |
|---|---:|---|---|---|
| MoE dispatch | 42 | `equal_total` | 1.000 [1.000, 1.003] | 1.000 [0.957, 1.009] |
| MoE dispatch | 42 | `per_link` | 1.000 [1.000, 1.000] | 1.000 [0.957, 1.009] |
| MoE dispatch | 162 | `equal_total` | 1.000 [1.000, 1.016] | 1.000 [1.000, 1.014] |
| MoE dispatch | 162 | `per_link` | 1.000 [1.000, 1.053] | 1.000 [1.000, 1.014] |
| 2D stencil | 42 | `equal_total` | 1.000 [1.000, 1.000] | 1.000 [1.000, 3.000] |
| 2D stencil | 42 | `per_link` | 1.000 [1.000, 1.000] | 1.000 [1.000, 3.000] |
| 2D stencil | 162 | `equal_total` | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |
| 2D stencil | 162 | `per_link` | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |

The sampled failures leave these graphs connected. Many p99 values are unchanged because the affected route or fraction of packets does not alter the 99th percentile. A sampled 42-rank stencil torus case triples p99 after rerouting. Some MoE torus samples slightly reduce p99 because changed deterministic BFS routes redistribute traffic; that is not evidence that failures improve real hardware. The JSON preserves the exact failed edge for every case.

## Interpretation and next evidence

The model uses chronological store-and-forward packet events, independent directed links, infinite output queues, shortest-hop BFS, and fixed rank-to-node mapping. It omits compute, actual HBM requests, memory-controller and endpoint injection limits, finite-buffer backpressure, VC credits, protocol overhead, precision/quality, and thermal/system power. Short synthetic traces and five seeds do not establish general application tail latency or reliability guarantees.

Before selecting a physical topology, add real application traces with compute phases, memory-locality and controller budgets; compare optimized placements under matched physical cost; and measure numerical correctness and complete application step latency. The new tiny decoder supplies numerical and logical-ownership checks, not calibrated HBM or multi-device performance. Evaluate the proposed hierarchical/local-memory hardware separately. Tests for this baseline and this sweep remain evidence about its infinite-output-queue communication model.
