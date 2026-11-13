# Prototype and publication revision v1.2.0

Revision date: **10 November 2026**. Author: **Musthafa Ali**, Independent Researcher, Sydney, Australia. This revision adds executable software and RTL prototypes and synchronizes their evidence with the paper, documentation and citation metadata. The corrected baseline experiments remain separate. The proposed compute-near-HBM architecture remains design v0.2; the tiny decoder executes actual numerical inference on one CPU with logical island ownership, while a production runtime, real HBM/PHY integration and physical hardware remain unvalidated.

## Current publication and evidence map

| Current file or directory | Meaning and evidence scope | How it is produced |
|---|---|---|
| [paper/main.tex](../paper/main.tex) and [paper/main.pdf](../paper/main.pdf) | Revised research paper: baseline results, scoped prototype execution, analytical bounds and proposed hardware architecture | LaTeX source and rendered PDF; compile after rebuilding both evidence bundles |
| [paper/publication_manifest.json](../paper/publication_manifest.json) | Exact paper/input and output attribution: LaTeX source, baseline/prototype tables, citation metadata, figure PDFs, both evidence manifests, build script, rendered PDF hash and LaTeX engine information | `scripts/build_paper.py` |
| [paper/generated_results.tex](../paper/generated_results.tex) | Values/tables derived directly from the committed baseline JSON reports | `scripts/rebuild_publication.py` |
| [paper/prototype_results.tex](../paper/prototype_results.tex) | Values and scope macros derived from the prototype report | `scripts/run_prototype_validation.py` |
| [artifacts/results.json](../artifacts/results.json) | Flat sphere, chorded sphere and torus evaluation at 162 nodes, 2,000 packet pairs, seed 42, equal aggregate directed bandwidth | Corrected event-driven evaluator |
| [examples/results/design_sweep.json](../examples/results/design_sweep.json) | 40 paired profiles across five seeds: MoE/stencil, 42/162 ranks, both bandwidth policies and static link-removal sensitivity | Corrected synthetic workload sweep |
| [artifacts/performance_model.json](../artifacts/performance_model.json) | Assumed dense-FP8/local-HBM capacity ceilings, remote-memory endpoint bounds and incomplete component power subtotal | Analytical capacity model; no measured processor or competitor ranking |
| [figures/](../figures) | Eight current PNG/PDF plot pairs generated from the flat-topology evaluation | Corrected evaluator and publication rebuild |
| [artifacts/publication_manifest.json](../artifacts/publication_manifest.json) | Rebuild environment, exact model-source fingerprints, configurations and hashes of generated outputs | Publication rebuild; excludes a self-referential manifest hash |
| [artifacts/prototype_validation.json](../artifacts/prototype_validation.json) | Bounded network, actual tiny numerical inference and expert-payload fault recovery, transport cases and missing-hardware assessment | Prototype runner; uncalibrated software schedules and no physical measurements |
| [artifacts/rtl_validation.json](../artifacts/rtl_validation.json) | Actual FIFO/word-fabric simulations against independent cycle oracles and generic synthesis, with tool versions and coverage | `scripts/run_rtl_validation.py`, invoked by the prototype runner |
| [artifacts/prototype_manifest.json](../artifacts/prototype_manifest.json) | Exact prototype source/config/test fingerprints, environment and generated-output hashes | Prototype runner; identifies test sources but does not claim to execute the unit suite |
| [docs/IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) and [docs/INDEPENDENT_VALIDATION.md](INDEPENDENT_VALIDATION.md) | Implemented contracts, independent checks and remaining application/hardware gates | Reviewed implementation/evidence records |
| [docs/PHYSICAL_VALIDATION_PROTOCOL.md](PHYSICAL_VALIDATION_PROTOCOL.md) | Measurement format, qualification and coverage requirements | Strict assessment harness; synthetic fixtures are not measured evidence |
| [CITATION.cff](../CITATION.cff) | Current repository revision metadata | Reviewed metadata; the existing DOI is identified as a historical archive |
| [CHANGELOG.md](../CHANGELOG.md) | Changes from the original publication and unsupported claims superseded | Human-reviewed revision record |

The baseline and prototype manifests attribute generated evidence to exact source-file contents. A Git commit identifies a saved repository state after commit; it must not be used to imply that a prior commit includes later uncommitted changes. The baseline manifest covers its data, figure and table artifacts; the prototype manifest covers the additional reports and paper macros. The paper manifest ties the exact LaTeX sources, both generated table/macro files, citation metadata, current figure PDFs, both evidence manifests and build script to the rendered PDF and compiler information. Paper text and rendered layout also receive separate review.

