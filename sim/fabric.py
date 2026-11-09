"""Exploratory store-and-forward fabric model with infinite output queues.

Batch traffic is scheduled in chronological hop-arrival order. Buffers,
credits, virtual-channel arbitration, reduction compute and protocol overhead
are not modeled. This is not a cycle-accurate or physically validated router.
"""
import heapq
import math
import random
from collections import defaultdict, deque
import numpy as np

try:
    from .validation import finite, integer
except ImportError:
    from validation import finite, integer


def _advance(timestamp, duration):
    """Reject overflow and timestamps too coarse to represent hop timing."""
    finite(duration, 'timing duration')
    result = timestamp + duration
    finite(result, 'simulation timestamp')
    if duration and (result <= timestamp or not math.isclose(
            result - timestamp, duration, rel_tol=1e-6, abs_tol=1e-9)):
        raise ValueError('timing resolution exceeded; reduce timestamps or duration range')
    return result


def _service_time(flits, model):
    try:
        service = flits * model.serialization_ns()
    except OverflowError as error:
        raise ValueError('packet serialization duration is out of range') from error
    return finite(service, 'packet serialization duration', strict=True)


class LatencyModel:
    def __init__(self, link_rate_gbps=32.0, lanes_per_link=8, flit_bits=512,
                 router_stages=4, router_clock_ghz=2.0,
                 wire_speed_mm_per_ns=150.0):
        finite(link_rate_gbps, 'link_rate_gbps', strict=True)
        integer(lanes_per_link, 'lanes_per_link', 1)
        integer(flit_bits, 'flit_bits', 1)
        integer(router_stages, 'router_stages')
        finite(router_clock_ghz, 'router_clock_ghz', strict=True)
        finite(wire_speed_mm_per_ns, 'wire_speed_mm_per_ns', strict=True)
        try:
            self.link_rate = finite(link_rate_gbps * 1e9, 'derived lane rate', strict=True)
            self.link_bw_gbps = finite(link_rate_gbps * lanes_per_link, 'derived link bandwidth', strict=True)
            self.router_cycle_ns = finite(1.0 / router_clock_ghz, 'router cycle', strict=True)
            self.lanes = lanes_per_link
            self.flit_bits = flit_bits
            self.router_stages = router_stages
            self.wire_speed = wire_speed_mm_per_ns
            self.serialization_ns()
            self.router_ns()
        except (OverflowError, ZeroDivisionError) as error:
            raise ValueError('latency parameters produce out-of-range derived values') from error

    def serialization_ns(self):
        return finite(self.flit_bits / self.link_bw_gbps, 'serialization_ns', strict=True)

    def wire_ns(self, length_mm):
        finite(length_mm, 'length_mm')
        return finite(length_mm / self.wire_speed, 'wire propagation time')

    def router_ns(self):
        return finite(self.router_stages * self.router_cycle_ns, 'router delay')

    def hop_ns(self, length_mm):
        return finite(self.router_ns() + self.serialization_ns() + self.wire_ns(length_mm), 'hop delay', strict=True)


def _endpoints(topo, src, dst):
    if src not in topo['adj'] or dst not in topo['adj']:
        raise ValueError('source and destination must be topology nodes')


def _validate_topology(topo):
    nodes = topo['nodes']
    if len(set(nodes)) != len(nodes) or set(nodes) != set(topo['adj']):
        raise ValueError('nodes must be unique and match adjacency keys')
    for u, neighbors in topo['adj'].items():
        if len(set(neighbors)) != len(neighbors):
            raise ValueError('adjacency must not contain duplicate neighbors')
        for v in neighbors:
            if v not in topo['adj'] or v == u:
                raise ValueError('adjacency contains an unknown node or self-link')
            if (u, v) not in topo['link_length']:
                raise ValueError('every directed link requires a length in mm')
            finite(topo['link_length'][(u, v)], 'link length')


def bfs_route(topo, src, dst):
    _endpoints(topo, src, dst)
    parent = {src: None}
    q = deque([src])
    while q:
        u = q.popleft()
        if u == dst:
            break
        for v in topo['adj'][u]:
            if v not in parent:
                parent[v] = u
                q.append(v)
    if dst not in parent:
        return None
    path, cur = [], dst
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    return path[::-1]


def route_packet(topo, src, dst):
    return bfs_route(topo, src, dst)


