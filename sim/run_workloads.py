"""Compare synthetic application traffic: python -m sim.run_workloads --help."""
import argparse
import json
from pathlib import Path
import sys

try:
    from .workloads import WorkloadConfig, compare_workloads
except ImportError:
    from workloads import WorkloadConfig, compare_workloads


def build_parser():
    parser = argparse.ArgumentParser(description="Compare equal-node sphere/torus application-traffic proxies with reproducible failures.")
    parser.add_argument("--config", type=Path, help="JSON configuration file; command-line values override it")
    parser.add_argument("--subdivisions", type=int, choices=(1, 2), help="1: 42 ranks; 2: 162 ranks")
    parser.add_argument("--workload", choices=("moe_dispatch", "stencil"))
    parser.add_argument("--n-packets", type=int, help="1 to 100000 packets")
    parser.add_argument("--packet-size-flits", type=int, help="1 to 65536 64-byte flits (up to 4 MiB)")
    parser.add_argument("--injection-rate-pkts-per-ns", type=float, help="aggregate injection rate across all ranks")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--failed-links", type=int, help="seeded physical links removed before traffic starts")
    parser.add_argument("--bandwidth-policy", choices=("equal_total", "per_link"))
    parser.add_argument("--expert-hotspot-share", type=float)
    parser.add_argument("--radius-mm", type=float)
    parser.add_argument("--torus-link-length-mm", type=float)
    parser.add_argument("--link-rate-gbps", type=float)
    parser.add_argument("--lanes-per-link", type=int)
    parser.add_argument("--output", type=Path, help="save the complete JSON report")
    parser.add_argument("--json", action="store_true", help="print JSON instead of the comparison table")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    values = {}
    try:
        if args.config:
            values = json.loads(args.config.read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                raise ValueError("configuration must be a JSON object")
        for key in WorkloadConfig.__dataclass_fields__:
            value = getattr(args, key)
            if value is not None:
                values[key] = value
        report = compare_workloads(WorkloadConfig.from_dict(values))
        serialized = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
    except (OSError, ValueError, TypeError, OverflowError) as error:
        parser.error(str(error))
    if args.json:
        sys.stdout.write(serialized)
    else:
        config = report["config"]
        print(f"Synthetic {config['workload']} | {report['workload_summary']['rank_count']} ranks | {config['bandwidth_policy']} bandwidth")
        print("Estimates from a communication model; not hardware or end-to-end application measurements.")
        summary = report["workload_summary"]
        print(f"Aggregate offered load: {summary['aggregate_offered_packets_per_ns']:g} packets/ns "
              f"({summary['aggregate_offered_payload_gbps']:.2f} Gbps payload); "
              f"mean {summary['mean_offered_packets_per_ns_per_rank']:.6g} packets/ns per rank.")
        if config["workload"] == "stencil":
            print("Stencil grid directly matches torus wiring; this fixed placement favors the torus.")
        print(f"{'Candidate':<10} {'Links':>6} {'Gbps/link':>10} {'Delivered':>11} {'p99 ns':>11} {'Failed p99':>12} {'Unreachable':>12}")
        for candidate in report["candidates"]:
            budget = candidate["resource_budget"]
            baseline = candidate["baseline"]
            failure = candidate["failure"]["metrics"]
            def number(value):
                return f"{value:.2f}" if value is not None else "n/a"
            print(f"{candidate['candidate']:<10} {budget['undirected_links']:>6} {budget['link_bandwidth_gbps_per_direction']:>10.2f} "
                  f"{baseline['delivered_packets']:>11} {number(baseline['p99_latency_ns']):>11} "
                  f"{number(failure['p99_latency_ns']):>12} {failure['unreachable_packets']:>12}")
        print("Identical rank traffic; fixed placement. Wiring, packaging and power budgets differ.")
        if args.output:
            print(f"JSON report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
