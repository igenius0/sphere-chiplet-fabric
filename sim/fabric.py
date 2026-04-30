"""
Improved fabric simulator v2.

Key improvements:
1. Tree-based all-reduce option (more representative of modern collectives like NCCL).
2. Hamiltonian-ish ring ordering for ring all-reduce so consecutive members are adjacent.
3. Cleaner contention model.
"""
import math
import random
from collections import defaultdict, deque
import numpy as np


class LatencyModel:
    def __init__(self,
                 link_rate_gbps=32.0,
                 lanes_per_link=8,
                 flit_bits=512,
                 router_stages=4,
                 router_clock_ghz=2.0,
                 wire_speed_mm_per_ns=150.0):
        self.link_rate = link_rate_gbps * 1e9
        self.lanes = lanes_per_link
        self.flit_bits = flit_bits
        self.router_cycle_ns = 1.0 / router_clock_ghz
        self.router_stages = router_stages
        self.wire_speed = wire_speed_mm_per_ns
        self.link_bw_gbps = link_rate_gbps * lanes_per_link

    def serialization_ns(self):
        return self.flit_bits / (self.lanes * self.link_rate) * 1e9

    def wire_ns(self, length_mm):
        return length_mm / self.wire_speed

    def router_ns(self):
        return self.router_stages * self.router_cycle_ns

    def hop_ns(self, length_mm):
        return self.router_ns() + self.serialization_ns() + self.wire_ns(length_mm)


def bfs_route(topo, src, dst):
    if src == dst:
        return [src]
    adj = topo['adj']
    parent = {src: None}
    q = deque([src])
    while q:
        u = q.popleft()
        if u == dst:
            break
        for v in adj[u]:
            if v not in parent:
                parent[v] = u
                q.append(v)
    if dst not in parent:
        return None
    path = []
    cur = dst
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    return path[::-1]


def route_packet(topo, src, dst):
    return bfs_route(topo, src, dst)


class FabricSim:
    def __init__(self, topo, latency_model=None, vcs=3):
        self.topo = topo
        self.lat = latency_model or LatencyModel()
        self.vcs = vcs
        self.link_busy = defaultdict(float)
        self.received = []

    def reset(self):
        self.link_busy = defaultdict(float)
        self.received = []

    def send_packet(self, src, dst, packet_size_flits=4, inject_time_ns=0.0):
        path = route_packet(self.topo, src, dst)
        if path is None:
            return None
        if len(path) < 2:
            if src == dst:
                return (0.0, 0, [src])
            return None

        ser_ns = self.lat.serialization_ns()
        router_ns = self.lat.router_ns()
        t = inject_time_ns + router_ns

        for i in range(len(path) - 1):
            u, v = path[i], path[i+1]
            length = self.topo['link_length'].get((u, v), 4.0)
            wire = self.lat.wire_ns(length)

            link_free = self.link_busy[(u, v)]
            t_link_start = max(t, link_free)
            packet_serialize = packet_size_flits * ser_ns
            t_link_end = t_link_start + packet_serialize
            self.link_busy[(u, v)] = t_link_end
            t = t_link_end + wire
            if v != dst:
                t += router_ns

        latency_ns = t - inject_time_ns
        hops = len(path) - 1
        self.received.append({
            'src': src, 'dst': dst, 'latency_ns': latency_ns,
            'hops': hops, 'inject_time': inject_time_ns
        })
        return (latency_ns, hops, path)


def uniform_random_traffic(nodes, n_packets, rng=None):
    rng = rng or random.Random(42)
    pairs = []
    for _ in range(n_packets):
        s = rng.choice(nodes)
        d = rng.choice(nodes)
        while d == s:
            d = rng.choice(nodes)
        pairs.append((s, d))
    return pairs


def hotspot_traffic(nodes, n_packets, hotspot_frac=0.05, hotspot_share=0.7, rng=None):
    rng = rng or random.Random(42)
    n_hot = max(1, int(len(nodes) * hotspot_frac))
    hotspots = rng.sample(nodes, n_hot)
    pairs = []
    for _ in range(n_packets):
        s = rng.choice(nodes)
        if rng.random() < hotspot_share:
            d = rng.choice(hotspots)
        else:
            d = rng.choice(nodes)
        while d == s:
            d = rng.choice(nodes)
        pairs.append((s, d))
    return pairs


