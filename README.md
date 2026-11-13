[![Historical DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19918238.svg)](https://doi.org/10.5281/zenodo.19918238) (original April 2026 archive)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

# Spherical Chiplet Fabric — iGenius accelerator research

**Prototype and publication revision v1.2.0, 10 November 2026.** Geodesic microprocessor research, reproducible network experiments, and executable prototypes for a proposed **compute-near-HBM modular AI accelerator**.

The design study uses four logical compute/memory islands, explicit ownership and a hierarchical network. This revision adds a bounded flit/credit model, a tiny actual CPU MoE decoder, a fault-injectable transport reference, synthesizable four-endpoint RTL, and a measurement-validation harness. These are scoped reference implementations. FPGA board operation, real HBM/PHY performance, power/cooling measurements and manufactured hardware remain outstanding.

See the **[implementation status](docs/IMPLEMENTATION_STATUS.md)** for exactly what is implemented, what has been tested, and what evidence is still missing. The [independent validation](docs/INDEPENDENT_VALIDATION.md) describes checks that do not rely on the implementation's own assertions.

Start with the **[revised paper](paper/main.pdf)**, [publication revision and evidence map](docs/PUBLICATION_REVISION.md), [architecture recommendation](docs/DESIGN_PROPOSAL.md), [application design](docs/APPLICATION_DESIGN.md), [physical design](docs/PHYSICAL_DESIGN.md), and [validation plan](docs/VALIDATION_PLAN.md). The [changelog](CHANGELOG.md) records corrections to the original claims.

## Next hardware iteration

The [four-island execution plan](docs/FOUR_ISLAND_EXECUTION_PLAN.md) and
[segmented external-cooling experiment](docs/EXTERNAL_COOLING_EXPERIMENT.md)
define architecture study v0.3 (13 November 2026). They specify separate FPGA
and heater demonstrators, a planar reference and the evidence needed before
selecting shell packaging. These are prospective plans; the publication and
measured-hardware status remain v1.2.0 as described above.

## Rebuild the publication

Python 3.10+; no GPU required. From the repository root:

```bash
python -m pip install -r requirements.txt
python scripts/rebuild_publication.py
python scripts/run_prototype_validation.py
python scripts/rebuild_publication.py --check
python scripts/run_prototype_validation.py --check
python scripts/build_paper.py
python -m unittest discover -s tests -v
```

The prototype rebuild requires **Icarus Verilog** (`iverilog`, `vvp`) and **Yosys** on `PATH`; the paper build requires `pdflatex`. Reading the committed JSON or running the three builders' `--check` modes does not require the hardware tools or TeX. Individual numerical inference uses NumPy. CI installs the external tools and executes the RTL checks instead of relying on optional local skips.

The baseline rebuild refreshes the current root JSON reports, 40-profile workload sweep, eight PNG/PDF figure pairs, generated tables and [model manifest](artifacts/publication_manifest.json). The prototype runner refreshes [prototype evidence](artifacts/prototype_validation.json), [RTL evidence](artifacts/rtl_validation.json), generated prototype macros and a separate [prototype manifest](artifacts/prototype_manifest.json). Both evidence builders accept `--output-root /absolute/directory` for isolated reproduction. Manifests record exact source bytes, inputs, dependency/tool versions and output hashes; they do not attribute uncommitted changes to an earlier Git commit.

With a LaTeX installation, build and verify the revised paper from the repository root:

```bash
python scripts/build_paper.py
python scripts/build_paper.py --check
```

The committed `paper/main.pdf` is the rendered paper. Its [manifest](paper/publication_manifest.json) ties the exact paper, baseline/prototype tables, citation metadata, figure PDFs, both evidence manifests and build script to the PDF hash and TeX version. Run all three builders' `--check` modes after changing inputs. Direct `pdflatex` is useful for drafting; use the builder to refresh the paper's provenance.

## Run the new prototypes

```bash
python -m sim.moe_inference --config examples/moe_inference.json --output generated/inference.json
python -m sim.transport --output generated/transport.json
python -m sim.physical_validation --manifest examples/measurements/missing_hardware.json --output generated/physical.json
python scripts/run_rtl_validation.py --output generated/rtl.json
```

| Component | Implemented scope | Detail |
|---|---|---|
| Finite network | Per-input/VC flit buffers, delayed credits, bounded endpoints, fair arbitration, explicit cutoffs and fixed-route dependency certificate | [Model contract](docs/FINITE_BUFFER_MODEL.md) |
| Tiny inference | Actual synthetic-weight causal attention, KV cache, top-k experts and combine on one CPU with four logical owners; independent full-prefix reference and actual expert-payload fault recovery | [Inference prototype](docs/INFERENCE_PROTOTYPE.md) |
| Transport | CRC32, bounded retries, duplicate suppression, generation aborts and explicit uncertain delivery after ACK loss | [Protocol reference](docs/TRANSPORT_PROTOCOL.md) |
| RTL | Synchronous buffers and fair four-endpoint word fabric; simulation and generic synthesis | [RTL prototype](docs/RTL_PROTOTYPE.md) |
| Physical evidence | Strict BER, voltage, power, temperature and coverage assessment of supplied measurements | [Measurement protocol](docs/PHYSICAL_VALIDATION_PROTOCOL.md) |

The supplied missing-hardware manifest returns `INSUFFICIENT_EVIDENCE` with expected CLI exit status 2. Synthetic fixtures are never evidence of measured hardware. CRC32 is accidental-error detection, not authentication; the Python transport, flit model and RTL switch have separate documented timing/architecture contracts. In the optional reliable inference path, an uncertain expert contribution prevents token combine, accepted logits and KV-cache commit.

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

The original `FabricSim` used by the eight publication plots remains an infinite-output-queue store-and-forward baseline. `sim.finite_buffer` is a separate clocked, bounded-input model; its fixed spanning-tree routes leave non-tree links unused, and its per-flit metadata does not implement wormhole header/VC allocation or adaptive routing. Its dependency certificate applies to those fixed routes. Batch baseline traffic uses `FabricSim.send_batch()`; `send_packet()` retains incremental reservations.

GitHub Actions runs Python 3.10/3.12 regressions, actual RTL simulation/synthesis, prototype rebuilds and paper compilation. Numerical reference agreement applies to the tiny synthetic decoder; it does not establish pretrained model quality, production inference performance, FPGA operation or physical feasibility. See the [research review](docs/RESEARCH_REVIEW.md), [adversarial review](docs/ADVERSARIAL_REVIEW.md) and [validation plan](docs/VALIDATION_PLAN.md).

## Publication history and citation

**Author:** Musthafa Ali, Independent Researcher, Sydney, Australia.

The current [paper](paper/main.pdf), [LaTeX source](paper/main.tex), `figures/` and `artifacts/` contain the corrected revision. The original April 2026 bundle remains accessible at [Git tag v1.0.1](https://github.com/igenius0/sphere-chiplet-fabric/tree/v1.0.1) and its [existing Zenodo DOI](https://doi.org/10.5281/zenodo.19918238). That DOI identifies the historical archive; this repository update does not create or claim a new Zenodo deposit or DOI.

The corrected paper replaces the original timing, bisection, workload-ranking and efficiency presentation with reproducible model results and explicit limitations. Its changes do not retroactively validate the original claims. Use [CITATION.cff](CITATION.cff) for current repository citation metadata and the original DOI only when citing that archived record. Code and figures are MIT-licensed; paper text is CC-BY 4.0.

Contact: **mali@outlook.com.au**.
