# Changelog

## v1.2.0 — 10 November 2026

Add executable software and RTL prototypes for the proposed compute-near-HBM accelerator, with separate evidence and publication attribution. The architecture remains a hardware proposal; the tiny numerical path runs on one CPU with four logical island owners.

- Add a clocked finite-buffer flit model with bounded input/VC queues, delayed credits, endpoint limits, fair arbitration, sink backpressure, explicit cutoffs and a fixed-route channel-dependency certificate.
- Add a tiny synthetic-weight NumPy causal MoE decoder with prefill/decode KV cache, actual expert computation, dispatch/combine byte accounting and an independent full-prefix numerical reference. An optional reliable contribution path tests actual expert payload recovery and cache noncommit after uncertain delivery.
- Add a bounded CRC32/ACK/retry transport reference with duplicate suppression, generation barriers, resource release and explicit uncertain sender outcomes after lost acknowledgments. Its logical ticks are separate from network cycles.
- Add synthesizable synchronous FIFO and local word-fabric RTL, actual Icarus simulations, independent cycle oracles and generic Yosys synthesis. This is a local-switch baseline rather than a hardware realization of the multi-hop Python flit model.
- Add strict physical-measurement assessment and documented coverage requirements. Missing or synthetic inputs cannot establish measured BER, voltage, power or thermal acceptance.
- Add `scripts/run_prototype_validation.py`, committed prototype/RTL reports, generated paper macros and a separate exact-source/output manifest. Extend CI and the paper builder to include this evidence alongside the baseline experiment bundle.
- Synchronize the paper, citation metadata and technical documents with the implemented scope and remaining gates. The eight baseline plot pairs and sensitivity-study numbers remain results of the separate infinite-output-queue model; they do not validate the new finite-buffer model or proposed hierarchy.

The [implementation status](docs/IMPLEMENTATION_STATUS.md) and [independent validation](docs/INDEPENDENT_VALIDATION.md) define the tested contracts. No trained-model quality, multi-device or real-HBM performance, FPGA-board operation, timing closure, formal RTL proof, measured PHY/BER/power/thermal result or full-system efficiency is claimed. The existing DOI continues to identify only the original historical archive.

## v1.1.0 — 9 November 2026

Corrected research-software and publication revision. The main paper, current root figures and JSON reports, generated LaTeX tables, README, technical documents and citation metadata now describe the same corrected model and evidence. The four-island compute-near-HBM architecture is a proposed next design, version v0.2.

### Correctness and reproducibility

- Process competing packet hops chronologically; isolate unloaded packet probes.
- Validate endpoints, topology dimensions/connectivity, collective inputs, numeric range and timestamp resolution.
- Use physical chord lengths for straight chords and report explicit resource budgets.
- Add import-safe, caller-directed experiment CLIs, seeded MoE/stencil proxies and a paired five-seed, 40-profile sensitivity study.
- Add independent analytic, adversarial, CLI and model regression checks plus Python 3.10/3.12 GitHub Actions.
- Add `scripts/rebuild_publication.py` to refresh current data, eight plot pairs, generated paper tables and the model/data publication manifest; `--check` verifies the bundle.
- Add `scripts/build_paper.py` for deterministic LaTeX compilation and a separate paper manifest with exact source/PDF hashes and compiler information; its `--check` verifies paper attribution.

### Original claims and corrected v1.1.0 treatment

| Earlier presentation | Treatment at v1.1.0 |
|---|---|
| Event-driven simulation described as cycle accurate | Exploratory chronological store-and-forward model with infinite queues; no finite-buffer/credit/VC router |
| Simultaneous burst described as unloaded/uncontended latency | Every isolated probe resets link reservations; concurrent traffic uses chronological batch events |
| Submission order could reserve downstream links ahead of earlier-arriving traffic | Downstream link service follows hop-arrival order, with explicit simultaneous-tie ordering |
| Different rank counts or undeclared aggregate link budgets | Equal-rank comparisons with both equal-total and equal-per-link policies; extra links and wiring remain visible |
| Chosen coordinate cut described as bisection | One balanced coordinate-median cut; no minimum-bisection claim |
| Ring chunks and tree full messages compared as equal payload | Match full per-rank payload and state ideal schedule limitations |
| Uniform 30 TB/s access implied from summed HBM stacks | Separate assumed local stack sum from the optimistic 4.8 TB/s remote-memory endpoint ceiling |
| 56.5 PFLOPS and 900 GB read as implemented product specifications | Hypothetical reference-design capacity assumptions, not achieved hardware performance or uniformly accessible memory |
| Approximately 2.3 kW and efficiency/competitor rankings | Component subtotal only, with missing HBM/system terms; full-system power and efficiency remain unknown |
| Mixed precision/sparsity or memory technologies in workload rankings | Precision-matched analytical Roofline ceilings; unsupported competitor/workload-ranking plots removed |
| Nanosecond packet and ideal collective timings read as application performance | Synthetic communication evidence with stated payloads, offered load and omissions; no tokens/s or inference claim |
| Adaptive/escape routing, frame protocols and recovery interpreted as implemented | Explicit future protocol, RTL and validation requirements; coordinate relabeling does not move silicon or create bandwidth |

### Publication and history

The current `paper/main.tex`, `paper/main.pdf`, `figures/` and `artifacts/` replace the stale root publication assets with the corrected revision. See [publication revision and evidence map](docs/PUBLICATION_REVISION.md) for reproduction, attribution and evidence boundaries.

The original April 2026 bundle remains at [v1.0.1](https://github.com/igenius0/sphere-chiplet-fabric/tree/v1.0.1) and [DOI 10.5281/zenodo.19918238](https://doi.org/10.5281/zenodo.19918238). That DOI is the historical archive; no new Zenodo record or DOI is claimed for this repository revision.

## v1.0.1 — original publication

Original paper, source, figures, simulation outputs and DOI/citation metadata. Its affected timing, workload-ranking and efficiency claims are superseded by the v1.1.0 repository revision and retain their historical evidence limitations.