def hamiltonian_ring(topo):
    """Greedy nearest-neighbor walk for a near-Hamiltonian cycle."""
    adj = topo['adj']
    nodes = list(topo['nodes'])
    if not nodes:
        return []
    visited = {nodes[0]}
    ring = [nodes[0]]
    while len(ring) < len(nodes):
        cur = ring[-1]
        next_node = None
        for n in adj[cur]:
            if n not in visited:
                next_node = n
                break
        if next_node is None:
            # jump to nearest unvisited via BFS
            dist = {cur: 0}
            q = deque([cur])
            target = None
            while q:
                u = q.popleft()
                if u not in visited and u != cur:
                    target = u
                    break
                for v in adj[u]:
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        q.append(v)
            next_node = target if target else nodes[-1]
        ring.append(next_node)
        visited.add(next_node)
    return ring


def ring_all_reduce_time(topo, message_flits=8, latency_model=None):
    ring = hamiltonian_ring(topo)
    n = len(ring)
    if n < 2:
        return 0.0
    sim = FabricSim(topo, latency_model)
    n_steps = 2 * (n - 1)
    total_time = 0.0
    for step in range(n_steps):
        sim.reset()
        step_max = 0.0
        for i, src in enumerate(ring):
            dst = ring[(i + 1) % n]
            r = sim.send_packet(src, dst, packet_size_flits=message_flits,
                                inject_time_ns=0.0)
            if r:
                step_max = max(step_max, r[0])
        total_time += step_max if step_max > 0 else 50.0
    return total_time


def tree_all_reduce_time(topo, message_flits=8, latency_model=None):
    """
    BFS tree all-reduce: log2(N) reduce steps + log2(N) broadcast steps.
    Each step time = max packet latency at that tree level (concurrent sends).
    """
    nodes = list(topo['nodes'])
    if len(nodes) < 2:
        return 0.0
    adj = topo['adj']
    root = nodes[0]
    parent = {root: None}
    children = defaultdict(list)
    levels = {root: 0}
    q = deque([root])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in parent:
                parent[v] = u
                children[u].append(v)
                levels[v] = levels[u] + 1
                q.append(v)

    max_level = max(levels.values())
    sim = FabricSim(topo, latency_model)
    total_time = 0.0

    # Reduce: leaves -> root, level by level
    for lvl in range(max_level, 0, -1):
        sim.reset()
        step_max = 0.0
        nodes_at_level = [n for n in nodes if levels.get(n) == lvl]
        for n in nodes_at_level:
            r = sim.send_packet(n, parent[n], packet_size_flits=message_flits,
                                inject_time_ns=0.0)
            if r:
                step_max = max(step_max, r[0])
        total_time += step_max if step_max > 0 else 50.0

    # Broadcast: root -> leaves, level by level
    for lvl in range(0, max_level):
        sim.reset()
        step_max = 0.0
        nodes_at_level = [n for n in nodes if levels.get(n) == lvl]
        for n in nodes_at_level:
            for c in children[n]:
                r = sim.send_packet(n, c, packet_size_flits=message_flits,
                                    inject_time_ns=0.0)
                if r:
                    step_max = max(step_max, r[0])
        total_time += step_max if step_max > 0 else 50.0

    return total_time


def run_traffic(topo, traffic_pairs, latency_model=None,
                injection_rate_pkts_per_ns=None, packet_size_flits=4):
    sim = FabricSim(topo, latency_model)
    latencies = []
    hops_list = []
    failed = 0

    for i, (s, d) in enumerate(traffic_pairs):
        if injection_rate_pkts_per_ns:
            t_inject = i / injection_rate_pkts_per_ns
        else:
            t_inject = 0.0
        result = sim.send_packet(s, d, packet_size_flits=packet_size_flits,
                                 inject_time_ns=t_inject)
        if result is None:
            failed += 1
            continue
        lat_ns, hops, _ = result
        latencies.append(lat_ns)
        hops_list.append(hops)

    if not latencies:
        return None

    latencies_arr = np.array(latencies)
    hops_arr = np.array(hops_list)
    return {
        'n_packets': len(latencies),
        'failed': failed,
        'avg_latency_ns': float(np.mean(latencies_arr)),
        'p50_latency_ns': float(np.percentile(latencies_arr, 50)),
        'p95_latency_ns': float(np.percentile(latencies_arr, 95)),
        'p99_latency_ns': float(np.percentile(latencies_arr, 99)),
        'max_latency_ns': float(np.max(latencies_arr)),
        'avg_hops': float(np.mean(hops_arr)),
        'p99_hops': int(np.percentile(hops_arr, 99)),
        'latencies_ns': latencies_arr.tolist(),
        'hops': hops_arr.tolist(),
    }
