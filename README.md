# Spherical Chiplet Fabric — Publication Bundle

**Paper:** *A Spherical Chiplet Fabric with Geodesic Topology, Planet-Style
Coordinates, and Reorientable Frame Semantics for Large-Scale AI and HPC
Workloads*

**Author:** Musthafa Ali (Independent Researcher, Sydney, Australia)
**Date:** April 2026

This bundle contains everything needed to reproduce the paper's figures,
re-run the simulator, and verify the analytical performance model.

---

## Contents

```
sphere-chiplet-paper/
├── paper/
│   ├── main.tex            # LaTeX source
│   └── main.pdf            # Compiled paper (7 pages)
├── sim/
│   ├── topology.py         # Icosphere / torus / dragonfly generators
│   ├── fabric.py           # Cycle-accurate router + collective simulator
│   ├── run_evaluation.py   # Reproduces all latency / bisection figures
│   └── performance_model.py # Analytical roofline + competitor comparison
├── figures/
│   ├── fig1_hop_cdf.{pdf,png}      # Hop count CDF
│   ├── fig2_latency_cdf.{pdf,png}  # Latency CDF (uncontended)
│   ├── fig3_latency_load.{pdf,png} # Latency vs offered load
│   ├── fig4_tail_ratio.{pdf,png}   # p99/p50 tail ratio
│   ├── fig5_allreduce.{pdf,png}    # Ring vs tree all-reduce
│   ├── fig6_bisection.{pdf,png}    # Bisection bandwidth
│   ├── fig7_hotspot.{pdf,png}      # Hotspot traffic CDF
│   ├── fig8_energy.{pdf,png}       # pJ/bit estimate
│   ├── fig9_workload.{pdf,png}     # Workload throughput projections
│   └── fig10_power.{pdf,png}       # Sphere-162 power breakdown
├── artifacts/
│   ├── results.json                # Raw simulator output
│   └── performance_model.json      # Aggregate / competitor data
├── README.md                       # This file
├── LICENSE                         # MIT
└── CITATION.cff                    # Citation metadata
```

---

## Quick start

### Reproduce all figures

```bash
cd sim/
python3 run_evaluation.py        # produces fig1-fig8
python3 performance_model.py     # produces fig9-fig10 + comparison tables
```

Requires Python 3.9+, NumPy, Matplotlib. No GPU, no special packages.
End-to-end runtime is roughly 30 seconds on a modern laptop.

### Compile the paper

```bash
cd paper/
pdflatex main.tex
pdflatex main.tex   # second pass for cross-references
```

Requires a TeX Live distribution with `texlive-latex-extra` (or equivalent).

---

## Headline results

| Metric | Value |
|---|---|
| FP8 dense throughput | 56.5 PFLOPS |
| HBM3e capacity | 900 GB |
| HBM bandwidth | 30 TB/s |
| Typical power | 2.28 kW |
| Energy efficiency | **24.8 PFLOPS/kW** |
| vs 8×H100 NVLink node | **~5× more efficient** |
| vs single B200 GPU | ~6× compute, ~5× memory |
| Tail latency p99/p50 | 1.96 (sphere) vs 2.23 (torus) |
| Bisection bandwidth | 11.8 Tb/s (1.76× torus at same node count) |
| Tree all-reduce (162 nodes) | 0.43 µs (0.22 µs with chords) |

---

## What this is, and what it isn't

**This is** an architecture proposal evaluated through a custom event-driven
fabric simulator and a roofline-style analytical performance model, in the
tradition of the original dragonfly (Kim et al., ISCA 2008) and HyperX
(Ahn et al., SC 2009) papers.

**This is not** a tape-out, a silicon validation, or an SI/PI-verified
implementation. The paper's Discussion section explicitly enumerates the
validation gaps and what would be required to advance the design to silicon.

Two original claims that did not survive review were removed:

1. *Heat-to-electricity recovery* as a primary feature — Carnot-limited to a
   few percent at the operating temperature; retained only as backup power
   for safety subsystem.
2. *Ring all-reduce supremacy* — naive ring on the icosphere is actually
   slower than torus due to non-Hamiltonian neighbor ordering. The sphere's
   collective advantage is in tree- and bisection-bound operations, not
   ring operations.

---

## License

Code and figures: MIT License (see `LICENSE`).
Paper text: CC-BY 4.0.

---

## Citation

```bibtex
@misc{ali2026sphere,
  title={A Spherical Chiplet Fabric with Geodesic Topology,
         Planet-Style Coordinates, and Reorientable Frame Semantics
         for Large-Scale AI and HPC Workloads},
  author={Ali, Musthafa},
  year={2026},
  note={Independent research, Sydney, Australia.
        Available at: <arxiv-link-here>}
}
```

A `CITATION.cff` is included for GitHub / Zenodo automatic citation parsing.

---

## Contact and feedback

This is independent research published openly to invite scrutiny and
collaboration. Issues, corrections, and replication attempts are welcome.

Contact: **mali@outlook.com.au**
