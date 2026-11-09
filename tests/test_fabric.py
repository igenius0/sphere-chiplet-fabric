"""Analytic packet timing and failure regressions on small known graphs."""
import math
from pathlib import Path
import random
import subprocess
import sys
import unittest

from sim.fabric import (FabricSim, LatencyModel, bfs_route,
                        hotspot_traffic, ring_all_reduce_time, run_traffic,
                        tree_all_reduce_time, uniform_random_traffic)


def line_topology(count=3):
    adj = {node: [] for node in range(count)}
    for node in range(count-1):
        adj[node].append(node+1)
        adj[node+1].append(node)
    return {"nodes": list(range(count)), "adj": adj,
            "link_length": {(src, dst): 4.0 for src in adj for dst in adj[src]}}


class RoutingTests(unittest.TestCase):
    def test_shortest_path_and_self_route(self):
        topo = line_topology()
        self.assertEqual(bfs_route(topo, 0, 2), [0, 1, 2])
        self.assertEqual(bfs_route(topo, 2, 0), [2, 1, 0])
        self.assertEqual(bfs_route(topo, 1, 1), [1])

    def test_unknown_endpoints_are_errors_even_for_self_route(self):
        topo = line_topology()
        for src, dst in ((99, 99), (0, 99), (99, 0)):
            with self.subTest(src=src, dst=dst), self.assertRaises(ValueError):
                bfs_route(topo, src, dst)

    def test_valid_disconnected_route_is_not_a_delivery(self):
        topo = {"nodes": [0, 1], "adj": {0: [], 1: []}, "link_length": {}}
        self.assertIsNone(bfs_route(topo, 0, 1))
        self.assertIsNone(FabricSim(topo).send_packet(0, 1))
        for collective in (tree_all_reduce_time, ring_all_reduce_time):
            with self.subTest(collective=collective.__name__), self.assertRaises(ValueError):
                collective(topo)

    def test_directed_routes_do_not_invent_reverse_links(self):
        topo = {"nodes": [0, 1], "adj": {0: [1], 1: []},
                "link_length": {(0, 1): 4}}
        sim = FabricSim(topo)
        forward, reverse = sim.send_batch([(0, 1, 4, 0), (1, 0, 4, 0)])
        self.assertIsNotNone(forward)
        self.assertIsNone(reverse)
        with self.assertRaises(ValueError):
            tree_all_reduce_time(topo)


class PacketTimingTests(unittest.TestCase):
    # Four 512-bit flits at 256 Gbit/s => 8 ns serialization.
    # Each hop has 2 ns router delay and 4/150 ns propagation.
    HOP_NS = 2 + 8 + 4 / 150

    def test_known_one_and_two_hop_uncontended_latencies(self):
        topo = line_topology()
        for destination in (1, 2):
            with self.subTest(destination=destination):
                latency, hops, path = FabricSim(topo).send_packet(0, destination)
                self.assertAlmostEqual(latency, destination * self.HOP_NS)
                self.assertEqual(hops, destination)
                self.assertEqual(path, list(range(destination+1)))

    def test_simultaneous_packets_share_directed_link_bandwidth(self):
        sim = FabricSim(line_topology())
        results = sim.send_batch([(0, 1, 4, 0), (0, 1, 4, 0)])
        self.assertAlmostEqual(results[0][0], self.HOP_NS)
        self.assertAlmostEqual(results[1][0], self.HOP_NS + 8)

    def test_opposite_directions_use_independent_full_duplex_links(self):
        results = FabricSim(line_topology()).send_batch([(0, 1, 4, 0), (1, 0, 4, 0)])
        self.assertAlmostEqual(results[0][0], self.HOP_NS)
        self.assertAlmostEqual(results[1][0], self.HOP_NS)

    def test_earlier_arrival_precedes_future_downstream_reservation(self):
        # The direct packet reaches 1->2 at 3 ns, before the 0->2 packet
        # arrives at that link at 12.0267 ns. Neither packet should queue.
        results = FabricSim(line_topology()).send_batch([(0, 2, 4, 0), (1, 2, 4, 1)])
        self.assertAlmostEqual(results[0][0], 2 * self.HOP_NS)
        self.assertAlmostEqual(results[1][0], self.HOP_NS)

    def test_batch_schedules_injection_times_and_preserves_result_order(self):
        results = FabricSim(line_topology()).send_batch([(0, 1, 4, 100), (0, 2, 4, 0)])
        self.assertEqual(results[0][2], [0, 1])
        self.assertEqual(results[1][2], [0, 1, 2])
        self.assertAlmostEqual(results[0][0], self.HOP_NS)
        self.assertAlmostEqual(results[1][0], 2 * self.HOP_NS)

    def test_translation_of_all_injection_times_keeps_latencies_identical(self):
        packets = [(0, 2, 4, 0), (1, 2, 2, 1), (2, 0, 8, 2), (0, 2, 4, 3)]
        original = FabricSim(line_topology()).send_batch(packets)
        shifted = FabricSim(line_topology()).send_batch(
            [(src, dst, flits, injection+1000)
             for src, dst, flits, injection in packets])
        for first, second in zip(original, shifted):
            self.assertAlmostEqual(first[0], second[0])
            self.assertEqual(first[1:], second[1:])

    def test_none_rate_is_independent_uncontended_samples(self):
        result = run_traffic(line_topology(), [(0, 1), (0, 1)])
        self.assertEqual(result["n_packets"], 2)
        self.assertEqual(result["failed"], 0)
        for latency in result["latencies_ns"]:
            self.assertAlmostEqual(latency, self.HOP_NS)

    def test_positive_rate_enables_queueing(self):
        result = run_traffic(line_topology(), [(0, 1), (0, 1)],
                             injection_rate_pkts_per_ns=1)
        self.assertAlmostEqual(result["latencies_ns"][0], self.HOP_NS)
        self.assertAlmostEqual(result["latencies_ns"][1], self.HOP_NS+7)

    def test_reset_removes_previous_contention(self):
        sim = FabricSim(line_topology())
        sim.send_packet(0, 1)
        sim.reset()
        self.assertAlmostEqual(sim.send_packet(0, 1)[0], self.HOP_NS)

    def test_disconnected_packets_are_counted(self):
        topo = line_topology()
        topo["adj"][2] = []
        topo["adj"][1] = [0]
        result = run_traffic(topo, [(0, 1), (0, 2)])
        self.assertEqual(result["n_packets"], 1)
        self.assertEqual(result["failed"], 1)

    def test_single_node_collectives_have_zero_communication(self):
        for collective in (tree_all_reduce_time, ring_all_reduce_time):
            self.assertEqual(collective(line_topology(1)), 0)


