"""Deterministic bounded-buffer flit network; cycles are not calibrated hardware.

Every flit uses a fixed spanning-tree up-then-down route and one unchanged VC.
Input buffers, endpoint rates, directed links and credit return are explicit.
This module does not implement wormhole packet/VC allocation or adaptive routing.
"""
from collections import Counter, defaultdict, deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass
import heapq
from numbers import Integral


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


@dataclass(frozen=True)
class Packet:
    packet_id: int
    source: int
    destination: int
    flits: int
    release_cycle: int = 0
    vc: int = 0

    def __post_init__(self):
        for name in ("packet_id", "source", "destination", "release_cycle", "vc"):
            object.__setattr__(self, name, _integer(getattr(self, name), name))
        object.__setattr__(self, "flits", _integer(self.flits, "flits", 1))


@dataclass(frozen=True)
class NetworkConfig:
    buffer_flits_per_vc: int = 4
    virtual_channels: int = 2
    link_latency_cycles: int = 1
    credit_latency_cycles: int = 1
    max_cycles: int = 100000
    injection_flits_per_cycle: int = 1
    ejection_flits_per_cycle: int = 1
    router_latency_cycles: int = 1
    flit_bytes: int = 64
    paused_sinks: tuple = ()
    record_trace: bool = False

    def __post_init__(self):
        for name in ("buffer_flits_per_vc", "virtual_channels", "link_latency_cycles",
                     "credit_latency_cycles", "max_cycles", "injection_flits_per_cycle",
                     "ejection_flits_per_cycle", "router_latency_cycles", "flit_bytes"):
            object.__setattr__(self, name, _integer(getattr(self, name), name, 1))
        if not isinstance(self.record_trace, bool):
            raise ValueError("record_trace must be a bool")
        intervals = []
        try:
            for interval in self.paused_sinks:
                interval = tuple(interval)
                if len(interval) != 3:
                    raise ValueError("paused_sinks requires (node, start_cycle, end_cycle) triples")
                node, start, end = interval
                node = _integer(node, "paused sink node")
                start = _integer(start, "pause start")
                if end is not None:
                    end = _integer(end, "pause end", start)
                intervals.append((node, start, end))
        except TypeError as error:
            raise ValueError("paused_sinks requires an iterable of interval triples") from error
        object.__setattr__(self, "paused_sinks", tuple(intervals))


def _graph(topo):
    try:
        if not isinstance(topo, Mapping):
            raise ValueError("topology must supply nodes and symmetric adjacency")
        raw_nodes, raw_adj = topo["nodes"], topo["adj"]
        if not isinstance(raw_adj, Mapping):
            raise ValueError("adjacency must be a mapping from integer nodes to neighbors")
        nodes = [_integer(node, "topology node") for node in raw_nodes]
        adjacency_nodes = [_integer(node, "adjacency node") for node in raw_adj]
        if not nodes or len(set(nodes)) != len(nodes) or set(nodes) != set(adjacency_nodes):
            raise ValueError("nonempty unique nodes must match adjacency keys")
        adj = {}
        for node in nodes:
            neighbors = [_integer(other, "neighbor") for other in raw_adj[node]]
            if len(set(neighbors)) != len(neighbors):
                raise ValueError("adjacency must not contain duplicate neighbors")
            if any(other not in raw_adj or other == node for other in neighbors):
                raise ValueError("adjacency contains an unknown neighbor or self-link")
            adj[node] = sorted(neighbors)
        if any(node not in adj[other] for node in nodes for other in adj[node]):
            raise ValueError("finite-buffer tree routing requires symmetric links")
        return sorted(nodes), adj
    except (KeyError, TypeError) as error:
        raise ValueError("topology must supply nodes and symmetric adjacency") from error


def _tree_data(topo):
    nodes, adj = _graph(topo)
    parent, depth, component, components = {}, {}, {}, []
    for root in nodes:
        if root in parent:
            continue
        parent[root], depth[root], component[root] = None, 0, root
        queue, members = deque([root]), []
        while queue:
            node = queue.popleft()
            members.append(node)
            for other in adj[node]:
                if other not in parent:
                    parent[other], depth[other], component[other] = node, depth[node] + 1, root
                    queue.append(other)
        components.append({"root": root, "nodes": sorted(members)})
    return nodes, adj, parent, depth, component, components