class FabricSim:
    def __init__(self, topo, latency_model=None, vcs=3):
        _validate_topology(topo)
        integer(vcs, 'vcs', 1)
        self.topo = topo
        self.lat = latency_model or LatencyModel()
        self.vcs = vcs  # Metadata only; virtual channels are not simulated.
        self.reset()

    def reset(self):
        self.link_busy = defaultdict(float)
        self.received = []
        self._used = False

    def _packet(self, src, dst, flits, injection):
        _endpoints(self.topo, src, dst)
        integer(flits, 'packet_size_flits', 1)
        finite(injection, 'inject_time_ns')
        _service_time(flits, self.lat)
        return route_packet(self.topo, src, dst)

    def _record(self, src, dst, injection, completion, path):
        latency, hops = completion - injection, len(path) - 1
        self.received.append({'src': src, 'dst': dst, 'latency_ns': latency,
                              'hops': hops, 'inject_time': injection,
                              'completion_time_ns': completion})
        return latency, hops, path

    def send_packet(self, src, dst, packet_size_flits=4, inject_time_ns=0.0):
        """Reserve one route; use send_batch for concurrent competing packets.

        Incremental calls retain earlier reservations and are suitable only
        for isolated probes or deliberately sequential traffic.
        """
        path = self._packet(src, dst, packet_size_flits, inject_time_ns)
        self._used = True
        if path is None:
            return None
        if len(path) == 1:
            return self._record(src, dst, inject_time_ns, inject_time_ns, path)
        t = _advance(inject_time_ns, self.lat.router_ns())
        for u, v in zip(path, path[1:]):
            start = max(t, self.link_busy[(u, v)])
            end = _advance(start, _service_time(packet_size_flits, self.lat))
            self.link_busy[(u, v)] = end
            t = _advance(end, self.lat.wire_ns(self.topo['link_length'][(u, v)]))
            if v != dst:
                t = _advance(t, self.lat.router_ns())
        return self._record(src, dst, inject_time_ns, t, path)

    def send_batch(self, packets):
        """Schedule (src, dst, flits, injection_ns) tuples on a fresh simulator.

        Directed links have independent full-duplex bandwidth. Output queues
        use arrival time, then input index for simultaneous ties. Return values
        preserve input order; unreachable destinations return None.
        """
        if self._used:
            raise ValueError('send_batch requires a fresh simulator or reset()')
        packets = list(packets)
        paths = [self._packet(*packet) for packet in packets]
        results, events = [None] * len(packets), []
        self._used = True
        for i, ((src, dst, flits, injection), path) in enumerate(zip(packets, paths)):
            if path is None:
                continue
            if len(path) == 1:
                results[i] = self._record(src, dst, injection, injection, path)
            else:
                heapq.heappush(events, (_advance(injection, self.lat.router_ns()), i, 0))
        while events:
            arrival, i, hop = heapq.heappop(events)
            src, dst, flits, injection = packets[i]
            path = paths[i]
            u, v = path[hop:hop + 2]
            start = max(arrival, self.link_busy[(u, v)])
            end = _advance(start, _service_time(flits, self.lat))
            self.link_busy[(u, v)] = end
            completion = _advance(end, self.lat.wire_ns(self.topo['link_length'][(u, v)]))
            if hop + 1 == len(path) - 1:
                results[i] = self._record(src, dst, injection, completion, path)
            else:
                heapq.heappush(events, (_advance(completion, self.lat.router_ns()), i, hop + 1))
        return results


def _traffic_inputs(nodes, n_packets):
    integer(n_packets, 'n_packets')
    if len(set(nodes)) != len(nodes):
        raise ValueError('traffic nodes must be unique')
    if n_packets and len(nodes) < 2:
        raise ValueError('traffic requires at least two distinct nodes')


def uniform_random_traffic(nodes, n_packets, rng=None):
    nodes = list(nodes)
    _traffic_inputs(nodes, n_packets)
    rng = rng or random.Random(42)
    return [tuple(rng.sample(nodes, 2)) for _ in range(n_packets)]


def hotspot_traffic(nodes, n_packets, hotspot_frac=0.05, hotspot_share=0.7, rng=None):
    nodes = list(nodes)
    _traffic_inputs(nodes, n_packets)
    finite(hotspot_frac, 'hotspot_frac', strict=True)
    finite(hotspot_share, 'hotspot_share')
    if hotspot_frac > 1 or hotspot_share > 1:
        raise ValueError('hotspot fractions must be <= 1')
    if not n_packets:
        return []
    rng = rng or random.Random(42)
    hotspots = rng.sample(nodes, max(1, int(len(nodes) * hotspot_frac)))
    pairs = []
    for _ in range(n_packets):
        src = rng.choice(nodes)
        targets = [n for n in hotspots if n != src]
        pool = targets if targets and rng.random() < hotspot_share else [n for n in nodes if n != src]
        pairs.append((src, rng.choice(pool)))
    return pairs