## Reproduce and verify

From the repository root:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/rebuild_publication.py
python scripts/run_prototype_validation.py
python scripts/rebuild_publication.py --check
python scripts/run_prototype_validation.py --check
```

Both evidence builders accept `--output-root /absolute/directory` for isolated reproduction. The prototype rebuild requires Icarus Verilog (`iverilog`, `vvp`) and Yosys on `PATH`; the numerical path requires NumPy. Inspect the exact recorded environment before byte comparisons; dependency or tool changes can affect numerical rounding, figures or synthesis statistics. The two regeneration commands write their separate reports, generated LaTeX inputs and manifests. With `pdflatex` installed, build the PDF and its separate attribution manifest:

```bash
python scripts/build_paper.py
python scripts/build_paper.py --check
```

The paper builder runs the required LaTeX passes with deterministic settings and records input/output hashes and compiler information in `paper/publication_manifest.json`. Rendered pages still need visual inspection. Direct `pdflatex` compilation can be used for local drafting, but does not refresh this manifest; rerun the builder for an attributed publication bundle. Run all three builders' `--check` commands after changing publication inputs. These hash/provenance checks do not rerun the experiments or substitute for unit tests; reading or verifying the saved bundles does not require the hardware tools or TeX.

The workload tables in [EXPERIMENT_RESULTS.md](EXPERIMENT_RESULTS.md) describe five-seed medians and observed ranges. Their 500-packet profiles are distinct from the 2,000-packet flat-topology evaluation. Comparing those reports requires their exact configuration, bandwidth policy and traffic assumptions.

## What the revised evidence supports

- Chronological store-and-forward scheduling, link serialization and directed-link contention in an infinite-output-queue model; isolated probes reset link state.
- Seeded synthetic traffic and static reachability sensitivity under explicit bandwidth and geometry assumptions. Equal aggregate link bandwidth does not match wire length, port cost, area or power.
- In the tested MoE cohorts, the flat sphere has higher p99 under equal total bandwidth. Equal per-link rates can lower its p99 while granting it more aggregate bandwidth. The stencil placement directly fits the torus.
- A necessary reference-design bandwidth constraint: 30 TB/s of assumed summed local HBM bandwidth cannot be supplied remotely through a 4.8 TB/s memory-port ceiling. Locality or additional ports are required.
- A separate bounded network contract: per-input/VC FIFOs, delayed credits, fair arbitration, endpoint backpressure, complete drain/cutoff accounting and a checkable acyclic dependency graph for the actual fixed tree routes. This is not an adaptive/wormhole routing or formal hardware proof.
- Actual tiny causal MoE execution with synthetic weights, KV-cache updates and independent full-prefix numerical agreement. Expert-payload fault cases verify bounded recovery and that uncertain acknowledgment outcomes cannot commit a token's cache or logits. Network cycles, transport ticks and CPU execution are distinct contracts.
- Bounded transport recovery, replay suppression, generation aborts and resource release, with explicit uncertain delivery after ACK exhaustion. CRC32 is accidental-error detection; shared admission state and finite replay history limit the reference's guarantees.
- Actual local word-switch RTL simulation and generic synthesis. These demonstrate a small synthesizable control baseline, not FPGA-board operation, ASIC area/power, timing closure or a realization of the whole Python network.
- Strict assessment of supplied physical measurements. Missing or synthetic inputs yield `INSUFFICIENT_EVIDENCE`; no real instrument data is supplied in this revision.

The eight baseline plot pairs and sensitivity tables retain their infinite-output-queue model and numerical scope; adding prototypes does not validate the proposed hierarchy with those old results. The combined evidence does not establish fastest-processor status, trained-model quality, general application speed, full-system PFLOPS/kW, minimum bisection, arbitrary-router deadlock freedom, physical live fault recovery, UCIe compliance or thermal/package feasibility. Calibrated multi-device/real-HBM performance, FPGA implementation and physical measurements remain gates in the [validation plan](VALIDATION_PLAN.md).

## Original publication and DOI

The April 2026 source, PDF, plots and JSON remain recoverable at [Git tag v1.0.1](https://github.com/igenius0/sphere-chiplet-fabric/tree/v1.0.1) and the original [Zenodo record, DOI 10.5281/zenodo.19918238](https://doi.org/10.5281/zenodo.19918238). The current repository paths contain the corrected revision and supersede the affected earlier results for this codebase. The archive retains its original claims and evidence status.

This work updates the GitHub repository; it does not create a new Zenodo deposit, replace that archived record or mint a new DOI. Cite the current repository revision using `CITATION.cff`; use the original DOI when discussing or citing the historical publication.
