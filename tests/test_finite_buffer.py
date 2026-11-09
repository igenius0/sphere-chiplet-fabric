"""Independent timing, trace-ledger and exhaustive small-graph checks.

The trace checker validates resource use and conservation without reproducing
the simulator's arbitration choices. Tiny hand calculations provide timing
oracles; a separate graph traversal checks the published routing certificate.
"""
from collections import Counter, defaultdict, deque
from copy import deepcopy
from dataclasses import fields, replace
from itertools import combinations
import math
import random
import unittest

from sim.finite_buffer import (NetworkConfig, Packet, routing_dependency_report,
                               simulate_network)


def graph(nodes, edges=()):
    adj = {node: [] for node in nodes}
    for first, second in edges:
        adj[first].append(second)
        adj[second].append(first)
    return {"nodes": list(nodes), "adj": adj}


def line(count=3):
    return graph(range(count), [(node, node + 1) for node in range(count - 1)])


def path_in_graph(adj, source, destination):
    """Independent breadth-first path oracle, used on the certificate's forest."""
    paths, queue = {source: [source]}, deque([source])
    while queue:
        node = queue.popleft()
        if node == destination:
            return paths[node]
        for other in adj[node]:
            if other not in paths:
                paths[other] = paths[node] + [other]
                queue.append(other)
    return None


