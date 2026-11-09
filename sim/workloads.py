"""Reproducible application-traffic experiments for the proposed fabric.

These are synthetic communication proxies, not end-to-end application benchmarks.
Ranks have the same ID and placement in both candidates; placement is deliberately
not optimized for either topology. The model does not simulate compute or memory.
"""
from dataclasses import asdict, dataclass, fields
import copy
import math
import random

import numpy as np

try:
    from .fabric import FabricSim, LatencyModel
    from .topology import make_icosphere, make_torus
except ImportError:  # Direct execution from sim/.
    from fabric import FabricSim, LatencyModel
    from topology import make_icosphere, make_torus


@dataclass(frozen=True)
class WorkloadConfig:
    """Bounded exploratory run inputs, not a supported-hardware specification.

    Packet size is capped at 65,536 512-bit flits (4 MiB), and traces at
    100,000 packets. Broad numeric bounds prevent overflow/underflow and keep
    scheduling well-defined even for mistaken units in configuration files.
    """
    subdivisions: int = 1
    workload: str = "moe_dispatch"
    n_packets: int = 2000
    packet_size_flits: int = 16
    injection_rate_pkts_per_ns: float = 0.1
    seed: int = 42
    failed_links: int = 1
    bandwidth_policy: str = "equal_total"
    expert_hotspot_share: float = 0.5
    radius_mm: float = 40.0
    torus_link_length_mm: float = 4.0
    link_rate_gbps: float = 32.0
    lanes_per_link: int = 8

    def __post_init__(self):
        integer_ranges = {
            "subdivisions": (1, 2), "n_packets": (1, 100000),
            "packet_size_flits": (1, 65536), "seed": (0, 2**32 - 1),
            "failed_links": (0, 20), "lanes_per_link": (1, 1024),
        }
        for key, (lower, upper) in integer_ranges.items():
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise ValueError(f"{key} must be an integer between {lower} and {upper}")
        numeric_ranges = {
            "injection_rate_pkts_per_ns": (1e-12, 1e6),
            "radius_mm": (1e-6, 1e6),
            "torus_link_length_mm": (1e-6, 1e6),
            "link_rate_gbps": (1e-6, 1e6),
        }
        for key, (lower, upper) in numeric_ranges.items():
            value = getattr(self, key)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not lower <= value <= upper):
                raise ValueError(f"{key} must be a finite number between {lower:g} and {upper:g}")
        share = self.expert_hotspot_share
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share <= 1:
            raise ValueError("expert_hotspot_share must be a finite number between 0 and 1")
        if self.workload not in ("moe_dispatch", "stencil"):
            raise ValueError("workload must be moe_dispatch or stencil")
        if self.bandwidth_policy not in ("per_link", "equal_total"):
            raise ValueError("bandwidth_policy must be per_link or equal_total")

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict):
            raise ValueError("configuration must be a JSON object")
        unknown = set(values) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError("unknown configuration keys: " + ", ".join(sorted(unknown)))
        return cls(**values)


def make_workload_pairs(config):
    """Return rank pairs, shared unchanged by every candidate and failure case."""
    config = config if isinstance(config, WorkloadConfig) else WorkloadConfig.from_dict(config)
    rows, cols = (6, 7) if config.subdivisions == 1 else (9, 18)
    count = rows * cols
    rng = random.Random(config.seed)
    pairs = []
    # A small popular-expert set models skew. Each packet is one token-batch
    # transfer; gate/top-k selection, return combine, and compute are omitted.
    experts = rng.sample(range(count), max(2, math.ceil(count * 0.1)))
    for index in range(config.n_packets):
        source = rng.randrange(count)
        if config.workload == "moe_dispatch":
            candidates = [rank for rank in experts if rank != source]
            if rng.random() < config.expert_hotspot_share:
                destination = rng.choice(candidates)
            else:
                destination = rng.randrange(count - 1)
                if destination >= source:
                    destination += 1
        else:
            # Four-neighbor periodic 2D halo exchange. This logical rank grid
            # stays fixed even when the physical candidate is the sphere.
            row, col = divmod(source, cols)
            neighbors = [row * cols + (col + 1) % cols,
                         row * cols + (col - 1) % cols,
                         ((row + 1) % rows) * cols + col,
                         ((row - 1) % rows) * cols + col]
            destination = neighbors[index % 4]
        pairs.append((source, destination))
    return pairs


def undirected_edges(topo):
    return sorted((source, destination) for source in topo["nodes"]
                  for destination in topo["adj"][source] if source < destination)


def remove_links(topo, count, seed):
    """Copy the graph and remove a seeded sample of physical links, both ways.

    Failures happen before traffic starts. This measures static reachability and
    route changes, not fault detection, live failover, or retry behaviour.
    """
    edges = undirected_edges(topo)
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= len(edges):
        raise ValueError("failed link count must be between zero and the number of edges")
    failed = sorted(random.Random(seed).sample(edges, count))
    result = copy.deepcopy(topo)
    for source, destination in failed:
        result["adj"][source].remove(destination)
        result["adj"][destination].remove(source)
        result["link_length"].pop((source, destination), None)
        result["link_length"].pop((destination, source), None)
    return result, failed


def resource_budget(topo, model):
    edges = undirected_edges(topo)
    lengths = [float(topo["link_length"][(source, destination)]) for source, destination in edges]
    return {
        "nodes": len(topo["nodes"]),
        "undirected_links": len(edges),
        "average_degree": 2 * len(edges) / len(topo["nodes"]),
        "link_bandwidth_gbps_per_direction": model.link_bw_gbps,
        "aggregate_directed_bandwidth_gbps": 2 * len(edges) * model.link_bw_gbps,
        "total_link_length_mm": sum(lengths),
        "mean_link_length_mm": sum(lengths) / len(lengths) if lengths else 0.0,
    }


