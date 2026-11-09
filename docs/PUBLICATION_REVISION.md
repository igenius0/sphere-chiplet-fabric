# Corrected publication revision v1.1.0

Revision date: **9 November 2026**. Author: **Musthafa Ali**, Independent Researcher, Sydney, Australia. This revision synchronizes the repository paper, generated figures and reports, documentation and citation metadata with the corrected exploratory simulator. The proposed compute-near-HBM architecture remains design v0.2 and has not been implemented as hardware or a complete inference runtime.

## Current publication and evidence map

| Current file or directory | Meaning and evidence scope | How it is produced |
|---|---|---|
| [paper/main.tex](../paper/main.tex) and [paper/main.pdf](../paper/main.pdf) | Revised research paper: implemented model, current simulation evidence, analytical bounds and proposed next architecture | LaTeX source and rendered PDF; compile after rebuilding data |
| [paper/publication_manifest.json](../paper/publication_manifest.json) | Exact paper/input and output attribution: LaTeX source, generated result tables, citation metadata, figure PDFs, model manifest, build script, rendered PDF hash and LaTeX engine information | `scripts/build_paper.py` |
| [paper/generated_results.tex](../paper/generated_results.tex) | Values/tables derived directly from the committed JSON reports | `scripts/rebuild_publication.py` |
| [artifacts/results.json](../artifacts/results.json) | Flat sphere, chorded sphere and torus evaluation at 162 nodes, 2,000 packet pairs, seed 42, equal aggregate directed bandwidth | Corrected event-driven evaluator |
| [examples/results/design_sweep.json](../examples/results/design_sweep.json) | 40 paired profiles across five seeds: MoE/stencil, 42/162 ranks, both bandwidth policies and static link-removal sensitivity | Corrected synthetic workload sweep |
| [artifacts/performance_model.json](../artifacts/performance_model.json) | Assumed dense-FP8/local-HBM capacity ceilings, remote-memory endpoint bounds and incomplete component power subtotal | Analytical capacity model; no measured processor or competitor ranking |
| [figures/](../figures) | Eight current PNG/PDF plot pairs generated from the flat-topology evaluation | Corrected evaluator and publication rebuild |
| [artifacts/publication_manifest.json](../artifacts/publication_manifest.json) | Rebuild environment, exact model-source fingerprints, configurations and hashes of generated outputs | Publication rebuild; excludes a self-referential manifest hash |
| [CITATION.cff](../CITATION.cff) | Current repository revision metadata | Reviewed metadata; the existing DOI is identified as a historical archive |
| [CHANGELOG.md](../CHANGELOG.md) | Changes from the original publication and unsupported claims superseded | Human-reviewed revision record |

The model/data manifest attributes generated evidence to exact source-file contents. A Git commit identifies a saved repository state after commit; it must not be used to imply that a prior commit includes later uncommitted changes. That manifest covers generated data, figure and table artifacts. The separate paper manifest ties the exact LaTeX sources, result tables, citation metadata, current figure PDFs, model manifest and build script to the rendered PDF and compiler information. Paper text and rendered layout also receive separate review.

## Reproduce and verify

From the repository root:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/rebuild_publication.py
python scripts/rebuild_publication.py --check
```

For an isolated evidence rebuild, use `python scripts/rebuild_publication.py --output-root /absolute/directory`. Inspect the exact recorded environment before byte comparisons; dependency changes can affect percentile rounding or figure bytes. Regeneration writes the current JSON, figures, generated LaTeX tables and model/data manifest. With a LaTeX installation, build the PDF and its separate attribution manifest:

```bash
python scripts/build_paper.py
python scripts/build_paper.py --check
```

The paper builder runs the required LaTeX passes with deterministic settings and records input/output hashes and compiler information in `paper/publication_manifest.json`. Rendered pages still need visual inspection. Direct `pdflatex` compilation can be used for local drafting, but does not refresh this manifest; rerun the builder for an attributed publication bundle. Run both the model/data and paper `--check` commands after changing any publication inputs.

The workload tables in [EXPERIMENT_RESULTS.md](EXPERIMENT_RESULTS.md) describe five-seed medians and observed ranges. Their 500-packet profiles are distinct from the 2,000-packet flat-topology evaluation. Comparing those reports requires their exact configuration, bandwidth policy and traffic assumptions.

## What the revised evidence supports

- Chronological store-and-forward scheduling, link serialization and directed-link contention in an infinite-output-queue model; isolated probes reset link state.
- Seeded synthetic traffic and static reachability sensitivity under explicit bandwidth and geometry assumptions. Equal aggregate link bandwidth does not match wire length, port cost, area or power.
- In the tested MoE cohorts, the flat sphere has higher p99 under equal total bandwidth. Equal per-link rates can lower its p99 while granting it more aggregate bandwidth. The stencil placement directly fits the torus.
- A necessary reference-design bandwidth constraint: 30 TB/s of assumed summed local HBM bandwidth cannot be supplied remotely through a 4.8 TB/s memory-port ceiling. Locality or additional ports are required.

The results do not establish fastest-processor status, full-system PFLOPS/kW, general application speed, minimum bisection, finite-buffer deadlock freedom, real inference correctness, live fault recovery, UCIe compliance or thermal/package feasibility. The four-island hierarchy must be evaluated separately after implementation. Proposed milestones remain in the [validation plan](VALIDATION_PLAN.md).

## Original publication and DOI

The April 2026 source, PDF, plots and JSON remain recoverable at [Git tag v1.0.1](https://github.com/igenius0/sphere-chiplet-fabric/tree/v1.0.1) and the original [Zenodo record, DOI 10.5281/zenodo.19918238](https://doi.org/10.5281/zenodo.19918238). The current repository paths contain the corrected revision and supersede the affected earlier results for this codebase. The archive retains its original claims and evidence status.

This work updates the GitHub repository; it does not create a new Zenodo deposit, replace that archived record or mint a new DOI. Cite the current repository revision using `CITATION.cff`; use the original DOI when discussing or citing the historical publication.
