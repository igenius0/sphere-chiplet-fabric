"""Independent, held-out checks across the bounded prototype components.

These tests replay public traces and evaluate a separate scalar numerical
oracle. They do not import private simulator helpers or certify hardware.
"""
from collections import Counter, defaultdict, deque
from dataclasses import replace
import itertools
import math
from pathlib import Path
import random
import unittest
from unittest import mock

import numpy as np

from sim.finite_buffer import (NetworkConfig, Packet, routing_dependency_report,
                               simulate_network)
from sim.moe_inference import (InferenceConfig, IslandDecoder, create_weights,
                               reference_logits)
from sim.transport import Fault, Frame, ReliableTransport, TransportConfig


ROOT = Path(__file__).resolve().parents[1]


def graph(nodes, edges):
    adjacency = {node: [] for node in nodes}
    for source, destination in edges:
        adjacency[source].append(destination)
        adjacency[destination].append(source)
    return {'nodes': list(nodes), 'adj': adjacency}


def scalar_decoder_oracle(weights, tokens, config):
    """Definition-level scalar attention and dense all-expert evaluation.

    No cache, island workspace, einsum, network, or production norm helper is
    used. Every prefix position attends to an explicitly enumerated history.
    Returns logits plus the final residual and weighted contribution vectors.
    """
    d, heads = config.model_dim, config.attention_heads
    width = d // heads

    def norm(vector, scale):
        mean = math.fsum(float(value) for value in vector) / len(vector)
        centered = [float(value) - mean for value in vector]
        variance = math.fsum(value * value for value in centered) / len(vector)
        return np.array([value / math.sqrt(variance + config.layer_norm_epsilon)
                         for value in centered]) * scale

    def probabilities(vector):
        maximum = max(vector)
        numerator = [math.exp(float(value) - maximum) for value in vector]
        total = math.fsum(numerator)
        return np.array([value / total for value in numerator])

    embeddings = [weights.embedding[token] + weights.position[position]
                  for position, token in enumerate(tokens)]
    normalized = [norm(value, weights.ln1_scale) for value in embeddings]
    queries = [value @ weights.wq for value in normalized]
    keys = [value @ weights.wk for value in normalized]
    values = [value @ weights.wv for value in normalized]
    logits = []
    for position, embedding in enumerate(embeddings):
        context = np.zeros(d)
        for head in range(heads):
            span = slice(head * width, (head + 1) * width)
            scores = [math.fsum(float(a) * float(b) for a, b in
                                zip(queries[position][span], keys[previous][span]))
                      / math.sqrt(width) for previous in range(position + 1)]
            attention = probabilities(scores)
            for coordinate in range(head * width, (head + 1) * width):
                context[coordinate] = math.fsum(
                    float(attention[previous]) * float(values[previous][coordinate])
                    for previous in range(position + 1))
        residual = embedding + context @ weights.wo
        expert_input = norm(residual, weights.ln2_scale)
        gates = expert_input @ weights.gate
        selected = sorted(range(config.num_experts),
                          key=lambda expert: (-float(gates[expert]), expert))[:config.top_k]
        all_outputs = [(np.maximum(expert_input @ weights.expert_w1[expert], 0)
                        @ weights.expert_w2[expert])
                       for expert in range(config.num_experts)]
        weights_selected = probabilities([gates[expert] for expert in selected])
        contributions = {expert: weights_selected[index] * all_outputs[expert]
                         for index, expert in enumerate(selected)}
        combined = np.array([math.fsum(float(vector[index])
                                      for vector in contributions.values())
                             for index in range(d)])
        final = norm(residual + combined, weights.final_scale) @ weights.lm_head
        logits.append(final)
    return np.stack(logits), residual, contributions, norm


