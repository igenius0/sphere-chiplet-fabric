"""Independent invariants for concurrency, conservation and numeric limits."""
import math
import random
import unittest

from sim.fabric import FabricSim, LatencyModel
from sim.topology import make_icosphere, make_torus


def one_link():
    return {'nodes': [0, 1], 'adj': {0: [1], 1: [0]},
            'link_length': {(0, 1): 4.0, (1, 0): 4.0}}


class ConcurrencyInvariants(unittest.TestCase):
    def test_non_tied_arrivals_are_invariant_to_submission_permutation(self):
        # Injection times and service durations are distinct. Shuffling the
        # batch must not change timing merely by changing submission order.
        for topology in (make_torus(3, 3), make_icosphere(0)):
            rng = random.Random(20)
            packets = []
            for index in range(100):
                source, destination = rng.sample(topology['nodes'], 2)
                packets.append((source, destination, rng.randint(1, 10),
                                index * 7.123456789 + rng.random()))
            normal = FabricSim(topology).send_batch(packets)
            permutation = list(range(len(packets)))
            rng.shuffle(permutation)
            shuffled = FabricSim(topology).send_batch([packets[i] for i in permutation])
            restored = dict(zip(permutation, shuffled))
            with self.subTest(topology=topology['name']):
                for index, result in enumerate(normal):
                    self.assertEqual(result, restored[index])

    def test_saturated_stream_cannot_exceed_one_link_capacity(self):
        count, flits = 200, 16
        packets = [(0, 1, flits, index * 0.01) for index in range(count)]
        model = LatencyModel()
        results = FabricSim(one_link(), model).send_batch(packets)
        last_completion = max(packet[3] + result[0]
                              for packet, result in zip(packets, results))
        # At saturation the link services exactly all transmitted bits once.
        expected = (model.router_ns() + count * flits * model.serialization_ns()
                    + model.wire_ns(4))
        self.assertAlmostEqual(last_completion, expected)
        delivered_gbps = count * flits * model.flit_bits / last_completion
        self.assertLess(delivered_gbps, model.link_bw_gbps)

    def test_queueing_never_beats_uncontended_route_service_bound(self):
        topology = make_icosphere(1)
        model = LatencyModel()
        rng = random.Random(81)
        packets = []
        for index in range(300):
            source, destination = rng.sample(topology['nodes'], 2)
            packets.append((source, destination, rng.randint(1, 32), index * 0.1))
        results = FabricSim(topology, model).send_batch(packets)
        for packet, result in zip(packets, results):
            _, _, flits, _ = packet
            latency, hops, path = result
            lower_bound = (hops * (model.router_ns() + flits * model.serialization_ns())
                           + sum(topology['link_length'][u, v] / model.wire_speed
                                 for u, v in zip(path, path[1:])))
            self.assertGreaterEqual(latency + 1e-8, lower_bound)

    def test_parallel_opposite_streams_have_twice_unidirectional_capacity(self):
        count, flits = 100, 16
        forward = [(0, 1, flits, index * 0.01) for index in range(count)]
        reverse = [(1, 0, flits, index * 0.01) for index in range(count)]
        model = LatencyModel()
        solo = FabricSim(one_link(), model).send_batch(forward)
        both = FabricSim(one_link(), model).send_batch(forward + reverse)
        self.assertEqual(solo, both[:count])
        for first, second in zip(solo, both[count:]):
            self.assertAlmostEqual(first[0], second[0])

    def test_invalid_batch_does_not_partially_reserve_links(self):
        simulator = FabricSim(one_link())
        with self.assertRaises(ValueError):
            simulator.send_batch([(0, 1, 4, 0), (0, 99, 4, 1)])
        actual = simulator.send_batch([(0, 1, 4, 0)])
        expected = FabricSim(one_link()).send_batch([(0, 1, 4, 0)])
        self.assertEqual(actual, expected)

    def test_unreachable_packets_do_not_reserve_connected_links(self):
        topology = {'nodes': [0, 1, 2], 'adj': {0: [1], 1: [0], 2: []},
                    'link_length': {(0, 1): 4.0, (1, 0): 4.0}}
        actual = FabricSim(topology).send_batch([(0, 2, 100, 0), (0, 1, 4, 0)])
        expected = FabricSim(one_link()).send_batch([(0, 1, 4, 0)])[0]
        self.assertIsNone(actual[0])
        self.assertEqual(actual[1], expected)


class NumericIntegrity(unittest.TestCase):
    def test_derived_model_numbers_are_finite_and_positive_or_rejected(self):
        # Finite inputs alone do not guarantee representable derived rates or
        # cycle times. An explicit ValueError is preferable to zero/inf timing.
        pathological = ({'link_rate_gbps': 1e308},
                        {'link_rate_gbps': 1e-320},
                        {'wire_speed_mm_per_ns': 1e-320},
                        {'router_clock_ghz': 1e-320})
        for values in pathological:
            with self.subTest(parameters=values):
                try:
                    model = LatencyModel(**values)
                    latency = FabricSim(one_link(), model).send_batch([(0, 1, 4, 0)])[0][0]
                except ValueError:
                    continue
                self.assertTrue(math.isfinite(latency))
                self.assertGreater(latency, 0)
                self.assertTrue(math.isfinite(model.serialization_ns()))
                self.assertGreater(model.serialization_ns(), 0)

    def test_large_timestamps_preserve_physical_latency_or_fail_explicitly(self):
        expected = FabricSim(one_link()).send_batch([(0, 1, 4, 0)])[0][0]
        for injection in (1e17, 1e20, 1e300):
            with self.subTest(injection=injection):
                try:
                    actual = FabricSim(one_link()).send_batch([(0, 1, 4, injection)])[0][0]
                except ValueError:
                    continue
                self.assertAlmostEqual(actual, expected, places=6)

    def test_overflowing_packet_service_time_is_not_a_delivery(self):
        try:
            result = FabricSim(one_link()).send_batch([(0, 1, 10**308, 0)])[0]
        except ValueError:
            return
        self.assertTrue(math.isfinite(result[0]))
        self.assertGreater(result[0], 0)


if __name__ == '__main__':
    unittest.main()