def _summarize(topo, model, pairs, config):
    sim = FabricSim(topo, model)
    latencies, hops = [], []
    completed_at = 0.0
    failed = 0
    packets = [(source, destination, config.packet_size_flits,
                index / config.injection_rate_pkts_per_ns)
               for index, (source, destination) in enumerate(pairs)]
    results = sim.send_batch(packets)
    for packet, result in zip(packets, results):
        injected_at = packet[3]
        if result is None:
            failed += 1
            continue
        latency, distance, _ = result
        latencies.append(latency)
        hops.append(distance)
        completed_at = max(completed_at, injected_at + latency)
    observation_ns = max(completed_at, (len(pairs) - 1) / config.injection_rate_pkts_per_ns)
    delivered_bits = len(latencies) * config.packet_size_flits * model.flit_bits
    result = {
        "generated_packets": len(pairs),
        "delivered_packets": len(latencies),
        "unreachable_packets": failed,
        "delivery_fraction": len(latencies) / len(pairs),
        "completion_time_ns": completed_at if latencies else None,
        "observation_window_ns": observation_ns,
        "delivered_payload_gbps": delivered_bits / observation_ns if observation_ns > 0 else None,
        "mean_latency_ns": float(np.mean(latencies)) if latencies else None,
        "p50_latency_ns": float(np.percentile(latencies, 50)) if latencies else None,
        "p95_latency_ns": float(np.percentile(latencies, 95)) if latencies else None,
        "p99_latency_ns": float(np.percentile(latencies, 99)) if latencies else None,
        "max_latency_ns": float(max(latencies)) if latencies else None,
        "mean_hops": float(np.mean(hops)) if hops else None,
    }
    return result


def compare_workloads(config=None):
    """Compare equal-rank candidates, with assumptions and no winner assertion."""
    config = WorkloadConfig() if config is None else config
    if not isinstance(config, WorkloadConfig):
        config = WorkloadConfig.from_dict(config)
    rows, cols = (6, 7) if config.subdivisions == 1 else (9, 18)
    topologies = {
        "sphere": make_icosphere(config.subdivisions, config.radius_mm),
        "torus": make_torus(rows, cols, config.torus_link_length_mm),
    }
    pairs = make_workload_pairs(config)
    reference_links = len(undirected_edges(topologies["torus"]))
    candidates = []
    for name, topo in topologies.items():
        rate = config.link_rate_gbps
        if config.bandwidth_policy == "equal_total":
            rate *= reference_links / len(undirected_edges(topo))
        model = LatencyModel(link_rate_gbps=rate, lanes_per_link=config.lanes_per_link)
        baseline = _summarize(topo, model, pairs, config)
        failed_topo, removed = remove_links(topo, config.failed_links, config.seed + 1)
        degraded = _summarize(failed_topo, model, pairs, config)
        baseline_p99 = baseline["p99_latency_ns"]
        failure_p99 = degraded["p99_latency_ns"]
        candidates.append({
            "candidate": name,
            "topology": topo["name"],
            "resource_budget": resource_budget(topo, model),
            "baseline": baseline,
            "failure": {
                "removed_undirected_links": [list(edge) for edge in removed],
                "resource_budget": resource_budget(failed_topo, model),
                "metrics": degraded,
                "p99_latency_ratio_vs_baseline": failure_p99 / baseline_p99 if baseline_p99 and failure_p99 is not None else None,
            },
        })
    return {
        "schema_version": 1,
        "model": "synthetic_application_traffic",
        "config": asdict(config),
        "assumptions": {
            "evidence_level": "simulation; not measured hardware or end-to-end application performance",
            "routing": "deterministic shortest-hop BFS; no adaptive routing or retry protocol",
            "traffic": "same seeded rank pairs, packet sizes and injection times for every candidate and scenario",
            "placement": "rank IDs map directly to node IDs; no topology-aware placement optimization",
            "load": "injection_rate_pkts_per_ns is aggregate across all ranks, not per rank",
            "bandwidth": "equal_total normalizes baseline sum of directed link capacities to the torus; per_link keeps the same rate on every link",
            "failure": "seeded undirected links removed before traffic; no live recovery or fault-detection model",
            "metrics": "delivered-only latency percentiles; unreachable packets reported separately; payload Gbps is delivered bits divided by observation window",
            "moe_dispatch": "skewed synthetic expert dispatch only; no gate, expert compute, combine return, memory or tokens/s prediction",
            "stencil": "synthetic 2D periodic four-neighbor halo traffic; logical grid directly matches torus wiring and favors its placement; no solver or compute barrier",
            "physical": "sphere links use surface arcs; torus links use configured fixed lengths; not an equal wiring, power or packaging budget",
            "router": "packet serialization and directed-link contention; no VC credits, finite buffers, host injection limits, protocol overhead or thermal model",
        },
        "workload_summary": {
            "rank_count": rows * cols,
            "logical_stencil_grid": [rows, cols] if config.workload == "stencil" else None,
            "generated_packets": len(pairs),
            "payload_bytes_per_packet": config.packet_size_flits * 64,
            "aggregate_offered_packets_per_ns": config.injection_rate_pkts_per_ns,
            "mean_offered_packets_per_ns_per_rank": config.injection_rate_pkts_per_ns / (rows * cols),
            "aggregate_offered_payload_gbps": config.injection_rate_pkts_per_ns * config.packet_size_flits * 512,
            "first_rank_pairs": [list(pair) for pair in pairs[:10]],
        },
        "candidates": candidates,
    }