class TraceReplay(unittest.TestCase):
    def assert_trace(self, topology, packets, report):
        """Reconstruct storage, readiness and credits solely from public events."""
        config = report['configuration']
        capacity = config['buffer_flits_per_vc']
        routes = {item['packet_id']: item['route'] for item in report['packet_routes']}
        packet_by_id = {packet.packet_id: packet for packet in packets}
        credits = {(source, destination, vc): capacity
                   for source in topology['nodes'] for destination in topology['adj'][source]
                   for vc in range(config['virtual_channels'])}
        buffers, flying, returns = defaultdict(deque), {}, Counter()
        seen, delivered, grants, outputs = set(), set(), set(), set()
        injections, ejections = Counter(), Counter()
        previous_cycle = -1

        def free_input(key, identity, cycle):
            self.assertEqual(buffers[key][0][0], identity, 'FIFO order')
            _, ready, position = buffers[key].popleft()
            self.assertGreaterEqual(cycle, ready, 'router readiness')
            physical_input = (cycle, key[0], key[1])
            self.assertNotIn(physical_input, grants, 'one grant per physical input')
            grants.add(physical_input)
            if key[1] is not None:
                returns[(cycle + config['credit_latency_cycles'],
                         key[1], key[0], key[2])] += 1
            return position

        for event in report['trace']:
            cycle, kind = event['cycle'], event['event']
            self.assertGreaterEqual(cycle, previous_cycle)
            previous_cycle = cycle
            if kind == 'credit':
                key = (event['source'], event['destination'], event['vc'])
                due = (cycle, *key)
                self.assertGreater(returns[due], 0, 'credit returned at its exact due cycle')
                returns[due] -= 1
                credits[key] += 1
            else:
                identity = (event['packet_id'], event['flit_index'])
                packet = packet_by_id[identity[0]]
                route = routes[identity[0]]
                vc = event['vc']
                self.assertEqual(vc, packet.vc, 'no VC transition')
                if kind == 'injection':
                    self.assertNotIn(identity, seen)
                    self.assertGreaterEqual(cycle, packet.release_cycle)
                    self.assertLess(identity[1], packet.flits)
                    self.assertEqual(event['node'], packet.source)
                    seen.add(identity)
                    key = (event['node'], None, vc)
                    buffers[key].append((identity, cycle + config['router_latency_cycles'], 0))
                    injections[(cycle, event['node'])] += 1
                    self.assertLessEqual(injections[(cycle, event['node'])],
                                         config['injection_flits_per_cycle'])
                elif kind == 'arrival':
                    source, destination, arrival, position = flying.pop(identity)
                    self.assertEqual((event['source'], event['destination'], cycle),
                                     (source, destination, arrival))
                    buffers[(destination, source, vc)].append(
                        (identity, cycle + config['router_latency_cycles'], position))
                else:
                    node = event['source'] if kind == 'send' else event['node']
                    candidates = [key for key, queue in buffers.items()
                                  if key[0] == node and key[2] == vc and queue
                                  and queue[0][0] == identity]
                    self.assertEqual(len(candidates), 1, 'flit is resident at its current router')
                    key = candidates[0]
                    position = free_input(key, identity, cycle)
                    self.assertEqual(route[position], node)
                    if kind == 'send':
                        destination = event['destination']
                        self.assertEqual(route[position + 1], destination)
                        self.assertIn(destination, topology['adj'][node])
                        link_use = (cycle, node, destination)
                        self.assertNotIn(link_use, outputs, 'one flit per directed link per cycle')
                        outputs.add(link_use)
                        credits[(node, destination, vc)] -= 1
                        flying[identity] = (node, destination,
                                            cycle + config['link_latency_cycles'], position + 1)
                    else:
                        self.assertEqual(kind, 'ejection')
                        self.assertEqual(position, len(route) - 1)
                        self.assertEqual(node, packet.destination)
                        self.assertFalse(any(node == paused and start <= cycle
                                             and (end is None or cycle < end)
                                             for paused, start, end in config['paused_sinks']))
                        self.assertNotIn(identity, delivered)
                        delivered.add(identity)
                        ejections[(cycle, node)] += 1
                        self.assertLessEqual(ejections[(cycle, node)],
                                             config['ejection_flits_per_cycle'])
            resident = [identity for queue in buffers.values() for identity, _, _ in queue]
            self.assertEqual(len(resident), len(set(resident)))
            self.assertFalse(set(resident) & set(flying))
            self.assertEqual(set(resident) | set(flying) | delivered, seen)
            self.assertFalse((set(resident) | set(flying)) & delivered)
            for queue in buffers.values():
                self.assertLessEqual(len(queue), capacity)
            for (source, destination, vc), available in credits.items():
                self.assertGreaterEqual(available, 0)
                self.assertLessEqual(available, capacity)
                resident_count = len(buffers[(destination, source, vc)])
                flying_count = sum((a, b, packet_by_id[identity[0]].vc) ==
                                   (source, destination, vc)
                                   for identity, (a, b, _, _) in flying.items())
                pending_count = sum(count for (_, a, b, channel), count in returns.items()
                                    if (a, b, channel) == (source, destination, vc))
                self.assertEqual(available + resident_count + flying_count + pending_count,
                                 capacity, 'independently replayed reserved-space conservation')
        accounting = report['accounting']
        self.assertEqual(accounting['injected_flits'], len(seen))
        self.assertEqual(accounting['delivered_flits'], len(delivered))
        self.assertEqual(accounting['inflight_flits'], len(flying))
        self.assertEqual(accounting['buffered_flits'], sum(map(len, buffers.values())))
        for entry in report['credits']:
            key = (entry['source'], entry['destination'], entry['vc'])
            self.assertEqual(entry['available'], credits[key])
            self.assertEqual(entry['resident'], len(buffers[(key[1], key[0], key[2])]))
            self.assertEqual(entry['inflight'], sum(
                (a, b, packet_by_id[identity[0]].vc) == key
                for identity, (a, b, _, _) in flying.items()))
            self.assertEqual(entry['return_pending'], sum(
                count for (_, a, b, channel), count in returns.items()
                if (a, b, channel) == key))
            self.assertEqual(sum(entry[field] for field in
                                 ('available', 'resident', 'inflight', 'return_pending')),
                             capacity)
        self.assertEqual({(entry['source'], entry['destination'], entry['vc'])
                          for entry in report['credits']}, set(credits))
        self.assertEqual(Counter((entry['return_cycle'], entry['source'],
                                  entry['destination'], entry['vc'])
                                 for entry in report['credit_returns']), +returns)
        return delivered

    def test_held_out_traffic_trace_replay(self):
        for seed in range(100, 110):
            rng = random.Random(seed)
            nodes = [11 + 3 * index for index in range(3 + seed % 3)]
            edges = [(nodes[index - 1], nodes[index]) for index in range(1, len(nodes))]
            edges += [edge for edge in itertools.combinations(nodes, 2)
                      if edge not in edges and rng.random() < 0.4]
            topology = graph(nodes, edges)
            packets = [Packet(index, rng.choice(nodes), rng.choice(nodes),
                              rng.randint(1, 9), rng.randint(0, 8), rng.randrange(2))
                       for index in range(15)]
            config = NetworkConfig(buffer_flits_per_vc=rng.randint(1, 3),
                                   link_latency_cycles=rng.randint(1, 3),
                                   credit_latency_cycles=rng.randint(1, 4),
                                   router_latency_cycles=rng.randint(1, 3),
                                   injection_flits_per_cycle=rng.randint(1, 3),
                                   ejection_flits_per_cycle=rng.randint(1, 2),
                                   paused_sinks=((nodes[-1], 3, 15),),
                                   max_cycles=5000, record_trace=True)
            with self.subTest(seed=seed):
                report = simulate_network(topology, packets, config)
                self.assertEqual(report['status'], 'completed')
                delivered = self.assert_trace(topology, packets, report)
                self.assertEqual(len(delivered), sum(packet.flits for packet in packets))

    def test_hand_derived_single_credit_timing(self):
        topology = graph([0, 1], [(0, 1)])
        for router, link, credit in itertools.product((1, 2, 3), (1, 2), (1, 3)):
            packet = Packet(23, 0, 1, 7, release_cycle=5)
            report = simulate_network(topology, [packet], NetworkConfig(
                buffer_flits_per_vc=1, router_latency_cycles=router,
                link_latency_cycles=link, credit_latency_cycles=credit,
                record_trace=True))
            with self.subTest(router=router, link=link, credit=credit):
                sends = [event['cycle'] for event in report['trace'] if event['event'] == 'send']
                period = link + router + credit
                self.assertEqual(sends, [5 + router + index * period for index in range(7)])
                completion = 5 + 2 * router + link + 6 * period
                self.assertEqual(report['deliveries'][0]['completion_cycle'], completion)
                self.assert_trace(topology, [packet], report)

    def test_large_credit_window_reaches_one_flit_per_cycle(self):
        topology = graph([0, 1], [(0, 1)])
        router, link, credit, count = 3, 2, 4, 31
        packet = Packet(4, 0, 1, count)
        report = simulate_network(topology, [packet], NetworkConfig(
            buffer_flits_per_vc=router + link + credit,
            router_latency_cycles=router, link_latency_cycles=link,
            credit_latency_cycles=credit, record_trace=True))
        sends = [event['cycle'] for event in report['trace'] if event['event'] == 'send']
        self.assertEqual(sends, list(range(router, router + count)))
        self.assertEqual(report['deliveries'][0]['completion_cycle'],
                         2 * router + link + count - 1)
        self.assert_trace(topology, [packet], report)

    def test_permanent_pause_and_disconnected_traffic_keep_all_ledgers(self):
        topology = graph([0, 1, 9], [(0, 1)])
        packets = [Packet(1, 0, 1, 20), Packet(2, 0, 9, 7), Packet(3, 9, 9, 3)]
        report = simulate_network(topology, packets, NetworkConfig(
            buffer_flits_per_vc=2, link_latency_cycles=3,
            credit_latency_cycles=4, paused_sinks=((1, 0, None),),
            max_cycles=30, record_trace=True))
        self.assertEqual(report['status'], 'max_cycles')
        self.assertEqual([entry['packet_id'] for entry in report['rejected_packets']], [2])
        delivered = self.assert_trace(topology, packets, report)
        self.assertEqual(delivered, {(3, index) for index in range(3)})
        self.assertEqual(report['accounting']['requested_flits'], 30)
        self.assertTrue(report['accounting']['conservation_ok'])
        self.assertGreater(report['stalls']['output_no_credit'], 0)
        self.assertGreater(report['stalls']['injection_buffer_full'], 0)

    def test_delivery_is_distinct_from_final_credit_quiescence(self):
        topology = graph([0, 1], [(0, 1)])
        packet = Packet(19, 0, 1, 1, release_cycle=4)
        config = NetworkConfig(router_latency_cycles=2, link_latency_cycles=3,
                               credit_latency_cycles=7, buffer_flits_per_vc=1,
                               max_cycles=12, record_trace=True)
        # Inject 4, send 6, arrive 9, eject 11, return credit 18. The
        # packet is delivered at the cutoff but the input reservation is live.
        cutoff = simulate_network(topology, [packet], config)
        self.assertEqual(cutoff['deliveries'][0]['completion_cycle'], 11)
        self.assertTrue(cutoff['all_requested_delivered'])
        self.assertEqual(cutoff['pending_packets'], [])
        self.assertEqual(cutoff['status'], 'max_cycles')
        self.assertFalse(cutoff['idle'])
        self.assertEqual(cutoff['cutoff_reason'], 'pending_credit_returns')
        self.assertEqual(cutoff['credit_returns'],
                         [{'return_cycle': 18, 'source': 0, 'destination': 1, 'vc': 0}])
        self.assert_trace(topology, [packet], cutoff)
        drained = simulate_network(topology, [packet], replace(config, max_cycles=19))
        self.assertEqual(drained['elapsed_cycles'], 19)
        self.assertTrue(drained['idle'])
        self.assertEqual(drained['status'], 'completed')
        self.assert_trace(topology, [packet], drained)