def _route(source, destination, parent, component):
    if component[source] != component[destination]:
        return None
    upward, node = [], source
    while node is not None:
        upward.append(node)
        node = parent[node]
    downward, node = [], destination
    while node not in upward:
        downward.append(node)
        node = parent[node]
    return upward[:upward.index(node) + 1] + list(reversed(downward))


def _dependency_report(data):
    nodes, adj, parent, depth, component, components = data
    height = max(depth.values())
    ranks, directions = {}, {}
    for child in nodes:
        ancestor = parent[child]
        if ancestor is not None:
            ranks[(child, ancestor)] = height - depth[child]
            ranks[(ancestor, child)] = height + depth[child]
            directions[(child, ancestor)] = "up"
            directions[(ancestor, child)] = "down"
    dependencies, checked = set(), 0
    for source in nodes:
        for destination in nodes:
            if source == destination or component[source] != component[destination]:
                continue
            path = _route(source, destination, parent, component)
            checked += 1
            channels = list(zip(path, path[1:]))
            dependencies.update(zip(channels, channels[1:]))
    ordered = sorted(ranks, key=lambda channel: (ranks[channel], channel))
    indices = {channel: index for index, channel in enumerate(ordered)}
    acyclic = all(ranks[first] < ranks[second] and indices[first] < indices[second]
                  for first, second in dependencies)
    if not acyclic:
        raise AssertionError("generated tree routes violate the channel rank certificate")
    return {
        "routing": "deterministic_spanning_tree_up_then_down",
        "connected": len(components) == 1,
        "components": components,
        "tree_edges": [[parent[node], node] for node in nodes if parent[node] is not None],
        "physical_undirected_links": sum(len(adj[node]) for node in nodes) // 2,
        "used_undirected_tree_links": len(nodes) - len(components),
        "checked_nonlocal_routes": checked,
        "acyclic": acyclic,
        "channel_order": [{"source": source, "destination": destination,
                           "rank": ranks[(source, destination)],
                           "direction": directions[(source, destination)]}
                          for source, destination in ordered],
        "dependencies": [{"before": list(first), "after": list(second)}
                         for first, second in sorted(dependencies)],
        "certificate_scope": "All fixed nonlocal routes in this spanning forest; ranks strictly increase. Each VC uses the same graph and never transitions VC. This is not an adaptive-routing/VC-transition proof or a liveness guarantee with stalled sinks.",
    }


def routing_dependency_report(topo):
    """Return a checkable channel-dependency DAG certificate for fixed routes."""
    return _dependency_report(_tree_data(topo))


@dataclass(frozen=True)
class _Flit:
    packet_id: int
    index: int
    route_position: int
    ready_cycle: int