class LedgerAssertions(unittest.TestCase):
    def assert_certificate(self, topo, certificate):
        nodes = set(topo["nodes"])
        tree = {node: [] for node in nodes}
        edges = {tuple(sorted(edge)) for edge in certificate["tree_edges"]}
        self.assertEqual(len(edges), len(certificate["tree_edges"]))
        for first, second in edges:
            self.assertIn(second, topo["adj"][first])
            tree[first].append(second)
            tree[second].append(first)
        components, remaining = [], set(nodes)
        while remaining:
            root = min(remaining)
            members = {node for node in nodes
                       if path_in_graph(topo["adj"], root, node) is not None}
            components.append({"root": root, "nodes": sorted(members)})
            remaining -= members
        self.assertEqual(certificate["components"], components)
        self.assertEqual(certificate["connected"], len(components) == 1)
        self.assertEqual(certificate["physical_undirected_links"],
                         sum(len(v) for v in topo["adj"].values()) // 2)
        self.assertEqual(certificate["used_undirected_tree_links"], len(edges))
        self.assertEqual(len(edges), len(nodes) - len(components))
        channels = {(a, b) for edge in edges for a, b in (edge, edge[::-1])}
        ranks = {(entry["source"], entry["destination"]): entry["rank"]
                 for entry in certificate["channel_order"]}
        self.assertEqual(set(ranks), channels)
        self.assertEqual(len(certificate["channel_order"]), len(channels))
        expected_dependencies, nonlocal_routes = set(), 0
        for source in nodes:
            for destination in nodes:
                physical = path_in_graph(topo["adj"], source, destination)
                path = path_in_graph(tree, source, destination)
                self.assertEqual(path is None, physical is None)
                if source == destination or path is None:
                    continue
                nonlocal_routes += 1
                route_channels = list(zip(path, path[1:]))
                expected_dependencies.update(zip(route_channels, route_channels[1:]))
        dependencies = {(tuple(d["before"]), tuple(d["after"]))
                        for d in certificate["dependencies"]}
        self.assertEqual(dependencies, expected_dependencies)
        self.assertEqual(len(dependencies), len(certificate["dependencies"]))
        self.assertEqual(certificate["checked_nonlocal_routes"], nonlocal_routes)
        # Check acyclicity with Kahn's algorithm independently of supplied ranks.
        successors, indegree = defaultdict(set), {channel: 0 for channel in channels}
        for before, after in dependencies:
            self.assertLess(ranks[before], ranks[after])
            successors[before].add(after)
            indegree[after] += 1
        ready = deque(channel for channel in channels if indegree[channel] == 0)
        visited = 0
        while ready:
            channel = ready.popleft()
            visited += 1
            for after in successors[channel]:
                indegree[after] -= 1
                if indegree[after] == 0:
                    ready.append(after)
        self.assertEqual(visited, len(channels))
        self.assertTrue(certificate["acyclic"])
        for component in components:
            root = component["root"]
            depth = {node: len(path_in_graph(tree, root, node)) - 1
                     for node in component["nodes"]}
            for entry in certificate["channel_order"]:
                source, destination = entry["source"], entry["destination"]
                if source in depth:
                    self.assertEqual(entry["direction"],
                                     "up" if depth[source] > depth[destination] else "down")
        return tree

    def assert_trace_ledger(self, topo, packets, config, report):
        """Replay recorded events, checking FIFO, bandwidth and every credit."""
        self.assertTrue(config.record_trace)
        tree = self.assert_certificate(topo, report["routing_certificate"])
        requested = {packet.packet_id: packet for packet in packets}
        routes = {packet.packet_id: path_in_graph(tree, packet.source, packet.destination)
                  for packet in packets}
        accepted = {identity for identity, route in routes.items() if route is not None}
        rejected = set(requested) - accepted
        self.assertEqual({entry["packet_id"] for entry in report["rejected_packets"]}, rejected)
        self.assertEqual(len(report["rejected_packets"]), len(rejected))
        for entry in report["rejected_packets"]:
            packet = requested[entry["packet_id"]]
            self.assertEqual((entry["source"], entry["destination"], entry["flits"], entry["reason"]),
                             (packet.source, packet.destination, packet.flits, "unreachable_in_fixed_graph"))
        self.assertEqual({entry["packet_id"]: entry["route"] for entry in report["packet_routes"]},
                         {identity: routes[identity] for identity in accepted})
        capacity = config.buffer_flits_per_vc
        queues = {(node, upstream, vc): deque() for node in topo["nodes"]
                  for upstream in [None] + topo["adj"][node]
                  for vc in range(config.virtual_channels)}
        credits = {(a, b, vc): capacity for a in topo["nodes"]
                   for b in topo["adj"][a] for vc in range(config.virtual_channels)}
        maxima, minima = Counter(), dict(credits)
        resident_metadata, flights, returns = {}, {}, Counter()
        injected, delivered = set(), set()
        next_index, injection_counts, ejection_counts = Counter(), Counter(), Counter()
        input_counts, link_counts = Counter(), Counter()
        last_cycle, last_phase = -1, -1

        def dequeue(identity, node, vc, cycle):
            position, ready_cycle = resident_metadata.pop(identity)
            path = routes[identity[0]]
            self.assertEqual(path[position], node)
            upstream = path[position - 1] if position else None
            self.assertEqual(queues[(node, upstream, vc)].popleft(), identity)
            self.assertLessEqual(ready_cycle, cycle)
            input_counts[(cycle, node, upstream)] += 1
            self.assertLessEqual(input_counts[(cycle, node, upstream)], 1)
            if upstream is not None:
                returns[(cycle + config.credit_latency_cycles, upstream, node, vc)] += 1
            return position

        for event in report["trace"]:
            cycle, kind = event["cycle"], event["event"]
            phase = {"credit": 0, "arrival": 1, "send": 2, "ejection": 2, "injection": 3}[kind]
            self.assertGreaterEqual(cycle, last_cycle)
            self.assertLess(cycle, report["elapsed_cycles"])
            if cycle == last_cycle:
                self.assertGreaterEqual(phase, last_phase)
            last_cycle, last_phase = cycle, phase
            if kind == "credit":
                key = (event["source"], event["destination"], event["vc"])
                return_key = (cycle,) + key
                self.assertGreater(returns[return_key], 0)
                returns[return_key] -= 1
                credits[key] += 1
            else:
                identity = (event["packet_id"], event["flit_index"])
                packet = requested[identity[0]]
                self.assertIn(packet.packet_id, accepted)
                self.assertEqual(event["vc"], packet.vc)
                if kind == "injection":
                    self.assertEqual(event["node"], packet.source)
                    self.assertGreaterEqual(cycle, packet.release_cycle)
                    self.assertEqual(identity[1], next_index[packet.packet_id])
                    self.assertLess(identity[1], packet.flits)
                    self.assertNotIn(identity, injected)
                    next_index[packet.packet_id] += 1
                    injected.add(identity)
                    key = (packet.source, None, packet.vc)
                    queues[key].append(identity)
                    resident_metadata[identity] = (0, cycle + config.router_latency_cycles)
                    maxima[key] = max(maxima[key], len(queues[key]))
                    injection_counts[(cycle, packet.source)] += 1
                    self.assertLessEqual(injection_counts[(cycle, packet.source)],
                                         config.injection_flits_per_cycle)
                elif kind == "send":
                    source, destination = event["source"], event["destination"]
                    position = dequeue(identity, source, packet.vc, cycle)
                    self.assertEqual(routes[packet.packet_id][position + 1], destination)
                    link_counts[(cycle, source, destination)] += 1
                    self.assertEqual(link_counts[(cycle, source, destination)], 1)
                    credit_key = (source, destination, packet.vc)
                    self.assertGreater(credits[credit_key], 0)
                    credits[credit_key] -= 1
                    minima[credit_key] = min(minima[credit_key], credits[credit_key])
                    self.assertNotIn(identity, flights)
                    flights[identity] = (cycle + config.link_latency_cycles, source,
                                         destination, packet.vc, position + 1)
                elif kind == "arrival":
                    arrival, source, destination, vc, position = flights.pop(identity)
                    self.assertEqual((arrival, source, destination, vc),
                                     (cycle, event["source"], event["destination"], event["vc"]))
                    key = (destination, source, vc)
                    queues[key].append(identity)
                    resident_metadata[identity] = (position, cycle + config.router_latency_cycles)
                    maxima[key] = max(maxima[key], len(queues[key]))
                elif kind == "ejection":
                    self.assertEqual(event["node"], packet.destination)
                    self.assertEqual(dequeue(identity, packet.destination, packet.vc, cycle),
                                     len(routes[packet.packet_id]) - 1)
                    self.assertFalse(any(node == packet.destination and start <= cycle
                                         and (end is None or cycle < end)
                                         for node, start, end in config.paused_sinks))
                    self.assertNotIn(identity, delivered)
                    delivered.add(identity)
                    ejection_counts[(cycle, packet.destination)] += 1
                    self.assertLessEqual(ejection_counts[(cycle, packet.destination)],
                                         config.ejection_flits_per_cycle)
            queued = [identity for queue in queues.values() for identity in queue]
            self.assertEqual(len(queued), len(set(queued)))
            self.assertFalse(set(queued) & set(flights))
            self.assertFalse((set(queued) | set(flights)) & delivered)
            self.assertEqual(injected, set(queued) | set(flights) | delivered)
            self.assertTrue(all(len(queue) <= capacity for queue in queues.values()))
            flying_counts = Counter((entry[1], entry[2], entry[3]) for entry in flights.values())
            return_counts = Counter()
            for (_, source, destination, vc), count in returns.items():
                return_counts[(source, destination, vc)] += count
            for (source, destination, vc), available in credits.items():
                self.assertGreaterEqual(available, 0)
                self.assertLessEqual(available, capacity)
                self.assertEqual(available + len(queues[(destination, source, vc)])
                                 + flying_counts[(source, destination, vc)]
                                 + return_counts[(source, destination, vc)], capacity)
        for entry in report["occupancies"]:
            key = (entry["node"], entry["upstream"], entry["vc"])
            self.assertEqual(entry["capacity"], capacity)
            self.assertEqual(entry["current"], len(queues[key]))
            self.assertEqual(entry["maximum"], maxima[key])
            self.assertEqual([(f["packet_id"], f["flit_index"]) for f in entry["flits"]], list(queues[key]))
            for flit in entry["flits"]:
                self.assertEqual((flit["route_position"], flit["ready_cycle"]),
                                 resident_metadata[(flit["packet_id"], flit["flit_index"])])
        for entry in report["credits"]:
            key = (entry["source"], entry["destination"], entry["vc"])
            self.assertEqual(entry["available"], credits[key])
            self.assertEqual(entry["minimum_available"], minima[key])
            self.assertEqual(entry["resident"], len(queues[(key[1], key[0], key[2])]))
            self.assertEqual(entry["inflight"], sum(value[1:4] == key for value in flights.values()))
            self.assertEqual(entry["return_pending"], sum(count for (_, a, b, vc), count in returns.items()
                                                        if (a, b, vc) == key))
        self.assertEqual({(entry["packet_id"], entry["flit_index"]):
                          (entry["arrival_cycle"], entry["source"], entry["destination"], entry["vc"])
                          for entry in report["inflight"]},
                         {identity: flight[:4] for identity, flight in flights.items()})
        self.assertEqual(Counter((entry["return_cycle"], entry["source"], entry["destination"], entry["vc"])
                                 for entry in report["credit_returns"]), +returns)
        completed = {packet_id for packet_id in accepted
                     if all((packet_id, index) in delivered for index in range(requested[packet_id].flits))}
        self.assertEqual({entry["packet_id"] for entry in report["deliveries"]}, completed)
        self.assertEqual(len(report["deliveries"]), len(completed))
        self.assertEqual({entry["packet_id"] for entry in report["pending_packets"]}, accepted - completed)
        self.assertEqual(len(report["pending_packets"]), len(accepted - completed))
        for entry in report["pending_packets"]:
            packet = requested[entry["packet_id"]]
            injected_count = sum(identity[0] == packet.packet_id for identity in injected)
            delivered_count = sum(identity[0] == packet.packet_id for identity in delivered)
            self.assertEqual(entry["injected_flits"], injected_count)
            self.assertEqual(entry["delivered_flits"], delivered_count)
            self.assertEqual(entry["uninjected_flits"], packet.flits - injected_count)
            self.assertEqual(entry["route"], routes[packet.packet_id])
            self.assertEqual(entry["release_cycle"], packet.release_cycle)
            self.assertEqual(entry["reason"], "not_released" if packet.release_cycle >= report["elapsed_cycles"]
                             else "partially_injected" if injected_count < packet.flits else "awaiting_delivery")
        for entry in report["deliveries"]:
            packet = requested[entry["packet_id"]]
            self.assertEqual((entry["source"], entry["destination"], entry["flits"], entry["release_cycle"]),
                             (packet.source, packet.destination, packet.flits, packet.release_cycle))
            self.assertEqual(entry["route"], routes[packet.packet_id])
            ejections = [e["cycle"] for e in report["trace"]
                         if e["event"] == "ejection" and e["packet_id"] == packet.packet_id]
            injections = [e["cycle"] for e in report["trace"]
                          if e["event"] == "injection" and e["packet_id"] == packet.packet_id]
            self.assertEqual(entry["completion_cycle"], max(ejections))
            self.assertEqual(entry["first_injection_cycle"], min(injections))
            self.assertEqual(entry["latency_cycles"], max(ejections) - packet.release_cycle)
        accounting = report["accounting"]
        self.assertEqual(accounting["requested_packets"], len(packets))
        self.assertEqual(accounting["accepted_packets"], len(accepted))
        self.assertEqual(accounting["rejected_packets"], len(rejected))
        self.assertEqual(accounting["delivered_packets"], len(completed))
        self.assertEqual(accounting["pending_packets"], len(accepted - completed))
        self.assertEqual(accounting["requested_flits"], sum(packet.flits for packet in packets))
        self.assertEqual(accounting["accepted_flits"], sum(requested[i].flits for i in accepted))
        self.assertEqual(accounting["injected_flits"], len(injected))
        self.assertEqual(accounting["delivered_flits"], len(delivered))
        self.assertEqual(accounting["buffered_flits"], sum(len(queue) for queue in queues.values()))
        self.assertEqual(accounting["inflight_flits"], len(flights))
        self.assertEqual(accounting["uninjected_flits"], sum(requested[i].flits for i in accepted) - len(injected))
        self.assertEqual(accounting["rejected_flits"], sum(requested[i].flits for i in rejected))
        self.assertTrue(accounting["conservation_ok"])
        idle = completed == accepted and not flights and not +returns and not resident_metadata
        self.assertEqual(report["idle"], idle)
        self.assertEqual(report["status"], "completed" if idle else "max_cycles")
        self.assertEqual(report["all_requested_delivered"], completed == set(requested))
        self.assertLessEqual(report["elapsed_cycles"], config.max_cycles)
        if idle:
            self.assertIsNone(report["cutoff_reason"])
            self.assertTrue(all(value == capacity for value in credits.values()))
        else:
            self.assertEqual(report["elapsed_cycles"], config.max_cycles)
            self.assertEqual(report["cutoff_reason"], "undelivered_packets" if completed != accepted
                             else "pending_credit_returns")


class HandTimingTests(LedgerAssertions):
    def run_case(self, topo, packets, **kwargs):
        config = NetworkConfig(record_trace=True, **kwargs)
        report = simulate_network(topo, packets, config)
        self.assert_trace_ledger(topo, packets, config, report)
        return report

    def test_local_packet_waits_router_latency_without_link_or_credit(self):
        report = self.run_case(line(1), [Packet(9, 0, 0, 1, release_cycle=4)],
                               router_latency_cycles=3)
        self.assertEqual(report["deliveries"][0]["completion_cycle"], 7)
        self.assertEqual(report["elapsed_cycles"], 8)
        self.assertEqual(report["credits"], [])

    def test_one_and_two_hop_hand_timing_includes_final_credit_drain(self):
        # Inject at 4. Each node waits R=2; each hop takes L=3.
        # One hop ejects at 4+2R+L=11, two hops at 4+3R+2L=16.
        for destination, completion in ((1, 11), (2, 16)):
            with self.subTest(destination=destination):
                report = self.run_case(line(), [Packet(21, 0, destination, 1, 4)],
                                       router_latency_cycles=2, link_latency_cycles=3,
                                       credit_latency_cycles=5)
                self.assertEqual(report["deliveries"][0]["completion_cycle"], completion)
                self.assertEqual(report["elapsed_cycles"], completion + 5 + 1)

    def test_one_slot_reservation_covers_link_flight_and_credit_round_trip(self):
        # First send 1, arrival 3, eject 4, returned credit 7. The next
        # flit is already at the source, but cannot send until cycle 7.
        report = self.run_case(line(2), [Packet(8, 0, 1, 2)], buffer_flits_per_vc=1,
                               link_latency_cycles=2, credit_latency_cycles=3)
        self.assertEqual([e["cycle"] for e in report["trace"] if e["event"] == "send"], [1, 7])
        self.assertEqual([e["cycle"] for e in report["trace"] if e["event"] == "ejection"], [4, 10])
        self.assertEqual(report["elapsed_cycles"], 14)

    def test_opposite_directions_are_full_duplex(self):
        report = self.run_case(line(2), [Packet(0, 0, 1, 1), Packet(1, 1, 0, 1)])
        self.assertEqual([p["completion_cycle"] for p in report["deliveries"]], [3, 3])
        self.assertEqual(report["elapsed_cycles"], 5)

    def test_ejection_rate_uses_distinct_physical_input_ports(self):
        topo = graph(range(3), [(0, 1), (0, 2)])
        packets = [Packet(1, 1, 0, 1), Packet(2, 2, 0, 1)]
        for rate, completion in ((1, [3, 4]), (2, [3, 3])):
            with self.subTest(rate=rate):
                report = self.run_case(topo, packets, ejection_flits_per_cycle=rate)
                self.assertEqual([p["completion_cycle"] for p in report["deliveries"]], completion)

    def test_endpoint_rates_cannot_bypass_one_grant_per_local_input(self):
        report = self.run_case(line(1), [Packet(3, 0, 0, 5)], buffer_flits_per_vc=4,
                               injection_flits_per_cycle=3, ejection_flits_per_cycle=3)
        self.assertEqual(Counter(e["cycle"] for e in report["trace"] if e["event"] == "injection"),
                         {0: 3, 1: 2})
        self.assertEqual([e["cycle"] for e in report["trace"] if e["event"] == "ejection"], [1, 2, 3, 4, 5])

    def test_ejection_slots_do_not_multiply_one_input_across_vcs(self):
        report = self.run_case(line(2), [Packet(0, 0, 1, 1, vc=0), Packet(1, 0, 1, 1, vc=1)],
                               paused_sinks=((1, 0, 10),), ejection_flits_per_cycle=4,
                               injection_flits_per_cycle=2)
        self.assertEqual([p["completion_cycle"] for p in report["deliveries"]], [10, 11])

    def test_sink_pause_bounds_queues_and_resume_releases_backpressure(self):
        packets = [Packet(51, 0, 1, 4)]
        blocked = self.run_case(line(2), packets, buffer_flits_per_vc=1,
                                 max_cycles=12, paused_sinks=((1, 0, None),))
        self.assertEqual(blocked["accounting"]["injected_flits"], 2)
        self.assertEqual(blocked["accounting"]["buffered_flits"], 2)
        self.assertEqual(blocked["accounting"]["uninjected_flits"], 2)
        self.assertEqual(blocked["accounting"]["delivered_flits"], 0)
        for name in ("output_no_credit", "sink_paused", "injection_buffer_full"):
            self.assertGreater(blocked["stalls"][name], 0)
        resumed = self.run_case(line(2), packets, buffer_flits_per_vc=1,
                                 credit_latency_cycles=2, paused_sinks=((1, 0, 10),))
        self.assertEqual([e["cycle"] for e in resumed["trace"] if e["event"] == "ejection"],
                         [10, 14, 18, 22])
        self.assertEqual(resumed["elapsed_cycles"], 25)

    def test_credit_only_cutoff_is_explicit_after_all_packet_deliveries(self):
        report = self.run_case(line(2), [Packet(37, 0, 1, 1)],
                               max_cycles=5, credit_latency_cycles=5)
        self.assertTrue(report["all_requested_delivered"])
        self.assertEqual(report["pending_packets"], [])
        self.assertEqual(report["status"], "max_cycles")
        self.assertFalse(report["idle"])
        self.assertEqual(report["cutoff_reason"], "pending_credit_returns")
        self.assertEqual(report["credit_returns"],
                         [{"return_cycle": 8, "source": 0, "destination": 1, "vc": 0}])

    def test_inflight_cutoff_keeps_reserved_space_and_exact_identity(self):
        report = self.run_case(line(2), [Packet(9001, 0, 1, 2)], buffer_flits_per_vc=1,
                               max_cycles=5, link_latency_cycles=7)
        self.assertEqual(report["inflight"], [{"arrival_cycle": 8, "packet_id": 9001,
                         "flit_index": 0, "source": 0, "destination": 1, "vc": 0}])
        credit = next(c for c in report["credits"] if (c["source"], c["destination"], c["vc"]) == (0, 1, 0))
        self.assertEqual((credit["available"], credit["resident"], credit["inflight"], credit["return_pending"]),
                         (0, 0, 1, 0))

    def test_all_releases_at_or_beyond_cutoff_are_uninjected(self):
        report = self.run_case(line(2), [Packet(2, 0, 1, 2, 7), Packet(6, 1, 0, 3, 100)], max_cycles=7)
        self.assertEqual(report["trace"], [])
        self.assertEqual(report["elapsed_cycles"], 7)
        self.assertEqual(report["accounting"]["uninjected_flits"], 5)
        self.assertEqual([p["reason"] for p in report["pending_packets"]], ["not_released", "not_released"])

    def test_cross_component_packets_are_rejected_with_original_ids(self):
        topo = graph([2, 5, 11], [(2, 5)])
        report = self.run_case(topo, [Packet(901, 2, 11, 3), Packet(7, 11, 11, 1), Packet(45, 5, 2, 2)])
        self.assertEqual([p["packet_id"] for p in report["rejected_packets"]], [901])
        self.assertEqual({p["packet_id"] for p in report["deliveries"]}, {7, 45})
        self.assertEqual(report["status"], "completed")
        self.assertFalse(report["all_requested_delivered"])

    def test_empty_or_all_rejected_traffic_is_idle_at_cycle_zero(self):
        self.assertEqual(self.run_case(line(1), [])["elapsed_cycles"], 0)
        report = self.run_case(graph([0, 1]), [Packet(6, 0, 1, 1, 99)])
        self.assertEqual(report["elapsed_cycles"], 0)
        self.assertEqual(report["accounting"]["rejected_flits"], 1)


class FairnessAndGraphTests(LedgerAssertions):
    def test_contending_input_ports_round_robin_in_known_three_way_case(self):
        topo = graph(range(4), [(0, 1), (0, 2), (0, 3)])
        packets = [Packet(0, 0, 3, 12), Packet(1, 1, 3, 12), Packet(2, 2, 3, 12)]
        config = NetworkConfig(buffer_flits_per_vc=16, record_trace=True)
        report = simulate_network(topo, packets, config)
        self.assert_trace_ledger(topo, packets, config, report)
        contested = [e["packet_id"] for e in report["trace"] if e["event"] == "send"
                     and e["source"] == 0 and e["destination"] == 3 and 3 <= e["cycle"] < 21]
        self.assertEqual(contested, [1, 2, 0] * 6)

    def test_input_vcs_receive_round_robin_grants(self):
        packets = [Packet(8, 0, 1, 20, vc=0), Packet(11, 0, 1, 20, vc=1)]
        config = NetworkConfig(buffer_flits_per_vc=32, injection_flits_per_cycle=2, record_trace=True)
        report = simulate_network(line(2), packets, config)
        self.assert_trace_ledger(line(2), packets, config, report)
        self.assertEqual([e["packet_id"] for e in report["trace"] if e["event"] == "send"], [8, 11] * 20)

    def test_packet_injection_is_fair_when_capacity_is_one(self):
        packets = [Packet(identity, 0, 1, 4) for identity in (4, 12, 99)]
        config = NetworkConfig(buffer_flits_per_vc=1, credit_latency_cycles=3, record_trace=True)
        report = simulate_network(line(2), packets, config)
        self.assert_trace_ledger(line(2), packets, config, report)
        self.assertEqual([e["packet_id"] for e in report["trace"] if e["event"] == "injection"], [4, 12, 99] * 4)

    def test_every_simple_undirected_graph_through_four_nodes(self):
        # 75 graphs, including every disconnected forest and cyclic graph.
        # All ordered endpoint pairs exercise accepted and rejected traffic.
        for count in range(1, 5):
            possible = list(combinations(range(count), 2))
            for mask in range(1 << len(possible)):
                with self.subTest(nodes=count, edge_mask=mask):
                    topo = graph(range(count), [edge for bit, edge in enumerate(possible) if mask & (1 << bit)])
                    packets = [Packet(source * count + destination, source, destination, 1,
                                      vc=(source + destination) % 2)
                               for source in range(count) for destination in range(count)]
                    config = NetworkConfig(buffer_flits_per_vc=1, link_latency_cycles=2,
                                           credit_latency_cycles=3, record_trace=True, max_cycles=1000)
                    report = simulate_network(topo, packets, config)
                    self.assert_trace_ledger(topo, packets, config, report)
                    self.assertTrue(report["idle"])

    def test_fixed_seed_adversarial_fifo_credit_and_cutoff_ledgers(self):
        # Seeds 100--109 are reserved for the separately owned integration suite.
        for seed in range(10):
            rng = random.Random(seed)
            nodes = list(range(7))
            edges = [(node, node + 1) for node in nodes[:-1]]
            edges += [edge for edge in combinations(nodes, 2)
                      if edge not in edges and rng.random() < 0.25]
            topo = graph(nodes, edges)
            packets = [Packet(1000 + identity * 3, rng.randrange(7), rng.randrange(7),
                              rng.randint(1, 7), rng.randint(0, 20), rng.randrange(3))
                       for identity in range(32)]
            config = NetworkConfig(buffer_flits_per_vc=rng.randint(1, 3), virtual_channels=3,
                                   link_latency_cycles=rng.randint(1, 4),
                                   credit_latency_cycles=rng.randint(1, 5),
                                   router_latency_cycles=rng.randint(1, 3),
                                   injection_flits_per_cycle=rng.randint(1, 3),
                                   ejection_flits_per_cycle=rng.randint(1, 3),
                                   paused_sinks=tuple((node, rng.randint(0, 8), rng.randint(25, 60))
                                                      for node in rng.sample(nodes, 3)),
                                   record_trace=True, max_cycles=10000)
            for actual in (config, replace(config, max_cycles=19)):
                with self.subTest(seed=seed, cutoff=actual.max_cycles):
                    report = simulate_network(topo, packets, actual)
                    self.assert_trace_ledger(topo, packets, actual, report)
                    if actual is config:
                        self.assertTrue(report["all_requested_delivered"])

    def test_permuted_inputs_and_repeat_runs_have_identical_reports(self):
        topo = graph([9, 2, 5, 7], [(9, 2), (2, 5), (5, 7), (7, 9), (2, 7)])
        packets = [Packet(55, 9, 5, 3, 2, 1), Packet(12, 7, 2, 4), Packet(97, 2, 9, 2, 1)]
        original_topology, original_packets = deepcopy(topo), list(packets)
        config = NetworkConfig(buffer_flits_per_vc=1, record_trace=True)
        expected = simulate_network(topo, packets, config)
        shuffled = {"nodes": list(reversed(topo["nodes"])),
                    "adj": {node: list(reversed(neighbors)) for node, neighbors in reversed(list(topo["adj"].items()))}}
        self.assertEqual(simulate_network(shuffled, reversed(packets), config), expected)
        self.assertEqual(simulate_network(topo, packets, config), expected)
        self.assertEqual(topo, original_topology)
        self.assertEqual(packets, original_packets)


class ValidationTests(unittest.TestCase):
    def test_packet_integer_fields_reject_bool_fractional_negative_and_nonfinite(self):
        valid = dict(packet_id=0, source=0, destination=1, flits=1, release_cycle=0, vc=0)
        for name in valid:
            for value in (True, False, -1, 1.5, math.nan, math.inf, "1", None):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    Packet(**{**valid, name: value})
        with self.assertRaises(ValueError):
            Packet(0, 0, 1, 0)

    def test_all_numeric_config_fields_require_positive_integers(self):
        names = [field.name for field in fields(NetworkConfig) if field.name not in ("paused_sinks", "record_trace")]
        for name in names:
            for value in (True, False, 0, -1, 1.1, math.nan, math.inf, "1", None):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    NetworkConfig(**{name: value})
        for value in (0, 1, None, "yes"):
            with self.subTest(trace=value), self.assertRaises(ValueError):
                NetworkConfig(record_trace=value)

    def test_pause_intervals_validate_nodes_and_half_open_bounds(self):
        invalid = (None, [1], [(1, 2)], [(1, 0, 2, 3)], [(True, 0, 1)],
                   [(1, -1, None)], [(1, 3, 2)], [(1, 0, math.inf)], [(1, 0, False)])
        for intervals in invalid:
            with self.subTest(intervals=intervals), self.assertRaises(ValueError):
                NetworkConfig(paused_sinks=intervals)
        mutable = [[0, 3, 3], [1, 4, None]]
        config = NetworkConfig(paused_sinks=mutable)
        mutable[0][1] = 99
        self.assertEqual(config.paused_sinks, ((0, 3, 3), (1, 4, None)))
        with self.assertRaises(ValueError):
            simulate_network(line(2), [], NetworkConfig(paused_sinks=((9, 0, None),)))

    def test_malformed_topology_is_rejected_before_packet_iteration(self):
        invalid = (None, {}, {"nodes": [], "adj": {}}, {"nodes": [0, 0], "adj": {0: []}},
                   {"nodes": [0], "adj": {0: [0]}}, {"nodes": [0], "adj": {0: [1]}},
                   {"nodes": [0, 1], "adj": {0: [1], 1: []}},
                   {"nodes": [0, 1], "adj": {0: [1, 1], 1: [0]}},
                   {"nodes": [False], "adj": {0: []}}, {"nodes": [0], "adj": {False: []}},
                   {"nodes": [0], "adj": {0.0: []}}, {"nodes": [0], "adj": []},
                   {"nodes": [0], "adj": {0: [True]}}, {"nodes": [0], "adj": {0: None}},
                   {"nodes": [0], "adj": {0: [], 1: []}})
        for topo in invalid:
            def untouched_packets():
                raise AssertionError("invalid topology consumed traffic before validation")
                yield
            with self.subTest(topo=topo), self.assertRaises(ValueError):
                simulate_network(topo, untouched_packets())
            with self.subTest(certificate=topo), self.assertRaises(ValueError):
                routing_dependency_report(topo)

    def test_packet_collection_endpoints_vcs_and_config_are_checked(self):
        for packets in (None, 3, [object()], [Packet(3, 0, 1, 1), Packet(3, 1, 0, 1)],
                        [Packet(0, 9, 9, 1)], [Packet(0, 0, 9, 1)], [Packet(0, 0, 1, 1, vc=2)]):
            with self.subTest(packets=packets), self.assertRaises(ValueError):
                simulate_network(line(2), packets)
        for config in ({}, 1, True):
            with self.subTest(config=config), self.assertRaises(ValueError):
                simulate_network(line(2), [], config)


if __name__ == "__main__":
    unittest.main()
