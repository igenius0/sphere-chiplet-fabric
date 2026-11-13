"""Payload, identity and bounded-failure checks for the logical protocol oracle."""
from dataclasses import replace
import itertools
import json
import random
import struct
import unittest

from sim.transport import (MAX_ID, MAX_TICK, Fault, Frame, ReliableTransport,
                           TransportConfig, demonstrate_transport,
                           run_transport_demo)


class TransportCase(unittest.TestCase):
    def assert_accounting(self, transport, terminal=False):
        report = transport.report()
        self.assertEqual(report['submitted'], report['completed'] + report['aborted'] + report['pending'])
        self.assertEqual(report['reserved_bytes'], sum(
            result['reservation_bytes'] for result in report['requests'] if result['status'] == 'pending'))
        self.assertEqual(report['completed'], report['stats']['completed_requests'])
        self.assertEqual(report['aborted'], report['stats']['aborted_requests'])
        self.assertEqual(report['stats']['receiver_executions'], sum(
            result['receiver_execute_count'] for result in report['requests']))
        self.assertLessEqual(report['queued_events'], transport.config.max_queued_events)
        for result in report['requests']:
            expected, acked = set(result['expected_sequences']), set(result['acked_sequences'])
            delivered = {item['sequence'] for item in result['delivered_payloads']}
            self.assertEqual(set(result['unacked_sequences']), expected - acked)
            self.assertTrue(acked <= delivered <= expected)
            self.assertEqual(result['receiver_execute_count'], len(delivered))
            self.assertTrue(all(1 <= count <= transport.config.max_retries + 1
                                for count in result['attempts'].values()))
            self.assertEqual(result['reservation_released'], result['status'] != 'pending')
            if result['status'] == 'completed':
                self.assertEqual(acked, expected)
                self.assertEqual(delivered, expected)
                self.assertFalse(result['uncertain_delivery'])
                self.assertEqual(result['sender_outcome'], 'acknowledged')
            if result['status'] != 'pending':
                self.assertLessEqual(result['terminal_tick'], result['deadline_tick'])
        if terminal:
            self.assertEqual(report['pending'], 0)
            self.assertEqual(report['reserved_bytes'], 0)
            self.assertEqual(report['queued_events'], 0)

    def assert_rejected_without_mutation(self, transport, operation):
        before = transport.report()
        with self.assertRaises(ValueError):
            operation()
        self.assertEqual(transport.report(), before)