def simulate_network(topo, packets, config=None):
    """Run bounded traffic with explicit queued, in-flight and returned credits.

    Per cycle: accept scheduled arrivals/credits, arbitrate existing ready input
    flits, then inject released endpoint flits. A newly injected/arrived flit
    waits router_latency_cycles before it can leave the router. An output link
    sends one flit/cycle; one physical input port supplies one flit/cycle across
    its VCs. Local ejection can consume the configured number of input ports.
    Completion includes draining every credit return after the final ejection.
    max_cycles limits cycles 0 through max_cycles - 1, including credit drain;
    a cutoff preserves all queued, in-flight and returning-credit state.
    """
    config = NetworkConfig() if config is None else config
    if not isinstance(config, NetworkConfig):
        raise ValueError("config must be NetworkConfig or None")
    data = _tree_data(topo)
    nodes, adj, parent, depth, component, components = data
    try:
        packets = list(packets)
    except TypeError as error:
        raise ValueError("packets must be an iterable of Packet values") from error
    if not all(isinstance(packet, Packet) for packet in packets):
        raise ValueError("packets must contain Packet values")
    if len({packet.packet_id for packet in packets}) != len(packets):
        raise ValueError("packet IDs must be unique")
    for packet in packets:
        if packet.source not in adj or packet.destination not in adj:
            raise ValueError("packet endpoints must exist in the topology")
        if packet.vc >= config.virtual_channels:
            raise ValueError("packet VC is outside configured virtual channels")
    if any(node not in adj for node, _, _ in config.paused_sinks):
        raise ValueError("paused sink must exist in the topology")
    packets.sort(key=lambda packet: packet.packet_id)
    states, rejected = {}, []
    for packet in packets:
        path = _route(packet.source, packet.destination, parent, component)
        if path is None:
            rejected.append({"packet_id": packet.packet_id, "source": packet.source,
                             "destination": packet.destination, "flits": packet.flits,
                             "reason": "unreachable_in_fixed_graph"})
        else:
            states[packet.packet_id] = {"packet": packet, "route": path,
                                       "injected": 0, "delivered": 0,
                                       "first_injection_cycle": None,
                                       "completion_cycle": None}
    capacity = config.buffer_flits_per_vc
    keys = [(node, upstream, vc) for node in nodes
            for upstream in [None] + adj[node] for vc in range(config.virtual_channels)]
    buffers = {key: deque() for key in keys}
    maxima = {key: 0 for key in keys}
    local_keys = {node: [key for key in keys if key[0] == node] for node in nodes}
    credit_keys = [(source, destination, vc) for source in nodes
                   for destination in adj[source] for vc in range(config.virtual_channels)]
    available = {key: capacity for key in credit_keys}
    minimum_credits = {key: capacity for key in credit_keys}
    in_flight, returning = [], []
    inflight_counts, returning_counts = defaultdict(int), defaultdict(int)
    injected_ids, delivered_ids = set(), set()
    per_source = {node: [state for state in states.values() if state["packet"].source == node]
                  for node in nodes}
    injection_next, output_next = defaultdict(int), defaultdict(int)
    stalls = {"output_no_credit": 0, "input_port_busy": 0,
              "sink_paused": 0, "injection_buffer_full": 0}
    trace, deliveries, sequence, elapsed = [], [], 0, 0

    def event(kind, cycle, **fields):
        if config.record_trace:
            trace.append({"cycle": cycle, "event": kind, **fields})

    def credit_key_for_buffer(key):
        node, upstream, vc = key
        return None if upstream is None else (upstream, node, vc)

    def pop_buffer(key, cycle):
        nonlocal sequence
        flit = buffers[key].popleft()
        credit_key = credit_key_for_buffer(key)
        if credit_key is not None:
            due = cycle + config.credit_latency_cycles
            heapq.heappush(returning, (due, sequence, credit_key))
            sequence += 1
            returning_counts[credit_key] += 1
        return flit

    def check_invariants():
        resident_ids = []
        for key, queue in buffers.items():
            if len(queue) > capacity:
                raise AssertionError("input buffer overflow")
            resident_ids.extend((flit.packet_id, flit.index) for flit in queue)
        resident_ids.extend((item[2].packet_id, item[2].index) for item in in_flight)
        if len(set(resident_ids)) != len(resident_ids):
            raise AssertionError("duplicate flit in resident/inflight state")
        if set(resident_ids) & delivered_ids or set(resident_ids) | delivered_ids != injected_ids:
            raise AssertionError("flit conservation or uniqueness violated")
        actual_inflight = Counter((item[3], item[4], item[5]) for item in in_flight)
        actual_returning = Counter(item[2] for item in returning)
        for source, destination, vc in credit_keys:
            key = (source, destination, vc)
            resident = len(buffers[(destination, source, vc)])
            if (inflight_counts[key] != actual_inflight[key]
                    or returning_counts[key] != actual_returning[key]):
                raise AssertionError("event heap and credit reservations differ")
            if not 0 <= available[key] <= capacity:
                raise AssertionError("credit underflow or overflow")
            if available[key] + resident + inflight_counts[key] + returning_counts[key] != capacity:
                raise AssertionError("reserved-space credit conservation violated")

    def sink_paused(node, cycle):
        return any(node == paused and start <= cycle and (end is None or cycle < end)
                   for paused, start, end in config.paused_sinks)

    def requests_output(key, target, cycle):
        if not buffers[key] or buffers[key][0].ready_cycle > cycle:
            return False
        flit = buffers[key][0]
        path = states[flit.packet_id]["route"]
        desired = path[flit.route_position + 1] if flit.route_position + 1 < len(path) else None
        return desired == target

    accepted_total = sum(state["packet"].flits for state in states.values())
    if accepted_total:
        for cycle in range(config.max_cycles):
            elapsed = cycle + 1
            while returning and returning[0][0] <= cycle:
                _, _, key = heapq.heappop(returning)
                returning_counts[key] -= 1
                available[key] += 1
                event("credit", cycle, source=key[0], destination=key[1], vc=key[2])
            while in_flight and in_flight[0][0] <= cycle:
                _, _, flit, source, destination, vc = heapq.heappop(in_flight)
                inflight_counts[(source, destination, vc)] -= 1
                key = (destination, source, vc)
                buffers[key].append(flit)
                maxima[key] = max(maxima[key], len(buffers[key]))
                event("arrival", cycle, packet_id=flit.packet_id, flit_index=flit.index,
                      source=source, destination=destination, vc=vc)
            for node in nodes:
                used_inputs = set()
                outputs = adj[node] + [None]
                # Rotate output priority; each output also round-robins input VCs.
                offset = cycle % len(outputs)
                outputs = outputs[offset:] + outputs[:offset]
                for target in outputs:
                    contenders = local_keys[node]
                    slots = config.ejection_flits_per_cycle if target is None else 1
                    for _ in range(slots):
                        start = output_next[(node, target)] % len(contenders)
                        candidate, saw_credit_block, saw_port_block = None, False, False
                        for distance in range(len(contenders)):
                            position = (start + distance) % len(contenders)
                            key = contenders[position]
                            if not requests_output(key, target, cycle):
                                continue
                            if target is None and sink_paused(node, cycle):
                                stalls["sink_paused"] += 1
                                break
                            if key[1] in used_inputs:
                                saw_port_block = True
                                continue
                            if target is not None and available[(node, target, key[2])] == 0:
                                saw_credit_block = True
                                continue
                            candidate = (key, position)
                            break
                        if saw_credit_block:
                            stalls["output_no_credit"] += 1
                        if saw_port_block:
                            stalls["input_port_busy"] += 1
                        if candidate is None:
                            break
                        key, position = candidate
                        flit = pop_buffer(key, cycle)
                        used_inputs.add(key[1])
                        output_next[(node, target)] = position + 1
                        state = states[flit.packet_id]
                        packet = state["packet"]
                        if target is None:
                            identity = (flit.packet_id, flit.index)
                            if identity in delivered_ids:
                                raise AssertionError("duplicate ejected flit")
                            delivered_ids.add(identity)
                            state["delivered"] += 1
                            event("ejection", cycle, packet_id=flit.packet_id,
                                  flit_index=flit.index, node=node, vc=packet.vc)
                            if state["delivered"] == packet.flits:
                                state["completion_cycle"] = cycle
                                deliveries.append({"packet_id": packet.packet_id,
                                    "source": packet.source, "destination": packet.destination,
                                    "flits": packet.flits, "release_cycle": packet.release_cycle,
                                    "first_injection_cycle": state["first_injection_cycle"],
                                    "completion_cycle": cycle,
                                    "latency_cycles": cycle - packet.release_cycle,
                                    "route": state["route"]})
                        else:
                            credit_key = (node, target, packet.vc)
                            available[credit_key] -= 1
                            minimum_credits[credit_key] = min(minimum_credits[credit_key], available[credit_key])
                            due = cycle + config.link_latency_cycles
                            next_flit = _Flit(flit.packet_id, flit.index, flit.route_position + 1,
                                              due + config.router_latency_cycles)
                            heapq.heappush(in_flight, (due, sequence, next_flit, node, target, packet.vc))
                            sequence += 1
                            inflight_counts[credit_key] += 1
                            event("send", cycle, packet_id=flit.packet_id, flit_index=flit.index,
                                  source=node, destination=target, vc=packet.vc)
            for node in nodes:
                source_states = per_source[node]
                if not source_states:
                    continue
                for _ in range(config.injection_flits_per_cycle):
                    start = injection_next[node] % len(source_states)
                    chosen, blocked = None, False
                    for distance in range(len(source_states)):
                        position = (start + distance) % len(source_states)
                        state = source_states[position]
                        packet = state["packet"]
                        if packet.release_cycle > cycle or state["injected"] == packet.flits:
                            continue
                        key = (node, None, packet.vc)
                        if len(buffers[key]) == capacity:
                            blocked = True
                            continue
                        chosen = (state, packet, key, position)
                        break
                    if blocked:
                        stalls["injection_buffer_full"] += 1
                    if chosen is None:
                        break
                    state, packet, key, position = chosen
                    index = state["injected"]
                    identity = (packet.packet_id, index)
                    if identity in injected_ids:
                        raise AssertionError("duplicate injected flit")
                    injected_ids.add(identity)
                    buffers[key].append(_Flit(packet.packet_id, index, 0,
                                              cycle + config.router_latency_cycles))
                    maxima[key] = max(maxima[key], len(buffers[key]))
                    state["injected"] += 1
                    if state["first_injection_cycle"] is None:
                        state["first_injection_cycle"] = cycle
                    injection_next[node] = position + 1
                    event("injection", cycle, packet_id=packet.packet_id,
                          flit_index=index, node=node, vc=packet.vc)
            check_invariants()
            if len(delivered_ids) == accepted_total and not in_flight and not returning:
                break
    check_invariants()
    pending = []
    for state in states.values():
        packet = state["packet"]
        if state["completion_cycle"] is None:
            reason = ("not_released" if packet.release_cycle >= elapsed else
                      "partially_injected" if state["injected"] < packet.flits else "awaiting_delivery")
            pending.append({"packet_id": packet.packet_id, "source": packet.source,
                            "destination": packet.destination, "flits": packet.flits,
                            "release_cycle": packet.release_cycle,
                            "first_injection_cycle": state["first_injection_cycle"],
                            "injected_flits": state["injected"],
                            "delivered_flits": state["delivered"],
                            "uninjected_flits": packet.flits - state["injected"],
                            "reason": reason, "route": state["route"]})
    occupancies = []
    for node, upstream, vc in keys:
        key = (node, upstream, vc)
        occupancies.append({"node": node, "upstream": upstream, "vc": vc,
            "capacity": capacity, "current": len(buffers[key]), "maximum": maxima[key],
            "flits": [{"packet_id": flit.packet_id, "flit_index": flit.index,
                       "route_position": flit.route_position, "ready_cycle": flit.ready_cycle}
                      for flit in buffers[key]]})
    credits = [{"source": source, "destination": destination, "vc": vc,
                "capacity": capacity, "available": available[(source, destination, vc)],
                "minimum_available": minimum_credits[(source, destination, vc)],
                "resident": len(buffers[(destination, source, vc)]),
                "inflight": inflight_counts[(source, destination, vc)],
                "return_pending": returning_counts[(source, destination, vc)]}
               for source, destination, vc in credit_keys]
    accounting = {"requested_packets": len(packets), "accepted_packets": len(states),
        "delivered_packets": len(deliveries), "pending_packets": len(pending),
        "rejected_packets": len(rejected), "requested_flits": sum(p.flits for p in packets),
        "accepted_flits": accepted_total, "injected_flits": len(injected_ids),
        "delivered_flits": len(delivered_ids), "buffered_flits": sum(len(q) for q in buffers.values()),
        "inflight_flits": len(in_flight),
        "uninjected_flits": accepted_total - len(injected_ids),
        "rejected_flits": sum(packet["flits"] for packet in rejected)}
    accounting["conservation_ok"] = accounting["requested_flits"] == sum(
        accounting[key] for key in ("delivered_flits", "buffered_flits", "inflight_flits",
                                    "uninjected_flits", "rejected_flits"))
    idle = not pending and not in_flight and not returning and not accounting["buffered_flits"]
    return {
        "schema_version": 1,
        "model": "bounded_input_vc_flit_network",
        "status": "completed" if idle else "max_cycles",
        "idle": idle,
        "cutoff_reason": (None if idle else "undelivered_packets" if pending
                          else "pending_credit_returns"),
        "elapsed_cycles": elapsed,
        "all_requested_delivered": not pending and not rejected,
        "configuration": asdict(config),
        "deliveries": sorted(deliveries, key=lambda item: (item["completion_cycle"], item["packet_id"])),
        "pending_packets": pending, "rejected_packets": rejected,
        "packet_routes": [{"packet_id": identity, "route": state["route"]}
                          for identity, state in states.items()],
        "accounting": accounting, "occupancies": occupancies, "credits": credits,
        "inflight": [{"arrival_cycle": due, "packet_id": flit.packet_id,
                      "flit_index": flit.index, "source": source,
                      "destination": destination, "vc": vc}
                     for due, _, flit, source, destination, vc in sorted(in_flight)],
        "credit_returns": [{"return_cycle": due, "source": key[0],
                            "destination": key[1], "vc": key[2]}
                           for due, _, key in sorted(returning)],
        "stalls": stalls, "trace": trace,
        "routing_certificate": _dependency_report(data),
        "limitations": ["Uncalibrated clocked flit model, not hardware performance.",
                        "Fixed spanning-tree routes; unused non-tree physical links.",
                        "Per-flit route metadata; no wormhole header/body locking or VC allocation.",
                        "No adaptive routing, VC transitions, protocol retries, HBM or thermal model.",
                        "Paused sinks can prevent completion; max_cycles is an explicit bound."],
    }
