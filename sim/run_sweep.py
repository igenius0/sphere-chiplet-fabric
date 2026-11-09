"""Paired-seed sensitivity study of the existing flat sphere/torus models.

This does not implement or evaluate the proposed hierarchical accelerator.
Run ``python -m sim.run_sweep --output examples/results/design_sweep.json``.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
from statistics import median
import sys

import numpy as np

try:
    from .workloads import WorkloadConfig, compare_workloads
except ImportError:
    from workloads import WorkloadConfig, compare_workloads


DEFAULT_SEEDS = (7, 19, 42, 73, 101)


def _provenance():
    """Fingerprint the numerical environment and source used for this run.

    Paths are resolved beside this module, independently of the caller's cwd.
    The runner's own bytes can be hashed without embedding a commit hash into
    the file that the commit would in turn need to identify.
    """
    source_directory = Path(__file__).resolve().parent
    source_names = ("fabric.py", "topology.py", "validation.py", "workloads.py", "run_sweep.py")
    return {
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "source_hash_algorithm": "sha256",
        "source_sha256": {
            "sim/" + name: hashlib.sha256((source_directory / name).read_bytes()).hexdigest()
            for name in source_names
        },
    }


def _range(values):
    """Descriptive spread only; these are not confidence intervals."""
    values = [value for value in values if value is not None]
    return {"median": median(values), "min": min(values), "max": max(values)} if values else None


def _ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator else None


def _compact_candidate(candidate):
    keys = ("generated_packets", "delivered_packets", "unreachable_packets", "delivery_fraction",
            "mean_latency_ns", "p50_latency_ns", "p99_latency_ns", "mean_hops")
    return {
        "candidate": candidate["candidate"],
        "topology": candidate["topology"],
        "resource_budget": candidate["resource_budget"],
        "baseline": {key: candidate["baseline"][key] for key in keys},
        "failure": {
            "removed_undirected_links": candidate["failure"]["removed_undirected_links"],
            "resource_budget": candidate["failure"]["resource_budget"],
            "metrics": {key: candidate["failure"]["metrics"][key] for key in keys},
            "p99_latency_ratio_vs_baseline": candidate["failure"]["p99_latency_ratio_vs_baseline"],
        },
    }


def run_sweep(n_packets=500, seeds=DEFAULT_SEEDS):
    """Return eight workload/size/budget cohorts, with paired runs per seed.

    Each profile uses exactly the same rank traffic in sphere/torus and their
    pre-failed cases. Seeds vary traffic and the statically removed links.
    """
    if isinstance(n_packets, bool) or not isinstance(n_packets, int) or not 1 <= n_packets <= 2000:
        raise ValueError("n_packets must be an integer between 1 and 2000")
    if isinstance(seeds, (str, bytes)):
        raise ValueError("seeds must be a sequence of unique integers")
    try:
        seeds = tuple(seeds)
    except TypeError as error:
        raise ValueError("seeds must be a sequence of unique integers") from error
    if not 1 <= len(seeds) <= 10:
        raise ValueError("provide between 1 and 10 seeds")
    for seed in seeds:
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
            raise ValueError("seeds must be integers between 0 and 4294967295")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be unique")
    profiles, cohorts = [], []
    for workload in ("moe_dispatch", "stencil"):
        for subdivisions in (1, 2):
            for policy in ("equal_total", "per_link"):
                paired = []
                for seed in seeds:
                    config = WorkloadConfig(workload=workload, subdivisions=subdivisions,
                                            bandwidth_policy=policy, n_packets=n_packets, seed=seed,
                                            packet_size_flits=16, injection_rate_pkts_per_ns=0.1,
                                            failed_links=1)
                    report = compare_workloads(config)
                    profile = {
                        "config": report["config"],
                        "rank_count": report["workload_summary"]["rank_count"],
                        "candidates": [_compact_candidate(candidate) for candidate in report["candidates"]],
                    }
                    profiles.append(profile)
                    paired.append(profile)
                baseline, failure, unreachable = {}, {}, {}
                for index, candidate in enumerate(("sphere", "torus")):
                    baseline[candidate + "_p99_latency_ns"] = _range(
                        profile["candidates"][index]["baseline"]["p99_latency_ns"] for profile in paired)
                    failure[candidate + "_p99_latency_ns"] = _range(
                        profile["candidates"][index]["failure"]["metrics"]["p99_latency_ns"] for profile in paired)
                    failure[candidate + "_p99_ratio_vs_own_baseline"] = _range(
                        profile["candidates"][index]["failure"]["p99_latency_ratio_vs_baseline"] for profile in paired)
                    unreachable[candidate + "_baseline"] = sum(
                        profile["candidates"][index]["baseline"]["unreachable_packets"] for profile in paired)
                    unreachable[candidate + "_failure"] = sum(
                        profile["candidates"][index]["failure"]["metrics"]["unreachable_packets"] for profile in paired)
                baseline["paired_sphere_over_torus_p99_ratio"] = _range(
                    _ratio(profile["candidates"][0]["baseline"]["p99_latency_ns"],
                           profile["candidates"][1]["baseline"]["p99_latency_ns"]) for profile in paired)
                failure["paired_sphere_over_torus_p99_ratio"] = _range(
                    _ratio(profile["candidates"][0]["failure"]["metrics"]["p99_latency_ns"],
                           profile["candidates"][1]["failure"]["metrics"]["p99_latency_ns"]) for profile in paired)
                cohorts.append({
                    "workload": workload, "rank_count": paired[0]["rank_count"],
                    "bandwidth_policy": policy, "seeds": list(seeds),
                    "resource_budgets": {candidate["candidate"]: candidate["resource_budget"]
                                         for candidate in paired[0]["candidates"]},
                    "baseline": baseline, "failure": failure,
                    "total_unreachable_packets": unreachable,
                })
    return {
        "schema_version": 1,
        "experiment": "existing_flat_topology_paired_seed_sensitivity",
        "provenance": _provenance(),
        "settings": {
            "seeds": list(seeds), "n_packets_per_profile": n_packets,
            "packet_size_flits": 16, "payload_bytes_per_packet": 1024,
            "aggregate_injection_rate_pkts_per_ns": 0.1,
            "aggregate_offered_payload_gbps": 819.2,
            "failed_undirected_links_per_candidate": 1,
            "profile_count": len(profiles), "cohort_count": len(cohorts),
            "total_packet_attempts": len(profiles) * n_packets * 4,
        },
        "method": {
            "evidence": "exploratory synthetic communication simulation, not hardware measurements",
            "pairing": "each seed shares rank pairs, injection times and packet size across both candidates and failure scenarios",
            "spread": "median and minimum/maximum across seeds; not confidence intervals or a guarantee over traffic distributions",
            "ratio": "sphere p99 / torus p99 calculated within each seed, then summarized; values above 1 mean higher sphere latency",
            "bandwidth": "equal_total matches baseline sum of directed capacities; per_link matches each link rate, leaving sphere more total links and bandwidth",
            "failure": "one seeded undirected link removed before traffic; no live detection, retry or failover tested",
            "stencil_bias": "fixed logical periodic grid directly matches torus adjacency; sphere placement is not optimized",
            "mapping": "rank IDs map directly to node IDs; results depend on placement, especially hotspot location",
            "physical": "sphere surface arcs and configured torus link lengths differ; wire-length, power, package and pin costs are not equalized",
            "scope": "only the existing flat sphere and torus models; proposed hierarchical/co-located-memory design is not implemented or tested",
            "omissions": "no compute, HBM transactions, finite buffers, VC credits, protocol overhead, full-system power, or end-to-end application SLO",
        },
        "cohorts": cohorts,
        "profiles": profiles,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the complete deterministic JSON report")
    parser.add_argument("--n-packets", type=int, default=500, help="packets per profile; 1 to 2000 (default: 500)")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS),
                        help="1 to 10 unique seeds; default: 7 19 42 73 101")
    parser.add_argument("--json", action="store_true", help="print complete JSON instead of a summary")
    args = parser.parse_args(argv)
    try:
        report = run_sweep(args.n_packets, args.seeds)
        serialized = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
    except (OSError, ValueError, TypeError, OverflowError) as error:
        parser.error(str(error))
    if args.json:
        sys.stdout.write(serialized)
    else:
        print(f"{report['settings']['profile_count']} synthetic paired-seed profiles; "
              f"{report['settings']['total_packet_attempts']} packet attempts.")
        print("Sphere/torus p99 ratios: median [min, max] across seeds; not confidence intervals.")
        print(f"{'Workload':<13} {'Ranks':>5} {'Bandwidth':<12} {'Baseline ratio':>25} {'Pre-failed ratio':>25}")
        for cohort in report["cohorts"]:
            def display(stats):
                return f"{stats['median']:.3f} [{stats['min']:.3f}, {stats['max']:.3f}]" if stats else "n/a"
            print(f"{cohort['workload']:<13} {cohort['rank_count']:>5} {cohort['bandwidth_policy']:<12} "
                  f"{display(cohort['baseline']['paired_sphere_over_torus_p99_ratio']):>25} "
                  f"{display(cohort['failure']['paired_sphere_over_torus_p99_ratio']):>25}")
        print("Current flat models only; stencil placement favors torus. Proposed hierarchical design is untested.")
        if args.output:
            print(f"JSON report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