def _connected_tree(topo):
    _validate_topology(topo)
    nodes = topo['nodes']
    if not nodes:
        return {}, {}
    parent, levels = {nodes[0]: None}, {nodes[0]: 0}
    q = deque([nodes[0]])
    while q:
        u = q.popleft()
        for v in topo['adj'][u]:
            if v not in parent:
                parent[v] = u
                levels[v] = levels[u] + 1
                q.append(v)
    if len(parent) != len(nodes):
        raise ValueError('collectives require a connected topology')
    for u, neighbors in topo['adj'].items():
        if any(u not in topo['adj'][v] for v in neighbors):
            raise ValueError('collectives require bidirectional links')
    return parent, levels


def hamiltonian_ring(topo):
    """Greedy ring ordering; nonadjacent members use BFS routes."""
    _connected_tree(topo)
    nodes = list(topo['nodes'])
    if not nodes:
        return []
    visited, ring = {nodes[0]}, [nodes[0]]
    while len(ring) < len(nodes):
        q, seen = deque([ring[-1]]), {ring[-1]}
        while q:
            u = q.popleft()
            if u not in visited:
                ring.append(u)
                visited.add(u)
                break
            for v in topo['adj'][u]:
                if v not in seen:
                    seen.add(v)
                    q.append(v)
    return ring


def _phase_time(topo, pairs, message_flits, latency_model):
    results = FabricSim(topo, latency_model).send_batch(
        [(s, d, message_flits, 0.0) for s, d in pairs])
    return max((r[0] for r in results), default=0.0)


def ring_all_reduce_time(topo, message_flits=8, latency_model=None):
    """Ideal schedule; message_flits is each per-step chunk, not full payload.

    Excludes reduction compute and protocol overhead. Full per-rank payload is
    N chunks; tree/ring comparisons must first match their payload semantics.
    """
    integer(message_flits, 'message_flits', 1)
    ring = hamiltonian_ring(topo)
    if len(ring) < 2:
        return 0.0
    pairs = [(src, ring[(i + 1) % len(ring)]) for i, src in enumerate(ring)]
    return 2 * (len(ring) - 1) * _phase_time(topo, pairs, message_flits, latency_model)


def tree_all_reduce_time(topo, message_flits=8, latency_model=None):
    """BFS reduce/broadcast using full messages and actual graph depth.

    Excludes reduction compute and shared endpoint-port limits beyond the
    modeled directed output-link queues.
    """
    integer(message_flits, 'message_flits', 1)
    parent, levels = _connected_tree(topo)
    total = 0.0
    for level in range(1, max(levels.values(), default=0) + 1):
        pairs = [(n, parent[n]) for n in topo['nodes'] if levels[n] == level]
        total += _phase_time(topo, pairs, message_flits, latency_model)
        total += _phase_time(topo, [(v, u) for u, v in pairs], message_flits, latency_model)
    return total


def run_traffic(topo, traffic_pairs, latency_model=None,
                injection_rate_pkts_per_ns=None, packet_size_flits=4):
    """Run isolated probes (rate=None) or an aggregate-rate concurrent stream."""
    integer(packet_size_flits, 'packet_size_flits', 1)
    if injection_rate_pkts_per_ns is not None:
        finite(injection_rate_pkts_per_ns, 'injection_rate_pkts_per_ns', strict=True)
    sim = FabricSim(topo, latency_model)
    pairs = list(traffic_pairs)
    if injection_rate_pkts_per_ns is None:
        results = []
        for src, dst in pairs:
            sim.reset()
            results.append(sim.send_packet(src, dst, packet_size_flits))
    else:
        results = sim.send_batch([(src, dst, packet_size_flits,
                                   i / injection_rate_pkts_per_ns)
                                  for i, (src, dst) in enumerate(pairs)])
    delivered = [r for r in results if r is not None]
    if not delivered:
        return None
    latencies = np.array([r[0] for r in delivered])
    hops = np.array([r[1] for r in delivered])
    return {
        'n_packets': len(delivered), 'failed': len(pairs) - len(delivered),
        'avg_latency_ns': float(np.mean(latencies)),
        'p50_latency_ns': float(np.percentile(latencies, 50)),
        'p95_latency_ns': float(np.percentile(latencies, 95)),
        'p99_latency_ns': float(np.percentile(latencies, 99)),
        'max_latency_ns': float(np.max(latencies)),
        'avg_hops': float(np.mean(hops)), 'p99_hops': int(np.percentile(hops, 99)),
        'latencies_ns': latencies.tolist(), 'hops': hops.tolist(),
    }
