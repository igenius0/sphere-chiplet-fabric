"""Graph invariants independent of performance or superiority claims."""
import math
import unittest

from sim.fabric import FabricSim, bfs_route
from sim.topology import make_dragonfly, make_icosphere, make_torus, topology_stats


class TopologyTests(unittest.TestCase):
    def assert_undirected_graph(self, topo):
        nodes = set(topo["nodes"])
        self.assertEqual(set(topo["adj"]), nodes)
        for src, neighbors in topo["adj"].items():
            self.assertEqual(len(neighbors), len(set(neighbors)))
            self.assertNotIn(src, neighbors)
            for dst in neighbors:
                self.assertIn(dst, nodes)
                self.assertIn(src, topo["adj"][dst])
                self.assertGreater(topo["link_length"][src, dst], 0)
                self.assertAlmostEqual(topo["link_length"][src, dst],
                                       topo["link_length"][dst, src])

    def test_icosphere_euler_counts_geometry_and_degree(self):
        for subdivisions in (0, 1, 2):
            with self.subTest(subdivisions=subdivisions):
                topo = make_icosphere(subdivisions, radius_mm=40)
                self.assert_undirected_graph(topo)
                self.assertEqual(len(topo["nodes"]), 10 * 4 ** subdivisions + 2)
                self.assertEqual(sum(map(len, topo["adj"].values())) // 2,
                                 30 * 4 ** subdivisions)
                degrees = [len(n) for n in topo["adj"].values()]
                self.assertEqual(degrees.count(5), 12)
                self.assertTrue(all(d in (5, 6) for d in degrees))
                for position in topo["coords"].values():
                    self.assertAlmostEqual(math.sqrt(sum(x*x for x in position)), 40)

    def test_equal_node_torus_baselines_are_connected(self):
        for rows, cols, expected_nodes in ((6, 7, 42), (9, 18, 162)):
            with self.subTest(rows=rows, cols=cols):
                topo = make_torus(rows, cols)
                self.assert_undirected_graph(topo)
                self.assertEqual(len(topo["nodes"]), expected_nodes)
                self.assertTrue(all(len(n) == 4 for n in topo["adj"].values()))
                for dst in topo["nodes"]:
                    path = bfs_route(topo, 0, dst)
                    self.assertIsNotNone(path)
                    self.assertEqual(path[0], 0)
                    self.assertEqual(path[-1], dst)

    def test_chords_add_edges_without_changing_nodes(self):
        base = make_icosphere(1)
        chorded = make_icosphere(1, add_chords=True, n_chords=5)
        self.assert_undirected_graph(chorded)
        self.assertEqual(chorded["nodes"], base["nodes"])
        self.assertEqual(sum(map(len, chorded["adj"].values())) -
                         sum(map(len, base["adj"].values())), 10)

    def test_torus_known_diameter(self):
        stats = topology_stats(make_torus(6, 7))
        self.assertEqual(stats["max_hops"], 6)  # floor(6/2) + floor(7/2)
        self.assertEqual(stats["nodes"], 42)

    def test_chords_use_straight_lengths_and_surface_neighbors_keep_arc_lengths(self):
        base = make_icosphere(1, radius_mm=40)
        chorded = make_icosphere(1, radius_mm=40, add_chords=True, n_chords=5)
        new_edges = set(chorded["link_length"]) - set(base["link_length"])
        self.assertEqual(len(new_edges), 10)  # Five undirected physical chords.
        for src, dst in new_edges:
            straight = math.sqrt(sum((x-y)**2 for x, y in
                                     zip(chorded["coords"][src], chorded["coords"][dst])))
            self.assertAlmostEqual(chorded["link_length"][src, dst], straight)
            self.assertAlmostEqual(straight, 80)
        for edge, length in base["link_length"].items():
            self.assertAlmostEqual(chorded["link_length"][edge], length)

    def test_degenerate_tori_are_simple_usable_graphs(self):
        for rows, cols in ((1, 1), (1, 2), (2, 1), (1, 4), (4, 1), (2, 2)):
            with self.subTest(rows=rows, cols=cols):
                topo = make_torus(rows, cols)
                self.assert_undirected_graph(topo)
                FabricSim(topo)
                self.assertEqual(set(topo["link_length"]),
                                 {(src, dst) for src in topo["adj"]
                                  for dst in topo["adj"][src]})

    def test_empty_and_singleton_stats_do_not_invent_pairwise_hops(self):
        empty = topology_stats({"nodes": [], "adj": {}, "link_length": {}})
        singleton = topology_stats(make_torus(1, 1))
        for stats in (empty, singleton):
            self.assertEqual(stats["reachable_pairs"], 0)
            self.assertEqual(stats["unreachable_pairs"], 0)
            self.assertIsNone(stats["avg_hops"])
            self.assertIsNone(stats["p99_hops"])
            self.assertEqual(stats["hop_dist"], [])
        self.assertEqual(empty["nodes"], 0)
        self.assertEqual(singleton["nodes"], 1)

    def test_disconnected_stats_keep_unreachable_pairs_visible(self):
        topo = {"nodes": [0, 1, 2], "adj": {0: [1], 1: [0], 2: []},
                "link_length": {(0, 1): 4, (1, 0): 4}}
        stats = topology_stats(topo)
        self.assertFalse(stats["connected"])
        self.assertEqual(stats["reachable_pairs"], 2)
        self.assertEqual(stats["unreachable_pairs"], 4)
        self.assertEqual(stats["hop_dist"], [1, 1])


class GeneratorValidationTests(unittest.TestCase):
    def test_icosphere_rejects_invalid_subdivision_radius_and_chord_budget(self):
        cases = ({"subdivisions": -1}, {"subdivisions": 0.5},
                 {"subdivisions": True}, {"radius_mm": 0},
                 {"radius_mm": float("nan")}, {"radius_mm": float("inf")},
                 {"n_chords": -1},
                 {"subdivisions": 0, "add_chords": True, "n_chords": 7})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                make_icosphere(**kwargs)

    def test_torus_rejects_invalid_dimensions_and_link_lengths(self):
        for value in (0, -1, 1.5, True):
            for dimension in ("rows", "cols"):
                kwargs = {"rows": 3, "cols": 3, dimension: value}
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    make_torus(**kwargs)
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(length=value), self.assertRaises(ValueError):
                make_torus(3, 3, link_length_mm=value)

    def test_grouped_graph_rejects_invalid_dimensions(self):
        for value in (0, -1, 1.5, True):
            for dimension in ("n_groups", "routers_per_group"):
                with self.subTest(dimension=dimension, value=value), self.assertRaises(ValueError):
                    make_dragonfly(**{dimension: value})

    def test_single_group_graph_has_no_self_links(self):
        for routers in (1, 2, 4):
            topo = make_dragonfly(1, routers)
            FabricSim(topo)
            for src, neighbors in topo["adj"].items():
                self.assertNotIn(src, neighbors)


if __name__ == "__main__":
    unittest.main()