class DependencyCertificate(unittest.TestCase):
    def test_all_four_node_graphs_have_complete_acyclic_fixed_route_certificate(self):
        nodes = [2, 5, 9, 17]
        possible = list(itertools.combinations(nodes, 2))
        for mask in range(1 << len(possible)):
            edges = [edge for bit, edge in enumerate(possible) if mask & (1 << bit)]
            topology = graph(nodes, edges)
            certificate = routing_dependency_report(topology)
            tree = graph(nodes, certificate['tree_edges'])['adj']
            expected, route_count = set(), 0
            for source, destination in itertools.permutations(nodes, 2):
                queue = [(source, [source])]
                paths = []
                while queue:
                    current, path = queue.pop()
                    if current == destination:
                        paths.append(path)
                    queue += [(other, path + [other]) for other in tree[current]
                              if other not in path]
                self.assertLessEqual(len(paths), 1)
                if paths:
                    route_count += 1
                    channels = list(zip(paths[0], paths[0][1:]))
                    expected.update(zip(channels, channels[1:]))
            actual = {(tuple(entry['before']), tuple(entry['after']))
                      for entry in certificate['dependencies']}
            with self.subTest(graph_mask=mask):
                self.assertEqual(actual, expected)
                self.assertEqual(certificate['checked_nonlocal_routes'], route_count)
                ranks = {(entry['source'], entry['destination']): entry['rank']
                         for entry in certificate['channel_order']}
                self.assertTrue(all(ranks[first] < ranks[second] for first, second in actual))
                successors = defaultdict(set)
                indegree = {channel: 0 for channel in ranks}
                for first, second in actual:
                    successors[first].add(second)
                    indegree[second] += 1
                ready = [channel for channel, degree in indegree.items() if degree == 0]
                visited = 0
                while ready:
                    channel = ready.pop()
                    visited += 1
                    for successor in successors[channel]:
                        indegree[successor] -= 1
                        if indegree[successor] == 0:
                            ready.append(successor)
                self.assertEqual(visited, len(ranks), 'independent Kahn DAG check')