class InputValidationTests(unittest.TestCase):
    def test_invalid_graph_links_are_not_silently_repaired(self):
        invalid = [
            {"nodes": [0, 1], "adj": {0: [1], 1: []}, "link_length": {}},
            {"nodes": [0], "adj": {0: [99]}, "link_length": {(0, 99): 4}},
            {"nodes": [0, 1], "adj": {0: [1, 1], 1: []},
             "link_length": {(0, 1): 4}},
            {"nodes": [0, 1], "adj": {0: [1], 1: []},
             "link_length": {(0, 1): -1}},
        ]
        for topo in invalid:
            with self.subTest(topo=topo), self.assertRaises(ValueError):
                FabricSim(topo)

    def test_latency_parameters_require_positive_finite_values(self):
        for name in ("link_rate_gbps", "lanes_per_link", "flit_bits",
                     "router_clock_ghz", "wire_speed_mm_per_ns"):
            for value in (0, -1, math.nan, math.inf):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    LatencyModel(**{name: value})

    def test_packet_size_requires_positive_integer(self):
        for value in (0, -1, 1.5, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FabricSim(line_topology()).send_packet(0, 1, packet_size_flits=value)

    def test_injection_time_requires_nonnegative_finite_number(self):
        for value in (-1, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FabricSim(line_topology()).send_packet(0, 1, inject_time_ns=value)

    def test_injection_rate_requires_positive_finite_number_when_supplied(self):
        for value in (0, -1, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_traffic(line_topology(), [(0, 1)], injection_rate_pkts_per_ns=value)

    def test_singleton_positive_traffic_requests_fail_without_retry_loop(self):
        # Keep this hang regression bounded even if destination retry loops
        # accidentally return in a later change.
        code = """
from sim.fabric import uniform_random_traffic, hotspot_traffic
for generator in (uniform_random_traffic, hotspot_traffic):
    try:
        generator([0], 1)
    except ValueError:
        pass
    else:
        raise AssertionError(generator.__name__ + ' accepted impossible traffic')
"""
        completed = subprocess.run([sys.executable, "-c", code],
                                   cwd=Path(__file__).resolve().parents[1],
                                   text=True, capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_zero_requested_packets_needs_no_possible_destination(self):
        for generator in (uniform_random_traffic, hotspot_traffic):
            self.assertEqual(generator([], 0), [])

    def test_seeded_traffic_is_reproducible_and_excludes_self_destinations(self):
        for generator in (uniform_random_traffic, hotspot_traffic):
            first = generator(list(range(42)), 200, rng=random.Random(7))
            second = generator(list(range(42)), 200, rng=random.Random(7))
            self.assertEqual(first, second)
            self.assertEqual(len(first), 200)
            self.assertTrue(all(src != dst for src, dst in first))


if __name__ == "__main__":
    unittest.main()
