[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19918238.svg)](https://doi.org/10.5281/zenodo.19918238)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

# Spherical Chiplet Fabric — iGenius accelerator research

Geodesic microprocessor research, a reproducible fabric workbench, and a proposed **compute-near-HBM modular AI accelerator**.

The recommended next design uses local compute/HBM islands, explicit NUMA memory ownership and a budgeted hierarchical network. Geodesic connectivity and spherical packaging are candidates to validate through application, electrical and thermal evidence. The implemented software supplies exploratory models and regression tests; the proposed accelerator still needs its runtime, RTL and physical implementation.

Start with the **[architecture recommendation](docs/DESIGN_PROPOSAL.md)**, [application design](docs/APPLICATION_DESIGN.md), [physical design](docs/PHYSICAL_DESIGN.md), and [validation plan](docs/VALIDATION_PLAN.md).

## Run the workbench

Python 3.10+; no GPU required. From the repository root:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m sim.run_workloads --config examples/moe_dispatch.json --output generated/moe.json
python -m sim.run_workloads --config examples/stencil.json --output generated/stencil.json
python -m sim.run_sweep --output generated/design_sweep.json
python -m sim.performance_model --output generated/capacity_bounds.json
python -m sim.run_evaluation --output-dir generated/evaluation
```

Direct execution such as `python sim/run_workloads.py --help` also works. Evaluation writes JSON and eight PNG/PDF plots beneath the chosen directory. It does not overwrite the historical publication figures. Commands are deterministic for a fixed configuration and numerical environment; percentile rounding may differ across dependency versions.

The workload CLI supports 42 or 162 equal ranks, aggregate injection rate, packet size, fixed seeds, static link failures and two bandwidth policies. `equal_total` matches the baseline sum of directed link capacities; `per_link` gives each link the same rate. Resource reports expose links, bandwidth and wiring length. Wiring, area and power are not normalized by these policies.

## What the current experiments show

The committed [40-profile, five-seed sensitivity study](docs/EXPERIMENT_RESULTS.md) includes 80,000 packet attempts across both candidates and pre-failed scenarios. The current flat sphere has higher MoE p99 under equal total bandwidth in the tested cohorts. With equal per-link bandwidth, sphere has lower MoE p99 while using approximately 43–48% more aggregate network bandwidth. The periodic stencil maps directly onto torus wiring and favours that placement. These results do not test the proposed local-memory hierarchy or establish end-to-end application performance.

The reference capacity model exposes an important bound: **30 TB/s of assumed local HBM stack bandwidth versus at most 4.8 TB/s through the memory tiles' outward fabric ports**. Compute/HBM locality must be designed before adding more compute or changing topology.

## Evidence and limitations

The simulator schedules chronological store-and-forward hop events with infinite output queues. It models directed-link serialization, propagation and queueing. Finite buffers, credit/VC arbitration, adaptive escape routing, endpoint shared-injection, HBM transactions, thermal behaviour and physical link feasibility require further work. Batch concurrent traffic must use `FabricSim.send_batch()`; `send_packet()` retains incremental reservations.

See the [research review](docs/RESEARCH_REVIEW.md) and [independent adversarial review](docs/ADVERSARIAL_REVIEW.md) for corrected defects, historical claim limitations and remaining validation gaps. GitHub Actions runs the regression suite on Python 3.10 and 3.12.

## Historical publication

**Author:** Musthafa Ali, Independent Researcher, Sydney, Australia.

The April 2026 [paper](paper/main.pdf), [LaTeX source](paper/main.tex), `figures/` and `artifacts/` are preserved as the historical publication bundle. Its performance, efficiency and timing tables are unvalidated projections; corrected code does not reproduce or validate all original claims. New output goes to `generated/` unless a different path is requested.

Compile the historical paper with `pdflatex main.tex` twice from `paper/`. Code and figures are MIT-licensed; paper text is CC-BY 4.0. Citation metadata is in [CITATION.cff](CITATION.cff).

Contact: **mali@outlook.com.au**.