class NumericalAndByteIntegration(unittest.TestCase):
    def test_held_out_cached_logits_schedule_and_memory_accounting(self):
        for seed in range(100, 110):
            config = InferenceConfig(seed=seed, model_dim=4 + 2 * (seed % 3),
                                     attention_heads=2, expert_hidden=5 + seed % 4,
                                     num_experts=4 + seed % 6, top_k=1 + seed % 4,
                                     vocab_size=13, max_seq_len=9,
                                     prompt_tokens=(1, 2), decode_tokens=3,
                                     flit_bytes=17 + seed % 3, buffer_flits_per_vc=1)
            weights = create_weights(config)
            decoder = IslandDecoder(weights, config)
            rng = random.Random(seed)
            tokens = [rng.randrange(config.vocab_size) for _ in range(5)]
            actual = list(decoder.prefill(tokens[:2]))
            actual += [decoder.step(token) for token in tokens[2:]]
            oracle, _, _, _ = scalar_decoder_oracle(weights, tokens, config)
            with self.subTest(seed=seed):
                np.testing.assert_allclose(actual, oracle, rtol=1e-10, atol=1e-10)
                np.testing.assert_allclose(reference_logits(weights, tokens, config), oracle,
                                           rtol=1e-10, atol=1e-10)
                report = decoder.report()
                self.assertEqual(report['contributions_completed'], len(tokens) * config.top_k)
                contribution_ids = set()
                prior_end = 0
                for position, trace in enumerate(report['token_traces']):
                    self.assertEqual(trace['start_cycle'], prior_end)
                    prior_end = trace['end_cycle']
                    self.assertEqual(trace['contributions_completed'], config.top_k)
                    self.assertEqual(len(trace['assignments']), config.top_k)
                    local = sum(assignment['expert'] % 4 == 0 for assignment in trace['assignments'])
                    byte_count = config.model_dim * np.dtype('float64').itemsize
                    self.assertEqual(trace['bytes']['dispatch_remote_payload'],
                                     (config.top_k - local) * byte_count)
                    self.assertEqual(trace['bytes']['combine_remote_payload'],
                                     (config.top_k - local) * byte_count)
                    self.assertEqual(trace['bytes']['dispatch_local_bypass'], local * byte_count)
                    self.assertEqual(trace['bytes']['combine_local_bypass'], local * byte_count)
                    self.assertEqual(trace['bytes']['kv_local_read'], position * 2 * byte_count)
                    self.assertEqual(trace['bytes']['kv_local_write'], 2 * byte_count)
                    expected_reads = [0] * 4
                    island_end = [trace['dispatch_barrier_cycle']] * 4
                    remote = set()
                    for assignment in trace['assignments']:
                        expert, island = assignment['expert'], assignment['island']
                        self.assertEqual(island, expert % 4)
                        identity = tuple(assignment['contribution_id'])
                        self.assertEqual(identity, (config.generation, position, expert))
                        self.assertNotIn(identity, contribution_ids)
                        contribution_ids.add(identity)
                        self.assertGreaterEqual(assignment['compute_start_cycle'], island_end[island])
                        island_end[island] = assignment['compute_end_cycle']
                        self.assertGreaterEqual(assignment['combine_completion_cycle'],
                                                assignment['compute_end_cycle'])
                        self.assertTrue(assignment['completed_exactly_once'])
                        expected_reads[island] += 2 * config.model_dim * config.expert_hidden * 8
                        if island:
                            remote.add(expert)
                    self.assertEqual(trace['bytes']['expert_local_weight_reads_by_island'], expected_reads)
                    for phase in ('dispatch', 'combine'):
                        network = trace['network'][phase]
                        self.assertEqual(network['status'], 'completed')
                        self.assertTrue(network['idle'])
                        self.assertEqual(network['pending_packets'], [])
                        self.assertEqual(network['rejected_packets'], [])
                        self.assertEqual(network['credit_returns'], [])
                        self.assertTrue(all(credit['available'] == credit['capacity']
                                            for credit in network['credits']))
                        self.assertEqual({entry['packet_id'] for entry in network['deliveries']}, remote)
                        if remote:
                            self.assertEqual(network['accounting']['delivered_flits'],
                                             len(remote) * math.ceil(byte_count / config.flit_bytes))
                d, h, n, e, v = (config.model_dim, config.expert_hidden,
                                 config.max_seq_len, config.num_experts, config.vocab_size)
                base_weights = 2 * v * d + n * d + 4 * d * d + d * e + 3 * d
                for island in report['memory']['islands']:
                    owner = island['island']
                    expert_count = sum(expert % 4 == owner for expert in range(e))
                    resident = 2 * expert_count * d * h + (base_weights if owner == 0 else 0)
                    workspace = 2 * d + h
                    if owner == 0:
                        workspace += n * config.attention_heads + e + config.top_k * d + d + v
                    self.assertEqual(island['resident_weights_bytes'], resident * 8)
                    self.assertEqual(island['kv_cache_bytes'], 2 * n * d * 8 if owner == 0 else 0)
                    self.assertEqual(island['reserved_workspace_bytes'], workspace * 8)

    def test_dispatch_and_combine_wait_for_real_credit_drain(self):
        config = InferenceConfig(model_dim=6, attention_heads=2, expert_hidden=7,
                                 num_experts=4, top_k=4, flit_bytes=13,
                                 buffer_flits_per_vc=1)
        weights = create_weights(config)
        weights.gate[:] = 0.0

        def slow_credit_network(topology, packets, network_config):
            return simulate_network(topology, packets, replace(
                network_config, router_latency_cycles=2, link_latency_cycles=3,
                credit_latency_cycles=7))

        decoder = IslandDecoder(weights, config)
        with mock.patch('sim.finite_buffer.simulate_network', side_effect=slow_credit_network):
            actual = decoder.step(2)
        expected, _, _, _ = scalar_decoder_oracle(weights, [2], config)
        np.testing.assert_allclose(actual, expected[0], rtol=1e-10, atol=1e-10)
        trace = decoder.traces[0]
        for phase in ('dispatch', 'combine'):
            network = trace['network'][phase]
            last_delivery = max(entry['completion_cycle'] for entry in network['deliveries'])
            self.assertEqual(network['elapsed_cycles'], last_delivery + 7 + 1)
            self.assertTrue(network['idle'])
            self.assertEqual(network['credit_returns'], [])
        dispatch_end = (trace['dispatch_start_cycle']
                        + trace['network']['dispatch']['elapsed_cycles'])
        self.assertEqual(trace['dispatch_barrier_cycle'], dispatch_end)
        self.assertTrue(all(assignment['compute_start_cycle'] >= dispatch_end
                            for assignment in trace['assignments']))
        self.assertEqual(trace['combine_barrier_cycle'],
                         dispatch_end + trace['network']['combine']['elapsed_cycles'])

    def test_cached_decoder_fault_channel_matches_scalar_prefixes(self):
        config = InferenceConfig(seed=109, model_dim=6, attention_heads=2,
                                 expert_hidden=7, num_experts=8, top_k=6,
                                 vocab_size=11, max_seq_len=4,
                                 prompt_tokens=(1,), decode_tokens=0)
        weights = create_weights(config)
        weights.gate[:] = 0.0
        transport = ReliableTransport(generation=config.generation, faults=(
            Fault('drop', 'DATA', request_id='token-0-expert-1', attempt=0),
            Fault('corrupt', 'DATA', request_id='token-1-expert-2', attempt=0),
            Fault('drop', 'ACK', request_id='token-2-expert-3', attempt=0),
            Fault('duplicate', 'DATA', request_id='token-1-expert-5', copies=2),
            Fault('reorder', 'DATA', request_id='token-2-expert-5', delay_ticks=1)))
        decoder = IslandDecoder(weights, config, contribution_transport=transport)
        tokens = [4, 1, 6]
        actual = decoder.prefill(tokens)
        expected, _, _, _ = scalar_decoder_oracle(weights, tokens, config)
        np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-10)
        self.assertEqual(decoder.report()['contributions_completed'], len(tokens) * config.top_k)
        self.assertEqual(transport.report()['completed'], 12)
        self.assertEqual(transport.report()['stats']['receiver_executions'], 12)
        self.assertEqual(transport.report()['pending'], 0)
        self.assertEqual(transport.report()['reserved_bytes'], 0)
        for position in range(len(tokens)):
            _, _, contributions, _ = scalar_decoder_oracle(weights, tokens[:position+1], config)
            for expert, weighted_output in contributions.items():
                if expert % 4 == 0:
                    continue
                request_id = f'token-{position}-expert-{expert}'
                payloads = transport.delivered_payloads(request_id)
                self.assertEqual(len(payloads), 1)
                self.assertEqual(payloads[0][0], 0)
                actual_output = np.frombuffer(payloads[0][1], dtype=np.float64)
                np.testing.assert_allclose(actual_output, weighted_output * config.top_k,
                                           rtol=1e-10, atol=1e-10)
                self.assertEqual(transport.request_result(request_id)['receiver_execute_count'], 1)

    def test_lost_second_token_acks_preserve_committed_prefix(self):
        config = InferenceConfig(seed=108, num_experts=4, top_k=4)
        weights = create_weights(config)
        weights.gate[:] = 0.0
        faults = tuple(Fault('drop', 'ACK', request_id=f'token-1-expert-{expert}')
                       for expert in (1, 2, 3))
        transport = ReliableTransport(generation=config.generation, faults=faults)
        decoder = IslandDecoder(weights, config, contribution_transport=transport)
        first_logits = decoder.step(3)
        expected, _, _, _ = scalar_decoder_oracle(weights, [3], config)
        np.testing.assert_allclose(first_logits, expected[0], rtol=1e-10, atol=1e-10)
        keys, values = decoder.cache.keys.copy(), decoder.cache.values.copy()
        committed_cycle = decoder.cycle
        with self.assertRaises(ValueError):
            decoder.step(2)
        np.testing.assert_array_equal(decoder.cache.keys, keys)
        np.testing.assert_array_equal(decoder.cache.values, values)
        self.assertEqual(decoder.cache.length, 1)
        self.assertEqual(decoder.tokens, [3])
        self.assertEqual(len(decoder.traces), 1)
        self.assertEqual(decoder.cycle, committed_cycle)
        np.testing.assert_array_equal(decoder.workspace[0]['logits'], first_logits)
        report = transport.report()
        self.assertEqual((report['completed'], report['aborted'], report['pending']), (3, 1, 0))
        self.assertEqual(report['reserved_bytes'], 0)
        self.assertEqual(report['stats']['receiver_executions'], 4)
        self.assertEqual(report['submitted'], 4, 'stop admission after the first failed remote')
        result = transport.request_result('token-1-expert-1')
        self.assertTrue(result['uncertain_delivery'])
        self.assertEqual(result['receiver_execute_count'], 1)

    def test_single_inflight_transport_admits_remote_experts_without_leak(self):
        config = InferenceConfig(seed=108, num_experts=4, top_k=4)
        weights = create_weights(config)
        weights.gate[:] = 0.0
        transport = ReliableTransport(TransportConfig(max_inflight=1),
                                      generation=config.generation)
        decoder = IslandDecoder(weights, config, contribution_transport=transport)
        actual = decoder.step(1)
        expected, _, _, _ = scalar_decoder_oracle(weights, [1], config)
        np.testing.assert_allclose(actual, expected[0], rtol=1e-10, atol=1e-10)
        report = transport.report()
        self.assertEqual((report['completed'], report['pending'], report['aborted']), (3, 0, 0))
        self.assertEqual(report['reserved_bytes'], 0)
        self.assertEqual(report['queued_events'], 0)
        self.assertEqual(decoder.cache.length, 1)

    def test_finite_transport_capacity_failure_keeps_no_live_work_or_partial_token(self):
        config = InferenceConfig(seed=108, num_experts=4, top_k=4)
        weights = create_weights(config)
        weights.gate[:] = 0.0
        transport = ReliableTransport(TransportConfig(max_requests=1, max_inflight=1),
                                      generation=config.generation)
        decoder = IslandDecoder(weights, config, contribution_transport=transport)
        with self.assertRaises(ValueError):
            decoder.step(1)
        report = transport.report()
        self.assertEqual(report['completed'], 1)
        self.assertEqual(report['pending'], 0)
        self.assertEqual(report['reserved_bytes'], 0)
        self.assertEqual(report['queued_events'], 0)
        self.assertEqual(decoder.cache.length, 0)
        self.assertEqual(decoder.tokens, [])
        self.assertEqual(decoder.traces, [])
        self.assertEqual(decoder.cycle, 0)
        self.assertFalse(np.any(decoder.cache.keys))
        self.assertFalse(np.any(decoder.cache.values))

    def test_real_moe_contribution_vectors_survive_transport_recovery(self):
        config = InferenceConfig(seed=107, model_dim=8, attention_heads=2,
                                 num_experts=8, top_k=4, expert_hidden=9)
        weights = create_weights(config)
        tokens = [3, 6, 2]
        oracle, residual, contributions, norm = scalar_decoder_oracle(weights, tokens, config)
        transport = ReliableTransport(generation=config.generation, faults=(
            Fault('corrupt', 'DATA', attempt=0),
            Fault('duplicate', 'DATA', attempt=1, copies=2),
            Fault('drop', 'ACK', attempt=1),
            Fault('reorder', 'ACK', attempt=2, delay_ticks=1)))
        for expert, contribution in contributions.items():
            transport.submit(f'token-2-expert-{expert}', expert % 4, 0,
                             [(expert, contribution.astype('<f8').tobytes())],
                             reservation_bytes=contribution.nbytes)
        report = transport.run()
        self.assertEqual(report['completed'], config.top_k)
        self.assertEqual(report['reserved_bytes'], 0)
        self.assertEqual(report['stats']['receiver_executions'], config.top_k)
        recovered = []
        for expert in contributions:
            payloads = transport.delivered_payloads(f'token-2-expert-{expert}')
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0][0], expert)
            recovered.append(np.frombuffer(payloads[0][1], dtype='<f8'))
        combined = np.sum(recovered, axis=0)
        recovered_logits = norm(residual + combined, weights.final_scale) @ weights.lm_head
        np.testing.assert_allclose(recovered_logits, oracle[-1], rtol=1e-10, atol=1e-10)
        decoder = IslandDecoder(weights, config)
        np.testing.assert_allclose(decoder.prefill(tokens)[-1], recovered_logits,
                                   rtol=1e-10, atol=1e-10)