class ValidationTests(TransportCase):
    def test_config_rejects_invalid_bounds_and_unknown_fields(self):
        invalid = ({'timeout_ticks': 0}, {'timeout_ticks': True}, {'max_retries': -1},
                   {'max_retries': 17}, {'max_inflight': 2, 'max_requests': 1},
                   {'max_requests': 0}, {'max_contributions': 0}, {'max_payload_bytes': 0},
                   {'max_reserved_bytes': -1}, {'link_delay_ticks': 0},
                   {'max_queued_events': 0}, {'max_wire_copies': 65})
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                TransportConfig(**values)
        for values in (None, [], {'timout_ticks': 2}, {1: 2}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                TransportConfig.from_dict(values)
        self.assertEqual(TransportConfig.from_dict({'max_retries': 0}).max_retries, 0)

    def test_frame_crc_has_fixed_vector_and_covers_identity_and_payload(self):
        frame = Frame.create('r', 3, 7, 2, 9, b'abc')
        # CRC32 of b'["DATA","r",3,7,2,9]\x00abc', fixed independently.
        self.assertEqual(frame.crc32, 0x5CE4D688)
        variants = ({'request_id': 's'}, {'generation': 4}, {'sequence': 8},
                    {'source': 3}, {'destination': 10}, {'payload': b'abd'},
                    {'kind': 'ACK', 'payload': b''})
        for change in variants:
            with self.subTest(change=change):
                self.assertNotEqual(replace(frame, **change).expected_crc32(), frame.crc32)
        self.assertEqual(Frame.create('r', 3, 7, 9, 2, kind='ACK').payload, b'')

    def test_frame_constructor_rejects_invalid_fields(self):
        valid = dict(request_id='r', generation=0, sequence=0, source=0, destination=1)
        invalid = ({'request_id': ''}, {'request_id': '\x00'}, {'request_id': '\ud800'},
                   {'request_id': 'é' * 65}, {'generation': True}, {'sequence': -1},
                   {'source': MAX_ID + 1}, {'destination': 1.0}, {'crc32': -1},
                   {'payload': bytearray(b'a')}, {'kind': 'NACK'},
                   {'kind': 'ACK', 'payload': b'a'})
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                Frame(**(valid | change))

    def test_fault_rules_validate_actions_selectors_and_copy_bounds(self):
        invalid = ({'action': 'flip'}, {'action': 'delay'},
                   {'action': 'reorder', 'delay_ticks': 0},
                   {'action': 'drop', 'delay_ticks': 1}, {'action': 'drop', 'copies': 2},
                   {'action': 'duplicate', 'copies': 0}, {'action': 'drop', 'attempt': True},
                   {'action': 'drop', 'kind': 'bad'}, {'action': 'drop', 'request_id': ''})
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Fault(**values)
        for faults in ((object(),), (Fault('drop'),) * 33,
                       (Fault('duplicate', copies=2), Fault('duplicate', copies=5))):
            with self.subTest(faults=faults), self.assertRaises(ValueError):
                ReliableTransport(faults=faults)
        with self.assertRaises(ValueError):
            ReliableTransport(TransportConfig(max_wire_copies=2), faults=(Fault('duplicate', copies=2),))

    def test_all_malformed_admissions_are_atomic(self):
        transport = ReliableTransport(TransportConfig(max_contributions=2, max_payload_bytes=3))
        transport.submit('existing', 1, 2, [(0, b'ok')], reservation_bytes=4)
        invalid = (None, [], [(0, bytearray(b'a'))], [(0, b'abcd')], [(-1, b'a')],
                   [(True, b'a')], [(0, b'a'), (0, b'b')], [(0, b'a'), (1, b'b'), (2, b'c')],
                   [b'ab'], [(0,)], [(0, b'a'), (1, 'text')])
        for contributions in invalid:
            with self.subTest(contributions=contributions):
                self.assert_rejected_without_mutation(transport, lambda: transport.submit(
                    'new', 0, 1, contributions, reservation_bytes=8))
        for changes in ({'request_id': ''}, {'request_id': 'x' * 129}, {'source': True},
                        {'destination': -1}, {'reservation_bytes': -1}, {'generation': 1}):
            values = dict(request_id='new', source=0, destination=1,
                          contributions=[(0, b'a')], reservation_bytes=8)
            values.update(changes)
            with self.subTest(changes=changes):
                self.assert_rejected_without_mutation(transport, lambda: transport.submit(**values))

    def test_admission_bounds_an_infinite_contribution_iterator(self):
        transport = ReliableTransport(TransportConfig(max_contributions=2))
        observed = []
        def contributions():
            for sequence in itertools.count():
                observed.append(sequence)
                yield sequence, b'a'
        self.assert_rejected_without_mutation(transport, lambda: transport.submit('r', 0, 1, contributions()))
        self.assertEqual(observed, [0, 1, 2])

    def test_injection_revalidates_reconstructed_frame_before_mutation(self):
        transport = ReliableTransport(TransportConfig(max_payload_bytes=2))
        malformed = Frame.create('r', 0, 0, 0, 1)
        object.__setattr__(malformed, 'sequence', -1)
        for frame in (None, malformed, Frame.create('r', 0, 0, 0, 1, b'abc')):
            with self.subTest(frame=frame):
                self.assert_rejected_without_mutation(transport, lambda: transport.inject_frame(frame))
        frame = Frame.create('r', 0, 0, 0, 1)
        for values in ({'delay_ticks': -1}, {'delay_ticks': True}, {'attempt': 17}, {'attempt': True}):
            self.assert_rejected_without_mutation(transport, lambda: transport.inject_frame(frame, **values))

    def test_invalid_control_and_query_arguments_do_not_mutate(self):
        transport = ReliableTransport()
        transport.submit('r', 0, 1, [(0, b'a')], reservation_bytes=3)
        for operation in (lambda: transport.run(-1), lambda: transport.run(True),
                          lambda: transport.advance_generation(0), lambda: transport.advance_generation(True),
                          lambda: transport.request_result('unknown'), lambda: transport.delivered_payloads('')):
            self.assert_rejected_without_mutation(transport, operation)


class ProtocolTests(TransportCase):
    def test_hand_derived_healthy_timeline_and_exact_bytes(self):
        transport = ReliableTransport()
        payloads = [(7, b'ab'), (2, b'\x00\xff')]
        transport.submit('r', 3, 8, payloads, reservation_bytes=12)
        at_one = transport.run(1)
        self.assertEqual(transport.delivered_payloads('r'), ((2, b'\x00\xff'), (7, b'ab')))
        self.assertEqual(at_one['pending'], 1)
        self.assertEqual(at_one['reserved_bytes'], 12)
        self.assertEqual(transport.request_result('r')['acked_sequences'], [])
        at_two = transport.run(1)
        result = transport.request_result('r')
        self.assertEqual(result['terminal_tick'], 2)  # DATA at 1, ACK at 2.
        self.assertEqual(result['attempts'], {'2': 1, '7': 1})
        self.assertEqual(at_two['completed'], 1)
        self.assertEqual(at_two['reserved_bytes'], 0)
        report = transport.run()
        self.assertEqual(report['tick'], 4)  # Drains obsolete original timeouts.
        self.assertEqual(report['stats']['data_transmissions'], 2)
        self.assertEqual(report['stats']['ack_transmissions'], 2)
        self.assertEqual(report['stats']['wire_copies'], 4)
        self.assertEqual(report['stats']['stale_timeouts'], 2)
        self.assert_accounting(transport, terminal=True)

    def test_first_data_drop_or_crc_error_recovers_on_first_retry(self):
        for action in ('drop', 'corrupt'):
            with self.subTest(action=action):
                transport = ReliableTransport(faults=(Fault(action, 'DATA', attempt=0),))
                transport.submit('r', 0, 1, [(9, b'actual payload')], reservation_bytes=13)
                transport.run(4)
                self.assertEqual(transport.request_result('r')['attempts'], {'9': 2})
                self.assertEqual(transport.delivered_payloads('r'), ())
                report = transport.run()
                self.assertEqual(transport.request_result('r')['terminal_tick'], 6)
                self.assertEqual(transport.delivered_payloads('r'), ((9, b'actual payload'),))
                self.assertEqual(report['stats']['receiver_executions'], 1)
                self.assertEqual(report['stats']['crc_errors'], int(action == 'corrupt'))
                self.assert_accounting(transport, terminal=True)

    def test_lost_or_corrupt_ack_repeats_data_without_execution(self):
        for action in ('drop', 'corrupt'):
            with self.subTest(action=action):
                transport = ReliableTransport(faults=(Fault(action, 'ACK', attempt=0),))
                transport.submit('r', 0, 1, [(0, b'once')])
                report = transport.run()
                self.assertEqual(transport.request_result('r')['terminal_tick'], 6)
                self.assertEqual(report['stats']['data_transmissions'], 2)
                self.assertEqual(report['stats']['ack_transmissions'], 2)
                self.assertEqual(report['stats']['duplicates_suppressed'], 1)
                self.assertEqual(transport.delivered_payloads('r'), ((0, b'once'),))
                self.assert_accounting(transport, terminal=True)

    def test_ack_arrival_wins_exact_tie_with_final_timeout(self):
        transport = ReliableTransport(TransportConfig(timeout_ticks=2, max_retries=0))
        transport.submit('r', 0, 1, [(0, b'tie')])
        transport.run()
        self.assertEqual(transport.request_result('r')['status'], 'completed')
        self.assertEqual(transport.request_result('r')['terminal_tick'], 2)
        self.assert_accounting(transport, terminal=True)

    def test_timeout_shorter_than_roundtrip_is_explicitly_uncertain(self):
        transport = ReliableTransport(TransportConfig(timeout_ticks=1, max_retries=0))
        transport.submit('r', 0, 1, [(0, b'executed')], reservation_bytes=5)
        report = transport.run()
        result = transport.request_result('r')
        self.assertEqual(result['status'], 'aborted')
        self.assertEqual(result['terminal_tick'], 1)
        self.assertEqual(result['receiver_execute_count'], 1)
        self.assertTrue(result['uncertain_delivery'])
        self.assertEqual(result['sender_outcome'], 'uncertain')
        self.assertEqual(report['stats']['late_frames'], 1)
        self.assert_accounting(transport, terminal=True)

    def test_partition_and_all_ack_loss_have_bounded_terminal_accounting(self):
        for kind, executions in (('DATA', 0), ('ACK', 2)):
            with self.subTest(kind=kind):
                transport = ReliableTransport(faults=(Fault('partition', kind),))
                transport.submit('r', 0, 1, [(0, b'a'), (1, b'b')], reservation_bytes=17)
                report = transport.run()
                result = transport.request_result('r')
                self.assertEqual(result['status'], 'aborted')
                self.assertEqual(result['reason'], 'retry_exhausted')
                self.assertEqual(result['terminal_tick'], 12)
                self.assertEqual(result['attempts'], {'0': 3, '1': 3})
                self.assertEqual(result['receiver_execute_count'], executions)
                self.assertEqual(report['stats']['data_transmissions'], 6)
                self.assertEqual(report['stats']['duplicates_suppressed'], 4 if kind == 'ACK' else 0)
                self.assertTrue(result['uncertain_delivery'])
                self.assert_accounting(transport, terminal=True)

    def test_duplicate_and_reordered_data_and_ack_preserve_each_payload(self):
        faults = (Fault('duplicate', 'DATA', copies=2), Fault('duplicate', 'ACK', copies=1),
                  Fault('reorder', 'DATA', sequence=0, delay_ticks=2),
                  Fault('reorder', 'ACK', sequence=1, delay_ticks=2))
        transport = ReliableTransport(faults=faults)
        transport.submit('r', 0, 1, [(0, b'first'), (1, b'second')])
        transport.run(1)
        self.assertEqual(transport.delivered_payloads('r'), ((1, b'second'),))
        report = transport.run()
        self.assertEqual(transport.delivered_payloads('r'), ((0, b'first'), (1, b'second')))
        self.assertEqual(report['stats']['receiver_executions'], 2)
        self.assertEqual(report['stats']['duplicates_suppressed'], 4)
        self.assertEqual(report['stats']['data_transmissions'], 2)
        self.assertEqual(transport.request_result('r')['terminal_tick'], 4)
        self.assert_accounting(transport, terminal=True)

    def test_foreign_stale_and_premature_ack_do_not_complete_or_abort_request(self):
        transport = ReliableTransport(generation=3, faults=(Fault('delay', 'DATA', delay_ticks=1),))
        transport.submit('r', 4, 9, [(5, b'payload')], reservation_bytes=7)
        frames = (Frame.create('unknown', 3, 5, 9, 4, kind='ACK'),
                  Frame.create('r', 2, 5, 9, 4, kind='ACK'),
                  Frame.create('r', 4, 5, 9, 4, kind='ACK'),
                  Frame.create('r', 3, 6, 9, 4, kind='ACK'),
                  Frame.create('r', 3, 5, 8, 4, kind='ACK'),
                  Frame.create('r', 3, 5, 9, 8, kind='ACK'),
                  Frame.create('r', 3, 5, 9, 4, kind='ACK'))
        for frame in frames:
            transport.inject_frame(frame)
        at_zero = transport.run(0)
        self.assertEqual(transport.request_result('r')['status'], 'pending')
        self.assertEqual(transport.request_result('r')['acked_sequences'], [])
        self.assertEqual(at_zero['stats']['unknown_frames'], 1)
        self.assertEqual(at_zero['stats']['stale_frames'], 2)
        self.assertEqual(at_zero['stats']['protocol_errors'], 4)
        transport.run()
        self.assertEqual(transport.request_result('r')['status'], 'completed')
        self.assert_accounting(transport, terminal=True)

    def test_crc_is_checked_before_identity_and_conflicting_payload_is_not_accepted(self):
        transport = ReliableTransport(faults=(Fault('delay', 'DATA', delay_ticks=1),))
        transport.submit('r', 0, 1, [(0, b'correct')])
        valid = Frame.create('r', 0, 0, 0, 1, b'correct')
        transport.inject_frame(replace(valid, request_id='other'))
        transport.inject_frame(Frame.create('r', 0, 0, 0, 1, b'conflict'))
        report = transport.run(0)
        self.assertEqual(report['stats']['crc_errors'], 1)
        self.assertEqual(report['stats']['unknown_frames'], 0)
        self.assertEqual(report['stats']['protocol_errors'], 1)
        self.assertEqual(transport.delivered_payloads('r'), ())
        self.assertEqual(transport.request_result('r')['status'], 'pending')
        transport.run()
        self.assertEqual(transport.delivered_payloads('r'), ((0, b'correct'),))
        self.assert_accounting(transport, terminal=True)

    def test_late_data_after_timeout_abort_cannot_execute(self):
        transport = ReliableTransport(TransportConfig(max_retries=0),
                                      faults=(Fault('delay', 'DATA', delay_ticks=20),))
        transport.submit('r', 0, 1, [(0, b'late')], reservation_bytes=8)
        report = transport.run()
        result = transport.request_result('r')
        self.assertEqual(result['terminal_tick'], 4)
        self.assertEqual(report['tick'], 21)
        self.assertEqual(result['receiver_execute_count'], 0)
        self.assertEqual(report['stats']['late_frames'], 1)
        transport.inject_frame(Frame.create('r', 0, 0, 1, 0, kind='ACK'))
        transport.run()
        self.assertEqual(transport.request_result('r'), result)
        self.assert_accounting(transport, terminal=True)

    def test_completed_record_suppresses_duplicates_without_double_terminal_count(self):
        transport = ReliableTransport()
        transport.submit('r', 0, 1, [(0, b'once')], reservation_bytes=11)
        transport.run()
        result = transport.request_result('r')
        transport.inject_frame(Frame.create('r', 0, 0, 0, 1, b'once'))
        transport.inject_frame(Frame.create('r', 0, 0, 1, 0, kind='ACK'))
        report = transport.run()
        self.assertEqual(transport.request_result('r'), result)
        self.assertEqual(report['stats']['receiver_executions'], 1)
        self.assertEqual(report['stats']['completed_requests'], 1)
        self.assertEqual(report['stats']['duplicates_suppressed'], 1)
        self.assert_accounting(transport, terminal=True)


class CapacityAndGenerationTests(TransportCase):
    def test_request_reuse_is_rejected_and_terminal_replay_records_never_evict(self):
        transport = ReliableTransport(TransportConfig(max_requests=2, max_inflight=1))
        transport.submit('r', 0, 1, [(0, b'old')])
        self.assert_rejected_without_mutation(transport, lambda: transport.submit('r', 0, 1, [(0, b'new')]))
        transport.run()
        self.assert_rejected_without_mutation(transport, lambda: transport.submit('r', 0, 1, [(0, b'new')]))
        transport.advance_generation(1)
        transport.submit('r', 0, 1, [(0, b'new')])
        transport.run()
        transport.advance_generation(2)
        self.assert_rejected_without_mutation(transport, lambda: transport.submit('next', 0, 1, [(0, b'x')]))
        self.assertEqual(transport.delivered_payloads('r', 0), ((0, b'old'),))
        self.assertEqual(transport.delivered_payloads('r', 1), ((0, b'new'),))
        self.assert_accounting(transport, terminal=True)

    def test_inflight_and_reservations_block_atomically_then_release(self):
        for config in (TransportConfig(max_inflight=1), TransportConfig(max_reserved_bytes=5)):
            with self.subTest(config=config):
                transport = ReliableTransport(config)
                transport.submit('a', 0, 1, [(0, b'a')], reservation_bytes=5)
                self.assert_rejected_without_mutation(transport, lambda: transport.submit(
                    'b', 0, 1, [(0, b'b')], reservation_bytes=1))
                transport.run()
                transport.submit('b', 0, 1, [(0, b'b')], reservation_bytes=5)
                transport.run()
                self.assert_accounting(transport, terminal=True)

    def test_event_admission_and_injection_reject_without_reserving(self):
        transport = ReliableTransport(TransportConfig(max_queued_events=1))
        self.assert_rejected_without_mutation(transport, lambda: transport.submit(
            'r', 0, 1, [(0, b'a')], reservation_bytes=10))
        transport.inject_frame(Frame.create('unknown', 0, 0, 0, 1))
        self.assert_rejected_without_mutation(transport, lambda: transport.inject_frame(
            Frame.create('unknown', 0, 0, 0, 1)))
        transport.run()
        self.assert_accounting(transport, terminal=True)

    def test_ack_event_capacity_exhaustion_aborts_after_execution_and_releases(self):
        transport = ReliableTransport(TransportConfig(max_queued_events=2),
                                      faults=(Fault('duplicate', 'ACK'),))
        transport.submit('r', 0, 1, [(0, b'once')], reservation_bytes=9)
        transport.run()
        result = transport.request_result('r')
        self.assertEqual(result['status'], 'aborted')
        self.assertEqual(result['reason'], 'event_capacity_exhausted')
        self.assertEqual(result['receiver_execute_count'], 1)
        self.assertTrue(result['uncertain_delivery'])
        self.assert_accounting(transport, terminal=True)

    def test_retry_event_capacity_exhaustion_is_terminal_without_false_execution(self):
        transport = ReliableTransport(TransportConfig(max_queued_events=2), faults=(
            Fault('drop', 'DATA', attempt=0), Fault('duplicate', 'DATA', attempt=1, copies=2)))
        transport.submit('r', 0, 1, [(0, b'never')], reservation_bytes=9)
        transport.run()
        result = transport.request_result('r')
        self.assertEqual(result['reason'], 'event_capacity_exhausted')
        self.assertEqual(result['receiver_execute_count'], 0)
        self.assertEqual(result['attempts'], {'0': 1})
        self.assert_accounting(transport, terminal=True)

    def test_generation_barrier_releases_reservation_and_isolates_late_data_or_ack(self):
        for kind, old_executions in (('DATA', 0), ('ACK', 1)):
            with self.subTest(kind=kind):
                transport = ReliableTransport(TransportConfig(max_reserved_bytes=20),
                    faults=(Fault('delay', kind, generation=0, delay_ticks=6),))
                transport.submit('r', 0, 1, [(0, b'old')], reservation_bytes=20)
                transport.run(1)
                transport.advance_generation(1)
                self.assertEqual(transport.reserved_bytes, 0)
                old = transport.request_result('r', 0)
                self.assertEqual(old['reason'], 'generation_changed')
                self.assertEqual(old['receiver_execute_count'], old_executions)
                transport.submit('r', 0, 1, [(0, b'new')], reservation_bytes=20)
                report = transport.run()
                self.assertEqual(transport.request_result('r', 0), old)
                self.assertEqual(transport.delivered_payloads('r', 1), ((0, b'new'),))
                self.assertEqual(report['completed'], 1)
                self.assertEqual(report['aborted'], 1)
                self.assertEqual(report['stats']['stale_frames'], 1)
                self.assert_accounting(transport, terminal=True)

    def test_all_pending_requests_abort_once_at_barrier(self):
        transport = ReliableTransport(faults=(Fault('partition', 'DATA'),))
        for sequence in range(4):
            transport.submit(str(sequence), 0, 1, [(sequence, b'x')], reservation_bytes=sequence + 1)
        transport.advance_generation(1)
        transport.advance_generation(2)
        report = transport.run()
        self.assertEqual(report['aborted'], 4)
        self.assertTrue(all(item['reason'] == 'generation_changed' for item in report['requests']))
        self.assert_accounting(transport, terminal=True)

    def test_tick_overflow_during_admission_is_atomic(self):
        transport = ReliableTransport()
        transport.run(MAX_TICK - 2)
        self.assert_rejected_without_mutation(transport, lambda: transport.submit(
            'r', 0, 1, [(0, b'a')], reservation_bytes=5))
        self.assert_rejected_without_mutation(transport, lambda: transport.inject_frame(
            Frame.create('r', 0, 0, 0, 1), delay_ticks=3))
        self.assert_rejected_without_mutation(transport, lambda: transport.run(3))

    def test_ack_tick_overflow_after_execution_is_explicit_terminal_uncertainty(self):
        transport = ReliableTransport(TransportConfig(timeout_ticks=1, max_retries=0))
        transport.run(MAX_TICK - 1)
        transport.submit('r', 0, 1, [(0, b'once')], reservation_bytes=5)
        transport.run()
        result = transport.request_result('r')
        self.assertEqual(result['reason'], 'event_tick_exhausted')
        self.assertEqual(result['receiver_execute_count'], 1)
        self.assertTrue(result['uncertain_delivery'])
        self.assert_accounting(transport, terminal=True)

    def test_delayed_retry_tick_overflow_aborts_and_drains_old_data(self):
        transport = ReliableTransport(TransportConfig(timeout_ticks=1, max_retries=1),
                                      faults=(Fault('delay', 'DATA', delay_ticks=2),))
        transport.run(MAX_TICK - 3)
        transport.submit('r', 0, 1, [(0, b'never')], reservation_bytes=5)
        transport.run()
        result = transport.request_result('r')
        self.assertEqual(result['reason'], 'event_tick_exhausted')
        self.assertEqual(result['receiver_execute_count'], 0)
        self.assertEqual(transport.report()['tick'], MAX_TICK)
        self.assert_accounting(transport, terminal=True)


class HeldOutPayloadTests(TransportCase):
    def test_fixed_faults_on_held_out_payload_seeds_100_through_109(self):
        # Rules are fixed before the held-out payloads are generated. Exact
        # immutable bytes, not just a checksum or floating sum, are compared.
        faults = (Fault('drop', 'DATA', sequence=0, attempt=0),
                  Fault('corrupt', 'DATA', sequence=1, attempt=0),
                  Fault('duplicate', 'DATA', sequence=2, attempt=0, copies=2),
                  Fault('delay', 'DATA', sequence=3, attempt=0, delay_ticks=5),
                  Fault('drop', 'ACK', sequence=4, attempt=0),
                  Fault('reorder', 'ACK', sequence=2, attempt=0, delay_ticks=3))
        for seed in range(100, 110):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                payloads = [(sequence, struct.pack('!8d', *(rng.uniform(-10, 10) for _ in range(8))))
                            for sequence in range(5)]
                transport = ReliableTransport(faults=faults)
                transport.submit(f'held-out-{seed}', 2, 7, payloads, reservation_bytes=320)
                report = transport.run()
                result = transport.request_result(f'held-out-{seed}')
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['terminal_tick'], 6)
                self.assertEqual(result['attempts'], {str(sequence): 2 for sequence in range(5)})
                self.assertEqual(transport.delivered_payloads(f'held-out-{seed}'), tuple(payloads))
                self.assertEqual(report['stats']['receiver_executions'], 5)
                self.assertEqual(report['stats']['crc_errors'], 1)
                # seq2: two initial copies plus one retry; seq3: delayed
                # original after retry; seq4: ACK-loss retry. Total five.
                self.assertEqual(report['stats']['duplicates_suppressed'], 5)
                self.assert_accounting(transport, terminal=True)

    def test_held_out_ack_loss_is_uncertain_and_never_double_combined(self):
        for seed in range(100, 110):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                values = tuple(rng.randrange(-1000, 1001) for _ in range(5))
                payloads = [(sequence, struct.pack('!q', value)) for sequence, value in enumerate(values)]
                transport = ReliableTransport(faults=(Fault('drop', 'ACK'),))
                transport.submit(f'ack-loss-{seed}', 0, 1, payloads, reservation_bytes=40)
                transport.run()
                result = transport.request_result(f'ack-loss-{seed}')
                combined = sum(struct.unpack('!q', payload)[0]
                               for _, payload in transport.delivered_payloads(f'ack-loss-{seed}'))
                self.assertEqual(combined, sum(values))
                self.assertEqual(transport.delivered_payloads(f'ack-loss-{seed}'), tuple(payloads))
                self.assertEqual(result['receiver_execute_count'], 5)
                self.assertEqual(result['status'], 'aborted')
                self.assertEqual(result['sender_outcome'], 'uncertain')
                self.assert_accounting(transport, terminal=True)

    def test_demo_is_deterministic_serializable_and_states_uncertain_sender_result(self):
        report = demonstrate_transport()
        self.assertEqual(report, run_transport_demo({}))
        self.assertEqual(json.loads(json.dumps(report, allow_nan=False)), report)
        for name in ('healthy', 'recover_drop_corrupt_duplicate_reorder'):
            scenario = report['scenarios'][name]
            self.assertEqual(scenario['completed'], 1)
            self.assertEqual(scenario['receiver_numeric_sum'], 7.75)
            self.assertEqual(scenario['sender_verified_numeric_result'], 7.75)
        for name in ('partition', 'all_acks_lost'):
            scenario = report['scenarios'][name]
            self.assertEqual(scenario['aborted'], 1)
            self.assertEqual(scenario['pending'], 0)
            self.assertEqual(scenario['reserved_bytes'], 0)
            self.assertIsNone(scenario['sender_verified_numeric_result'])
            self.assertTrue(scenario['requests'][0]['uncertain_delivery'])
        self.assertEqual(report['scenarios']['all_acks_lost']['receiver_numeric_sum'], 7.75)
        barrier = report['scenarios']['generation_barrier']
        self.assertEqual((barrier['completed'], barrier['aborted']), (1, 1))
        self.assertEqual(barrier['requests'][0]['receiver_execute_count'], 0)
        self.assertEqual(barrier['requests'][1]['delivered_payloads'][0]['payload_hex'],
                         struct.pack('!d', 3.5).hex())


if __name__ == '__main__':
    unittest.main()
