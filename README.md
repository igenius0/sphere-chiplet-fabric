[![Historical DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19918238.svg)](https://doi.org/10.5281/zenodo.19918238) (original April 2026 archive)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

# Spherical Chiplet Fabric — iGenius accelerator research

**Corrected publication revision v1.1.0, 9 November 2026.** Geodesic microprocessor research, a reproducible fabric workbench, and a proposed **compute-near-HBM modular AI accelerator**.

The recommended next design uses four local compute/HBM islands, explicit NUMA memory ownership and a budgeted hierarchical network. Geodesic connectivity and spherical packaging are candidates to validate through application, electrical and thermal evidence. The implemented software supplies exploratory models and regression tests; the proposed accelerator still needs its runtime, RTL and physical implementation.

Start with the **[revised paper](paper/main.pdf)**, [publication revision and evidence map](docs/PUBLICATION_REVISION.md), [architecture recommendation](docs/DESIGN_PROPOSAL.md), [application design](docs/APPLICATION_DESIGN.md), [physical design](docs/PHYSICAL_DESIGN.md), and [validation plan](docs/VALIDATION_PLAN.md). The [changelog](CHANGELOG.md) records corrections to the original claims.

## Rebuild the publication

Python 3.10+; no GPU required. From the repository root:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/rebuild_publication.py
python scripts/rebuild_publication.py --check
```

The `--check` mode verifies the current generated bundle; rerun it after changing sources or publication inputs. The explicit rebuild refreshes the current root JSON reports, the 40-profile workload sweep, eight PNG/PDF figure pairs, generated LaTeX result tables and a [publication manifest](artifacts/publication_manifest.json). To reproduce into a separate directory, use `python scripts/rebuild_publication.py --output-root /absolute/directory`. The manifest records exact source SHA-256 fingerprints, configurations, dependency versions and generated-output hashes; it does not substitute an earlier Git commit for uncommitted source.

With a LaTeX installation, build and verify the revised paper from the repository root:

```bash
python scripts/build_paper.py
python scripts/build_paper.py --check
```

The committed `paper/main.pdf` is the rendered paper. The data rebuild and paper build are separate steps. The paper builder runs the required LaTeX passes with deterministic settings and writes [paper/publication_manifest.json](paper/publication_manifest.json), recording the exact paper sources, generated tables, citation metadata, current figure PDFs, model-manifest and build-script hashes, the PDF hash and LaTeX engine information. Run both `--check` commands after publication inputs change. Direct `pdflatex` compilation is possible, but does not refresh the paper manifest; use the builder to produce an attributed publication bundle.

## Run individual experiments

```bash
python -m sim.run_workloads --config examples/moe_dispatch.json --output generated/moe.json
python -m sim.run_workloads --config examples/stencil.json --output generated/stencil.json
python -m sim.run_sweep --output generated/design_sweep.json
python -m sim.performance_model --output generated/capacity_bounds.json
python -m sim.run_evaluation --output-dir generated/evaluation
```

Direct execution such as `python sim/run_workloads.py --help` also works. The individual evaluation command writes JSON and eight PNG/PDF plots beneath the chosen output directory. Commands are deterministic for a fixed configuration and numerical environment; percentile rounding may differ across dependency versions.

The workload CLI supports 42 or 162 equal ranks, aggregate injection rate, packet size, fixed seeds, static link failures and two bandwidth policies. `equal_total` matches the baseline sum of directed link capacities; `per_link` gives each link the same rate. Resource reports expose links, bandwidth and wiring length. Wiring, area and power are not normalized by these policies.

## What the current experiments show

The committed [40-profile, five-seed sensitivity study](docs/EXPERIMENT_RESULTS.md) includes 80,000 packet attempts across both candidates and pre-failed scenarios. The current flat sphere has higher MoE p99 under equal total bandwidth in the tested cohorts. With equal per-link bandwidth, sphere has lower MoE p99 while using approximately 43–48% more aggregate network bandwidth. The periodic stencil maps directly onto torus wiring and favours that placement. These results do not test the proposed local-memory hierarchy or establish end-to-end application performance.

The reference capacity model exposes an important bound: **30 TB/s of assumed local HBM stack bandwidth versus at most 4.8 TB/s through the memory tiles' outward fabric ports**. Compute/HBM locality must be designed before adding more compute or changing topology. The capacity values and component power subtotal are analytical assumptions, not measured hardware performance or full-system energy efficiency.

## Evidence and limitations

The simulator schedules chronological store-and-forward hop events with infinite output queues. It models directed-link serialization, propagation and queueing. Finite buffers, credit/VC arbitration, adaptive escape routing, endpoint shared-injection, HBM transactions, thermal behaviour and physical link feasibility require further work. Batch concurrent traffic must use `FabricSim.send_batch()`; `send_packet()` retains incremental reservations.

See the [research review](docs/RESEARCH_REVIEW.md) and [independent adversarial review](docs/ADVERSARIAL_REVIEW.md) for corrected defects, superseded claims and remaining validation gaps. GitHub Actions runs the regression suite on Python 3.10 and 3.12. Successful software checks do not validate silicon, cooling or inference accuracy.

## Publication history and citation

**Author:** Musthafa Ali, Independent Researcher, Sydney, Australia.

The current [paper](paper/main.pdf), [LaTeX source](paper/main.tex), `figures/` and `artifacts/` contain the corrected revision. The original April 2026 bundle remains accessible at [Git tag v1.0.1](https://github.com/igenius0/sphere-chiplet-fabric/tree/v1.0.1) and its [existing Zenodo DOI](https://doi.org/10.5281/zenodo.19918238). That DOI identifies the historical archive; this repository update does not create or claim a new Zenodo deposit or DOI.

The corrected paper replaces the original timing, bisection, workload-ranking and efficiency presentation with reproducible model results and explicit limitations. Its changes do not retroactively validate the original claims. Use [CITATION.cff](CITATION.cff) for current repository citation metadata and the original DOI only when citing that archived record. Code and figures are MIT-licensed; paper text is CC-BY 4.0.

Contact: **mali@outlook.com.au**.