class ProtocolOutcomes(unittest.TestCase):
    def test_fault_pairs_preserve_payloads_and_execute_once(self):
        data_faults = ('drop', 'corrupt', 'duplicate', 'reorder')
        ack_faults = ('drop', 'corrupt', 'duplicate', 'reorder')
        for data_action, ack_action in itertools.product(data_faults, ack_faults):
            faults = tuple(Fault(action, kind, sequence=sequence, attempt=0,
                                 delay_ticks=1 if action == 'reorder' else 0)
                           for action, kind, sequence in
                           ((data_action, 'DATA', 0), (ack_action, 'ACK', 1)))
            transport = ReliableTransport(faults=faults)
            expected = ((0, b'\x00\xfffirst'), (1, b'second\x00'), (2, b''))
            transport.submit('held-out', 3, 9, expected, reservation_bytes=177)
            report = transport.run()
            with self.subTest(data=data_action, ack=ack_action):
                self.assertEqual(report['completed'], 1)
                self.assertEqual(report['aborted'], 0)
                self.assertEqual(report['queued_events'], 0)
                self.assertEqual(report['reserved_bytes'], 0)
                self.assertEqual(transport.delivered_payloads('held-out'), expected)
                result = transport.request_result('held-out')
                self.assertEqual(result['receiver_execute_count'], len(expected))
                self.assertFalse(result['uncertain_delivery'])
                self.assertTrue(all(1 <= attempts <= 3 for attempts in result['attempts'].values()))

    def test_ack_loss_is_uncertain_even_when_every_contribution_executed_once(self):
        transport = ReliableTransport(faults=(Fault('drop', 'ACK'),))
        expected = ((1, b'alpha'), (5, b'beta'))
        transport.submit('uncertain', 1, 2, expected, reservation_bytes=9)
        report = transport.run()
        result = transport.request_result('uncertain')
        self.assertEqual(result['status'], 'aborted')
        self.assertEqual(result['reason'], 'retry_exhausted')
        self.assertTrue(result['uncertain_delivery'])
        self.assertEqual(result['acked_sequences'], [])
        self.assertEqual(result['receiver_execute_count'], 2)
        self.assertEqual(transport.delivered_payloads('uncertain'), expected)
        self.assertEqual(set(result['attempts'].values()), {3})
        self.assertEqual(report['reserved_bytes'], 0)

    def test_crc_header_coverage_and_ack_provenance(self):
        frame = Frame.create('crc-scope', 3, 7, 2, 9, b'')
        variants = (replace(frame, generation=4), replace(frame, sequence=8),
                    replace(frame, source=3), replace(frame, destination=10),
                    replace(frame, request_id='crc-scopf'), replace(frame, kind='ACK'),
                    replace(frame, payload=b'\x00'))
        for variant in variants:
            self.assertNotEqual(frame.crc32, variant.expected_crc32())
        transport = ReliableTransport(generation=3, faults=(Fault('drop', 'DATA', attempt=0),))
        transport.submit('crc-scope', 2, 9, [(7, b'')])
        transport.inject_frame(Frame.create('crc-scope', 3, 7, 9, 2, kind='ACK'))
        pending = transport.run(max_ticks=0)
        self.assertEqual(pending['completed'], 0, 'ACK before execution cannot complete')
        self.assertEqual(pending['stats']['protocol_errors'], 1)
        report = transport.run()
        self.assertEqual(report['completed'], 1)
        self.assertEqual(report['stats']['receiver_executions'], 1)

    def test_generation_barrier_prevents_delayed_old_payload_from_contaminating_reuse(self):
        transport = ReliableTransport(faults=(
            Fault('delay', 'DATA', generation=0, delay_ticks=9),))
        transport.submit('same-name', 0, 1, [(0, b'old')], reservation_bytes=13)
        transport.run(max_ticks=1)
        transport.advance_generation(1)
        transport.submit('same-name', 0, 1, [(0, b'new')], reservation_bytes=13)
        report = transport.run()
        self.assertEqual(transport.delivered_payloads('same-name', 0), ())
        self.assertEqual(transport.delivered_payloads('same-name', 1), ((0, b'new'),))
        self.assertEqual(transport.request_result('same-name', 0)['reason'], 'generation_changed')
        self.assertEqual(transport.request_result('same-name', 1)['status'], 'completed')
        self.assertGreater(report['stats']['stale_frames'], 0)
        self.assertEqual(report['reserved_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
