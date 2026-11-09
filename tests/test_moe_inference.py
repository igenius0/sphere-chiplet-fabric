"""Actual decoder numerics, dependency schedules, capacity and CLI contracts."""
from dataclasses import asdict, fields, replace
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

from sim.finite_buffer import simulate_network
from sim.moe_inference import (InferenceConfig, IslandDecoder, create_weights,
                               reference_logits, run_inference_demo,
                               run_inference_fault_demo)
from sim.transport import Fault, ReliableTransport, TransportConfig


ROOT = Path(__file__).resolve().parents[1]


def small_config(**values):
    defaults = dict(model_dim=8, attention_heads=2, expert_hidden=12,
                    num_experts=8, top_k=4, prompt_tokens=(1, 4, 2),
                    decode_tokens=2, max_seq_len=8)
    defaults.update(values)
    return InferenceConfig(**defaults)


class InferenceInputTests(unittest.TestCase):
    def test_invalid_configurations_rejected(self):
        cases = ([], '', 0, False, {'typo': 1}, {1: 2},
                 {'model_dim': 3, 'attention_heads': 2}, {'num_islands': 3},
                 {'top_k': 9}, {'prompt_tokens': []}, {'prompt_tokens': [True]},
                 {'prompt_tokens': [-1]}, {'prompt_tokens': [32]},
                 {'prompt_tokens': '123'}, {'max_seq_len': 7},
                 {'island_capacity_bytes': [100, 200, 300]},
                 {'island_capacity_bytes': [100, 200, 300, False]},
                 {'island_capacity_bytes': 1.5}, {'island_capacity_bytes': 0})
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                InferenceConfig.from_dict(value)

    def test_each_integer_field_rejects_boolean_fractional_and_nonfinite(self):
        names = [f.name for f in fields(InferenceConfig)
                 if f.name not in ('prompt_tokens', 'layer_norm_epsilon', 'island_capacity_bytes')]
        for name in names:
            for value in (True, 1.5, float('nan'), float('inf'), -1):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    InferenceConfig.from_dict({name: value})
        for value in (True, 0.0, -1.0, float('nan'), float('inf'), '1e-5'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                InferenceConfig(layer_norm_epsilon=value)

    def test_numpy_scalar_configuration_is_normalized_to_strict_json(self):
        c = small_config(seed=np.int64(100), model_dim=np.int64(8),
                         layer_norm_epsilon=np.float64(1e-5),
                         island_capacity_bytes=np.int64(100000))
        self.assertEqual(json.loads(json.dumps(asdict(c), allow_nan=False))['seed'], 100)
        self.assertEqual(run_inference_demo(c)['config']['model_dim'], 8)

    def test_token_sequences_rejected_before_mutating_cache(self):
        c = small_config()
        decoder = IslandDecoder(config=c)
        for values in ([], '12', 1, np.array(1), np.array([[1, 2]]),
                       [True], [1.0], [float('nan')], [-1], [c.vocab_size], [1, False]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                decoder.prefill(values)
            with self.subTest(reference=values), self.assertRaises(ValueError):
                reference_logits(decoder.weights, values, c)
        self.assertEqual(decoder.cache.length, 0)
        self.assertEqual(decoder.tokens, [])
        self.assertEqual(decoder.prefill(np.array([1, 2], dtype=np.int64)).shape, (2, c.vocab_size))

    def test_invalid_model_arrays_rejected(self):
        c = small_config()
        for value in (np.zeros((1, 1)), np.full((c.vocab_size, c.model_dim), np.nan),
                       np.zeros((c.vocab_size, c.model_dim), dtype=bool),
                       np.zeros((c.vocab_size, c.model_dim), dtype=complex)):
            weights = create_weights(c)
            weights.embedding = value
            with self.subTest(dtype=value.dtype, shape=value.shape), self.assertRaises(ValueError):
                IslandDecoder(weights, c)
        with self.assertRaises(ValueError):
            IslandDecoder({'embedding': np.zeros((2, 2))}, c)

    def test_source_weights_are_copied_and_read_only(self):
        c = small_config()
        weights = create_weights(c)
        decoder = IslandDecoder(weights, c)
        expected = decoder.weights.embedding.copy()
        weights.embedding[:] = 0.0
        np.testing.assert_array_equal(decoder.weights.embedding, expected)
        with self.assertRaises(ValueError):
            decoder.weights.embedding[:] = 0.0


class InferenceNumericalTests(unittest.TestCase):
    def test_cached_prefill_decode_matches_independent_reference_seeds_100_to_109(self):
        for seed in range(100, 110):
            c = small_config(seed=seed, model_dim=8 if seed % 2 else 12,
                             attention_heads=2 if seed % 2 else 3,
                             top_k=1 + seed % 8)
            weights = create_weights(c)
            decoder = IslandDecoder(weights, c)
            sequence = list(c.prompt_tokens)
            prefill = decoder.prefill(sequence)
            full = reference_logits(weights, sequence, c)
            with self.subTest(seed=seed, phase='prefill'):
                np.testing.assert_allclose(prefill, full, rtol=1e-10, atol=1e-10)
            logits = prefill[-1]
            for _ in range(c.decode_tokens):
                token = int(np.argmax(logits))
                self.assertEqual(token, int(np.argmax(full[-1])))
                sequence.append(token)
                logits = decoder.step(token)
                full = reference_logits(weights, sequence, c)
                with self.subTest(seed=seed, length=len(sequence)):
                    np.testing.assert_allclose(logits, full[-1], rtol=1e-10, atol=1e-10)
            report = run_inference_demo(c)
            self.assertTrue(report['numerical_validation']['all_logits_close'])
            self.assertTrue(report['numerical_validation']['greedy_tokens_match'])
            self.assertEqual(report['execution']['cache_length'], len(sequence))

    def test_vectorized_reference_is_causal_and_uses_no_decoder_path(self):
        c = small_config()
        weights = create_weights(c)
        tokens = [1, 2, 3, 4, 5]
        with mock.patch.object(IslandDecoder, 'step', side_effect=AssertionError('not a reference')):
            whole = reference_logits(weights, tokens, c)
            for length in range(1, len(tokens) + 1):
                np.testing.assert_allclose(reference_logits(weights, tokens[:length], c),
                                           whole[:length], rtol=1e-10, atol=1e-10)

    def test_top_k_ties_select_ascending_experts_and_normalized_weights(self):
        c = small_config(top_k=5)
        weights = create_weights(c)
        weights.gate[:] = 0.0
        decoder = IslandDecoder(weights, c)
        logits = decoder.step(1)
        assignments = decoder.traces[0]['assignments']
        self.assertEqual([a['expert'] for a in assignments], [0, 1, 2, 3, 4])
        self.assertEqual([a['gate_weight'] for a in assignments], [0.2] * 5)
        self.assertAlmostEqual(sum(a['gate_weight'] for a in assignments), 1.0)
        np.testing.assert_allclose(logits, reference_logits(weights, [1], c)[0], rtol=1e-10, atol=1e-10)

    def test_kv_cache_contains_attention_projection_for_each_committed_token(self):
        c = small_config()
        decoder = IslandDecoder(config=c)
        tokens = [1, 2, 3]
        decoder.prefill(tokens)
        x = decoder.weights.embedding[tokens] + decoder.weights.position[:len(tokens)]
        x -= x.mean(axis=1, keepdims=True)
        normalized = x / np.sqrt(np.mean(x*x, axis=1, keepdims=True) + c.layer_norm_epsilon)
        normalized *= decoder.weights.ln1_scale
        shape = (len(tokens), c.attention_heads, c.model_dim // c.attention_heads)
        np.testing.assert_allclose(decoder.cache.keys[:3], (normalized @ decoder.weights.wk).reshape(shape))
        np.testing.assert_allclose(decoder.cache.values[:3], (normalized @ decoder.weights.wv).reshape(shape))
        self.assertTrue(np.all(decoder.cache.keys[3:] == 0))

    def test_prompt_generation_and_exact_capacity_boundaries(self):
        for prompt, decode in (((1,), 0), ((1,), 3), ((1, 2, 3, 4), 0), ((1, 2), 2)):
            c = small_config(prompt_tokens=prompt, decode_tokens=decode,
                             max_seq_len=len(prompt) + decode)
            with self.subTest(prompt=prompt, decode=decode):
                report = run_inference_demo(c)
                self.assertEqual(len(report['generated_tokens']), decode)
                self.assertEqual(report['execution']['cache_length'], len(prompt) + decode)
                self.assertTrue(report['numerical_validation']['all_logits_close'])
        c = small_config(prompt_tokens=(1,), decode_tokens=0, max_seq_len=1)
        decoder = IslandDecoder(config=c)
        decoder.step(1)
        before = decoder.report()
        with self.assertRaises(ValueError):
            decoder.step(2)
        self.assertEqual(decoder.report(), before)
        with self.assertRaises(ValueError):
            reference_logits(decoder.weights, [1, 2], c)
        empty = IslandDecoder(config=c)
        with self.assertRaises(ValueError):
            empty.prefill([1, 2])
        self.assertEqual(empty.cache.length, 0)

    def test_generation_and_unrepresentable_arithmetic_never_commit_cache(self):
        c = small_config()
        decoder = IslandDecoder(config=c)
        for generation in (0, c.generation + 1, True, float('nan')):
            with self.subTest(generation=generation), self.assertRaises(ValueError):
                decoder.step(1, generation=generation)
        self.assertEqual(decoder.cache.length, 0)
        weights = create_weights(c)
        weights.embedding[:] = np.finfo(np.float64).max
        weights.position[:] = np.finfo(np.float64).max
        decoder = IslandDecoder(weights, c)
        with self.assertRaises(ValueError):
            decoder.step(1)
        with self.assertRaises(ValueError):
            reference_logits(weights, [1], c)
        self.assertEqual(decoder.cache.length, 0)
        self.assertTrue(np.all(decoder.cache.keys == 0))
        self.assertEqual(decoder.report()['contributions_completed'], 0)


class InferenceMemoryAndScheduleTests(unittest.TestCase):
    def test_named_arrays_exact_bytes_ownership_and_per_island_capacity(self):
        c = small_config(num_experts=7)
        decoder = IslandDecoder(config=c)
        memory = decoder.memory
        inventory = memory['array_inventory']
        self.assertEqual(len({(a['owner_island'], a['name']) for a in inventory}), len(inventory))
        for array in inventory:
            self.assertEqual(array['bytes'], math.prod(array['shape']) * np.dtype(array['dtype']).itemsize)
            self.assertIs(type(array['bytes']), int)
            self.assertEqual(array['itemsize_bytes'], np.dtype(array['dtype']).itemsize)
        self.assertEqual(memory['total_budgeted_tensor_bytes'], sum(a['bytes'] for a in inventory))
        weight_total = sum(getattr(decoder.weights, f.name).nbytes for f in fields(decoder.weights))
        self.assertEqual(sum(i['resident_weights_bytes'] for i in memory['islands']), weight_total)
        for island in memory['islands']:
            owner = island['island']
            self.assertEqual(island['total_budgeted_tensor_bytes'],
                             sum(a['bytes'] for a in inventory if a['owner_island'] == owner))
            self.assertEqual(island['reserved_workspace_bytes'],
                             sum(a.nbytes for a in decoder.workspace[owner].values()))
            self.assertEqual(island['owned_experts'], list(decoder.expert_arrays[owner]))
            for expert, (w1, w2) in decoder.expert_arrays[owner].items():
                self.assertEqual(owner, expert % 4)
                self.assertTrue(np.shares_memory(w1, decoder.weights.expert_w1))
                self.assertTrue(np.shares_memory(w2, decoder.weights.expert_w2))
        required = tuple(i['total_budgeted_tensor_bytes'] for i in memory['islands'])
        admitted = IslandDecoder(config=replace(c, island_capacity_bytes=required))
        self.assertTrue(all(i['available_tensor_bytes'] == 0 for i in admitted.memory['islands']))
        admitted.step(1)
        for owner in range(4):
            undersized = list(required)
            undersized[owner] -= 1
            with self.subTest(owner=owner), self.assertRaisesRegex(ValueError, f'island {owner}'):
                IslandDecoder(config=replace(c, island_capacity_bytes=undersized))

    def test_dispatch_compute_combine_dependencies_and_every_packet_completion(self):
        c = small_config(top_k=8, buffer_flits_per_vc=1, flit_bytes=19)
        decoder = IslandDecoder(config=c)
        decoder.prefill(c.prompt_tokens)
        previous_end = 0
        identities = set()
        for trace in decoder.traces:
            self.assertEqual(trace['start_cycle'], previous_end)
            self.assertEqual(trace['dispatch_barrier_cycle'],
                             trace['dispatch_start_cycle'] + trace['network']['dispatch']['elapsed_cycles'])
            island_ends = [trace['dispatch_barrier_cycle']] * 4
            self.assertEqual(trace['contributions_completed'], c.top_k)
            for assignment in trace['assignments']:
                owner = assignment['island']
                identity = tuple(assignment['contribution_id'])
                self.assertNotIn(identity, identities)
                identities.add(identity)
                self.assertEqual(identity, (c.generation, trace['token_position'], assignment['expert']))
                self.assertGreaterEqual(assignment['compute_start_cycle'], assignment['dispatch_completion_cycle'])
                self.assertGreaterEqual(assignment['compute_start_cycle'], island_ends[owner])
                self.assertGreater(assignment['compute_end_cycle'], assignment['compute_start_cycle'])
                self.assertGreaterEqual(assignment['combine_completion_cycle'], assignment['compute_end_cycle'])
                self.assertTrue(assignment['completed_exactly_once'])
                island_ends[owner] = assignment['compute_end_cycle']
            for phase in ('dispatch', 'combine'):
                network = trace['network'][phase]
                self.assertTrue(network['idle'])
                self.assertTrue(network['all_requested_delivered'])
                self.assertEqual(len(network['deliveries']), 6)
                self.assertEqual(network['accounting']['delivered_packets'], 6)
                self.assertTrue(all(q['current'] == 0 for q in network['occupancies']))
                self.assertTrue(all(cr['available'] == cr['capacity'] for cr in network['credits']))
            self.assertGreaterEqual(trace['combine_barrier_cycle'], max(island_ends))
            self.assertGreater(trace['end_cycle'], trace['combine_barrier_cycle'])
            previous_end = trace['end_cycle']
        self.assertEqual(decoder.report()['contributions_completed'], len(identities))

    def test_byte_ledger_counts_payload_padding_hops_and_local_memory(self):
        c = small_config(top_k=8, flit_bytes=19)
        decoder = IslandDecoder(config=c)
        decoder.prefill([1, 2, 3])
        payload = c.model_dim * np.dtype(np.float64).itemsize
        padded = math.ceil(payload / c.flit_bytes) * c.flit_bytes
        for position, trace in enumerate(decoder.traces):
            ledger = trace['bytes']
            self.assertEqual(ledger['dispatch_remote_payload'], 6 * payload)
            self.assertEqual(ledger['combine_remote_payload'], 6 * payload)
            self.assertEqual(ledger['dispatch_local_bypass'], 2 * payload)
            self.assertEqual(ledger['combine_local_bypass'], 2 * payload)
            self.assertEqual(ledger['dispatch_remote_flit_bytes'], 6 * padded)
            self.assertEqual(ledger['dispatch_remote_padding'], 6 * (padded-payload))
            self.assertEqual(ledger['dispatch_link_flit_bytes'], 8 * padded)
            self.assertEqual(ledger['combine_link_flit_bytes'], 8 * padded)
            self.assertEqual(ledger['kv_local_read'], position * 2 * payload)
            self.assertEqual(ledger['kv_local_write'], 2 * payload)
            for island in ledger['by_island']:
                owner = island['island']
                self.assertEqual(island['expert_local_weight_reads'],
                                 sum(a.nbytes for pair in decoder.expert_arrays[owner].values() for a in pair))
                self.assertEqual(island['dispatch_payload_sent'], 6 * payload if owner == 0 else 0)
                self.assertEqual(island['dispatch_payload_received'], 0 if owner == 0 else 2 * payload)
                self.assertEqual(island['combine_payload_sent'], 0 if owner == 0 else 2 * payload)
                self.assertEqual(island['combine_payload_received'], 6 * payload if owner == 0 else 0)
                self.assertTrue(all(type(value) is int for value in island.values()))
        totals = decoder.report()['bytes']
        self.assertEqual(totals['dispatch_remote_payload'], 18 * payload)
        self.assertEqual(totals['kv_local_read'], 6 * payload)
        for owner in range(4):
            self.assertEqual(totals['by_island'][owner]['expert_local_weight_reads'],
                             totals['expert_local_weight_reads_by_island'][owner])

    def test_local_expert_bypasses_network_but_computes_real_output(self):
        c = small_config(num_experts=1, top_k=1)
        decoder = IslandDecoder(config=c)
        logits = decoder.step(1)
        trace = decoder.traces[0]
        self.assertEqual(trace['bytes']['dispatch_remote_payload'], 0)
        self.assertEqual(trace['bytes']['dispatch_local_bypass'], c.model_dim * 8)
        for network in trace['network'].values():
            self.assertEqual(network['deliveries'], [])
            self.assertEqual(network['elapsed_cycles'], 0)
            self.assertTrue(network['idle'])
        np.testing.assert_allclose(logits, reference_logits(decoder.weights, [1], c)[0], rtol=1e-10, atol=1e-10)

    def test_missing_duplicate_rejected_or_corrupt_network_ledger_prevents_cache_commit(self):
        def duplicate(report):
            report['deliveries'][1] = dict(report['deliveries'][0])

        mutations = (
            lambda r: r['deliveries'].pop(), duplicate,
            lambda r: r['pending_packets'].append({'packet_id': 100}),
            lambda r: r['rejected_packets'].append({'packet_id': 100}),
            lambda r: r.update(idle=False),
            lambda r: r.update(all_requested_delivered=False),
            lambda r: r['deliveries'][0].update(source=13),
            lambda r: r['deliveries'][0].update(completion_cycle=-1),
            lambda r: r['accounting'].update(delivered_flits=0),
            lambda r: r['credits'][0].update(available=0),
            lambda r: r['credit_returns'].append({'return_cycle': 999}),
        )
        c = small_config(num_experts=4, top_k=4)
        for mutation in mutations:
            def broken(*args, **kwargs):
                result = simulate_network(*args, **kwargs)
                mutation(result)
                return result

            decoder = IslandDecoder(config=c)
            with self.subTest(mutation=mutation), mock.patch('sim.finite_buffer.simulate_network', side_effect=broken):
                with self.assertRaises(ValueError):
                    decoder.step(1)
            self.assertEqual(decoder.cache.length, 0)
            self.assertEqual(decoder.tokens, [])
            self.assertEqual(decoder.traces, [])
            self.assertTrue(np.all(decoder.cache.keys == 0))


class InferenceTransportTests(unittest.TestCase):
    def test_actual_expert_output_recovery_and_ambiguous_ack_loss(self):
        report = run_inference_fault_demo()
        self.assertEqual(report, run_inference_fault_demo())
        self.assertTrue(report['numerical_validation']['all_logits_close'])
        self.assertTrue(report['numerical_validation']['remote_contributions_executed_once'])
        self.assertTrue(report['numerical_validation']['weighted_contributions_combined_once'])
        recovered = report['recovery']
        self.assertEqual(recovered['completed'], 3)
        self.assertEqual(recovered['aborted'], 0)
        self.assertEqual(recovered['stats']['receiver_executions'], 3)
        self.assertGreaterEqual(recovered['stats']['duplicates_suppressed'], 3)
        self.assertEqual(recovered['stats']['fault_drops'], 2)
        self.assertEqual(recovered['stats']['fault_corruptions'], 1)
        self.assertEqual(recovered['stats']['crc_errors'], 1)
        received = [np.frombuffer(bytes.fromhex(r['delivered_payloads'][0]['payload_hex']), dtype=np.float64)
                    for r in recovered['requests']]
        self.assertTrue(all(vector.shape == (report['config']['model_dim'],) for vector in received))
        self.assertFalse(np.array_equal(received[0], received[1]))
        lost = report['all_acks_lost']
        self.assertTrue(lost['aborted_without_cache_commit'])
        self.assertFalse(lost['logits_accepted'])
        self.assertEqual(lost['combined_contributions'], 0)
        # Stop admission after the first uncertain output; no later expert is
        # submitted and every admitted request is terminal before returning.
        self.assertEqual(lost['transport']['submitted'], 1)
        self.assertEqual(lost['transport']['stats']['receiver_executions'], 1)
        self.assertEqual(lost['transport']['aborted'], 1)
        self.assertEqual(lost['transport']['reserved_bytes'], 0)
        self.assertTrue(all(r['uncertain_delivery'] for r in lost['transport']['requests']))
        json.dumps(report, allow_nan=False)

    def test_transport_ticks_are_separate_and_stale_transport_rejected(self):
        report = run_inference_fault_demo()
        c = InferenceConfig.from_dict(report['config'])
        weights = create_weights(c)
        weights.gate[:] = 0.0
        healthy = IslandDecoder(weights, c)
        healthy.step(c.prompt_tokens[0])
        self.assertEqual(healthy.cycle, report['execution']['modeled_cycles'])
        with self.assertRaises(ValueError):
            IslandDecoder(weights, c, contribution_transport=object())
        with self.assertRaises(ValueError):
            IslandDecoder(weights, c, contribution_transport=ReliableTransport(generation=c.generation + 1))
        transport = ReliableTransport(generation=c.generation)
        decoder = IslandDecoder(weights, c, contribution_transport=transport)
        transport.advance_generation(c.generation + 1)
        with self.assertRaises(ValueError):
            decoder.step(1)
        self.assertEqual(decoder.cache.length, 0)


class InferenceCliTests(unittest.TestCase):
    def test_report_determinism_and_strict_json(self):
        report = run_inference_demo()
        self.assertEqual(report, run_inference_demo())
        self.assertEqual(report, json.loads(json.dumps(report, allow_nan=False)))

    def test_import_has_no_output_or_file_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            source = f"import sys; sys.path.insert(0, {str(ROOT)!r}); import sim.moe_inference"
            result = subprocess.run([sys.executable, '-c', source], cwd=directory,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, '')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_module_and_direct_script_paths_produce_identical_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config = directory / 'config.json'
            config.write_text(json.dumps(asdict(small_config(seed=101))), encoding='utf-8')
            output = directory / 'nested' / 'result.json'
            module = subprocess.run([sys.executable, '-m', 'sim.moe_inference', '--config', str(config),
                                     '--output', str(output)], cwd=ROOT, capture_output=True, text=True, check=True)
            direct = subprocess.run([sys.executable, str(ROOT / 'sim' / 'moe_inference.py'),
                                     '--config', str(config)], cwd=directory, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(module.stdout), json.loads(direct.stdout))
            self.assertEqual(module.stdout, output.read_text(encoding='utf-8'))
            self.assertEqual(module.stderr + direct.stderr, '')

    def test_cli_rejects_nonstandard_json_duplicate_keys_and_invalid_config_without_output(self):
        cases = ('{"seed": NaN}', '{"seed": Infinity}', '{"seed": 1, "seed": 2}',
                 '{"seed": true}', '{"layer_norm_epsilon": 1e309}', '{"unknown": 1}', '[]', '{')
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config, output = directory / 'bad.json', directory / 'absent.json'
            for source in cases:
                config.write_text(source, encoding='utf-8')
                result = subprocess.run([sys.executable, '-m', 'sim.moe_inference', '--config', str(config),
                                         '--output', str(output)], cwd=ROOT, capture_output=True, text=True)
                with self.subTest(source=source):
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('error:', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
