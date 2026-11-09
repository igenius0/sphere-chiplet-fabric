"""Bounded, deterministic DATA/ACK recovery reference; not router RTL.

CRC32 detects accidental errors. It does not authenticate a sender or provide
cryptographic integrity. Logical ticks and the receiver's admission record are
shared by this reference harness; no asynchronous failure-detector claim is made.
"""
import argparse
from dataclasses import asdict, dataclass, field, fields, replace
import heapq
import json
from pathlib import Path
import struct
import zlib


MAX_ID = (1 << 32) - 1
MAX_TICK = (1 << 63) - 1


def _integer(value, name, minimum=0, maximum=MAX_ID):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')
    return value


def _request_id(value):
    if not isinstance(value, str) or not value:
        raise ValueError('request_id must be a nonempty string')
    try:
        encoded = value.encode('utf-8')
    except UnicodeError as error:
        raise ValueError('request_id must be valid UTF-8') from error
    if len(encoded) > 128 or '\x00' in value:
        raise ValueError('request_id must contain at most 128 UTF-8 bytes and no NUL')
    return value


def _add_ticks(start, duration):
    return _integer(start + duration, 'event tick', maximum=MAX_TICK)


@dataclass(frozen=True)
class TransportConfig:
    timeout_ticks: int = 4
    max_retries: int = 2
    max_inflight: int = 16
    max_requests: int = 64
    max_contributions: int = 128
    max_payload_bytes: int = 4096
    max_reserved_bytes: int = 1 << 20
    link_delay_ticks: int = 1
    max_queued_events: int = 65536
    max_wire_copies: int = 16

    def __post_init__(self):
        limits = {
            'timeout_ticks': (1, 1000000), 'max_retries': (0, 16),
            'max_inflight': (1, 4096), 'max_requests': (1, 4096),
            'max_contributions': (1, 4096), 'max_payload_bytes': (1, 1048576),
            'max_reserved_bytes': (0, 1 << 40), 'link_delay_ticks': (1, 1000000),
            'max_queued_events': (1, 1048576), 'max_wire_copies': (1, 64),
        }
        for name, (minimum, maximum) in limits.items():
            _integer(getattr(self, name), name, minimum, maximum)
        if self.max_inflight > self.max_requests:
            raise ValueError('max_inflight must not exceed max_requests')

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict):
            raise ValueError('transport configuration must be an object')
        unknown = set(values) - {item.name for item in fields(cls)}
        if unknown:
            raise ValueError('unknown transport fields: ' + ', '.join(sorted(map(str, unknown))))
        return cls(**values)


def _config(value):
    if value is None:
        return TransportConfig()
    if isinstance(value, TransportConfig):
        return value
    return TransportConfig.from_dict(value)


@dataclass(frozen=True)
class Frame:
    request_id: str
    generation: int
    sequence: int
    source: int
    destination: int
    payload: bytes = b''
    kind: str = 'DATA'
    crc32: int = 0

    def __post_init__(self):
        _request_id(self.request_id)
        for name in ('generation', 'sequence', 'source', 'destination', 'crc32'):
            _integer(getattr(self, name), name)
        if self.kind not in ('DATA', 'ACK'):
            raise ValueError('frame kind must be DATA or ACK')
        if not isinstance(self.payload, bytes):
            raise ValueError('payload must be immutable bytes')
        if self.kind == 'ACK' and self.payload:
            raise ValueError('ACK frames must have an empty payload')

    def expected_crc32(self):
        header = json.dumps([self.kind, self.request_id, self.generation,
                             self.sequence, self.source, self.destination],
                            separators=(',', ':'), ensure_ascii=True).encode('ascii')
        return zlib.crc32(header + b'\x00' + self.payload) & MAX_ID

    @classmethod
    def create(cls, request_id, generation, sequence, source, destination,
               payload=b'', kind='DATA'):
        frame = cls(request_id, generation, sequence, source, destination, payload, kind)
        return replace(frame, crc32=frame.expected_crc32())


@dataclass(frozen=True)
class Fault:
    """A reusable rule applied to matching transmissions, including retries.

    attempt=0 identifies the original transmission. A delay/reorder rule adds
    ticks to the selected frames; delaying an earlier sequence makes it arrive
    after a later one. Duplicate copies pass through later rules as well.
    """
    action: str
    kind: str = None
    request_id: str = None
    sequence: int = None
    attempt: int = None
    generation: int = None
    delay_ticks: int = 0
    copies: int = 1

    def __post_init__(self):
        if self.action not in ('drop', 'partition', 'corrupt', 'duplicate', 'delay', 'reorder'):
            raise ValueError('unknown fault action')
        if self.kind is not None and self.kind not in ('DATA', 'ACK'):
            raise ValueError('fault kind must be DATA, ACK, or None')
        if self.request_id is not None:
            _request_id(self.request_id)
        for name in ('sequence', 'attempt', 'generation'):
            if getattr(self, name) is not None:
                _integer(getattr(self, name), name)
        _integer(self.delay_ticks, 'delay_ticks', 0, 1000000)
        _integer(self.copies, 'copies', 1, 16)
        if self.action in ('delay', 'reorder') and not self.delay_ticks:
            raise ValueError('delay/reorder requires a positive delay_ticks')
        if self.action not in ('delay', 'reorder', 'duplicate') and self.delay_ticks:
            raise ValueError('delay_ticks is only valid for delay/reorder/duplicate')
        if self.action != 'duplicate' and self.copies != 1:
            raise ValueError('copies is only configurable for duplicate faults')

    def matches(self, frame, attempt):
        return all(value is None or value == actual for value, actual in (
            (self.kind, frame.kind), (self.request_id, frame.request_id),
            (self.sequence, frame.sequence), (self.attempt, attempt),
            (self.generation, frame.generation)))


@dataclass
class _Request:
    request_id: str
    generation: int
    source: int
    destination: int
    expected: dict
    reservation_bytes: int
    submitted_tick: int
    deadline_tick: int
    attempts: dict = field(default_factory=dict)
    acked: set = field(default_factory=set)
    delivered: dict = field(default_factory=dict)
    status: str = 'pending'
    reason: str = None
    terminal_tick: int = None
    reservation_released: bool = False
    uncertain_delivery: bool = False


class ReliableTransport:
    """A finite-session protocol oracle with bounded admission/replay state.

    Terminal records are retained until this object is discarded. max_requests
    caps lifetime submissions, including across generations. There is no silent
    eviction or reuse of an identity in the same generation. New generations
    may reuse request names; old frames and ACKs cannot match them.
    """

    def __init__(self, config=None, generation=0, faults=()):
        if config is None:
            config = TransportConfig()
        if not isinstance(config, TransportConfig):
            raise ValueError('config must be TransportConfig')
        _integer(generation, 'generation')
        validated_faults = []
        copies = 1
        for fault in faults:
            if len(validated_faults) == 32 or not isinstance(fault, Fault):
                raise ValueError('faults must contain at most 32 Fault objects')
            if fault.action == 'duplicate':
                copies *= 1 + fault.copies
            if copies > config.max_wire_copies:
                raise ValueError('fault rules exceed bounded max_wire_copies')
            validated_faults.append(fault)
        self.config = config
        self.generation = generation
        self.faults = tuple(validated_faults)
        self.tick = 0
        self.reserved_bytes = 0
        self._requests = {}
        self._events = []
        self._serial = 0
        self._stats = {name: 0 for name in (
            'data_transmissions', 'ack_transmissions', 'wire_copies',
            'fault_drops', 'fault_corruptions', 'fault_duplicates', 'fault_delays',
            'crc_errors', 'unknown_frames', 'stale_frames', 'late_frames',
            'duplicates_suppressed', 'stale_timeouts', 'protocol_errors',
            'receiver_executions', 'completed_requests', 'aborted_requests')}

    def _validate_frame(self, frame):
        if not isinstance(frame, Frame):
            raise ValueError('incoming frame must be Frame')
        # Check again at the boundary, including objects reconstructed externally.
        frame.__post_init__()
        if len(frame.payload) > self.config.max_payload_bytes:
            raise ValueError('frame payload exceeds max_payload_bytes')

    def _wire_plan(self, frame, attempt):
        frames = [(self.config.link_delay_ticks, frame)]
        changes = {'fault_drops': 0, 'fault_corruptions': 0,
                   'fault_duplicates': 0, 'fault_delays': 0}
        for fault in self.faults:
            if not fault.matches(frame, attempt):
                continue
            if fault.action in ('drop', 'partition'):
                changes['fault_drops'] += len(frames)
                frames = []
            elif fault.action == 'corrupt':
                changes['fault_corruptions'] += len(frames)
                frames = [(delay, replace(item, crc32=item.crc32 ^ 1))
                          for delay, item in frames]
            elif fault.action in ('delay', 'reorder'):
                changes['fault_delays'] += len(frames)
                frames = [(delay + fault.delay_ticks, item) for delay, item in frames]
            elif fault.action == 'duplicate':
                extra = [(delay + (copy + 1) * fault.delay_ticks, item)
                         for delay, item in frames for copy in range(fault.copies)]
                changes['fault_duplicates'] += len(extra)
                frames += extra
        plan = [(_add_ticks(self.tick, delay), 'frame', item, attempt)
                for delay, item in frames]
        return plan, changes

    def _enqueue(self, events):
        for tick, kind, value, attempt in events:
            self._serial += 1
            # Arrivals win exact ties against timeouts; this is explicit policy.
            heapq.heappush(self._events, (tick, 0 if kind == 'frame' else 1,
                                         self._serial, kind, value, attempt))

    def _count_emission(self, frame, plan, changes):
        key = 'data_transmissions' if frame.kind == 'DATA' else 'ack_transmissions'
        self._stats[key] += 1
        self._stats['wire_copies'] += len(plan)
        for key, value in changes.items():
            self._stats[key] += value

    def submit(self, request_id, source, destination, contributions,
               reservation_bytes=0, generation=None):
        """Validate the entire admission before changing protocol state.

        contributions is a finite iterable of unique (sequence, bytes) pairs.
        Admission failure raises ValueError without a reservation or queued work.
        """
        _request_id(request_id)
        _integer(source, 'source')
        _integer(destination, 'destination')
        _integer(reservation_bytes, 'reservation_bytes', 0, self.config.max_reserved_bytes)
        if generation is None:
            generation = self.generation
        _integer(generation, 'generation')
        if generation != self.generation:
            raise ValueError('request generation must be current')
        expected = {}
        try:
            iterator = iter(contributions)
            for item in iterator:
                if len(expected) == self.config.max_contributions:
                    raise ValueError('request exceeds max_contributions')
                if not isinstance(item, (tuple, list)) or len(item) != 2:
                    raise ValueError('contributions require (sequence, payload) pairs')
                sequence, payload = item
                _integer(sequence, 'sequence')
                if sequence in expected:
                    raise ValueError('sequence may not be reused inside a request')
                if not isinstance(payload, bytes) or len(payload) > self.config.max_payload_bytes:
                    raise ValueError('contribution must contain bounded immutable bytes')
                expected[sequence] = payload
        except TypeError as error:
            raise ValueError('contributions must be a finite iterable of pairs') from error
        if not expected:
            raise ValueError('request requires at least one contribution')
        key = (generation, request_id)
        if key in self._requests:
            raise ValueError('request_id cannot be reused within a generation')
        if len(self._requests) >= self.config.max_requests:
            raise ValueError('finite session max_requests exhausted; create a new session')
        if sum(request.status == 'pending' for request in self._requests.values()) >= self.config.max_inflight:
            raise ValueError('max_inflight admission capacity exhausted')
        if self.reserved_bytes + reservation_bytes > self.config.max_reserved_bytes:
            raise ValueError('reservation capacity exhausted')
        deadline = _add_ticks(self.tick, (self.config.max_retries + 1) * self.config.timeout_ticks)
        planned, emissions = [], []
        for sequence, payload in expected.items():
            frame = Frame.create(request_id, generation, sequence, source, destination, payload)
            plan, changes = self._wire_plan(frame, 0)
            planned.extend(plan)
            planned.append((_add_ticks(self.tick, self.config.timeout_ticks),
                            'timeout', (key, sequence), 0))
            emissions.append((frame, plan, changes))
        if len(self._events) + len(planned) > self.config.max_queued_events:
            raise ValueError('event admission capacity exhausted')
        state = _Request(request_id, generation, source, destination, expected,
                         reservation_bytes, self.tick, deadline,
                         attempts={sequence: 1 for sequence in expected})
        self._requests[key] = state
        self.reserved_bytes += reservation_bytes
        for frame, plan, changes in emissions:
            self._count_emission(frame, plan, changes)
        self._enqueue(planned)
        return self.request_result(request_id, generation)

    def _terminal(self, state, status, reason=None):
        if state.status != 'pending':
            return
        state.status = status
        state.reason = reason
        state.terminal_tick = self.tick
        state.uncertain_delivery = status == 'aborted' and bool(set(state.expected) - state.acked)
        state.reservation_released = True
        self.reserved_bytes -= state.reservation_bytes
        self._stats['completed_requests' if status == 'completed' else 'aborted_requests'] += 1

    def _emit(self, frame, attempt, state, with_timeout=False):
        try:
            plan, changes = self._wire_plan(frame, attempt)
            events = list(plan)
            if with_timeout:
                events.append((_add_ticks(self.tick, self.config.timeout_ticks),
                               'timeout', ((state.generation, state.request_id), frame.sequence), attempt))
        except ValueError:
            # A valid admission can approach MAX_TICK on a delayed retry or ACK.
            # Treat exhaustion as an explicit abort, including after execution,
            # rather than leaking its reservation through an uncaught overflow.
            self._terminal(state, 'aborted', 'event_tick_exhausted')
            return False
        if len(self._events) + len(events) > self.config.max_queued_events:
            self._terminal(state, 'aborted', 'event_capacity_exhausted')
            return False
        self._count_emission(frame, plan, changes)
        self._enqueue(events)
        return True

    def inject_frame(self, frame, delay_ticks=0, attempt=0):
        """Inject an already-on-wire frame without applying transmission faults."""
        self._validate_frame(frame)
        _integer(delay_ticks, 'delay_ticks', 0, 1000000)
        _integer(attempt, 'attempt', 0, 16)
        tick = _add_ticks(self.tick, delay_ticks)
        if len(self._events) >= self.config.max_queued_events:
            raise ValueError('event injection capacity exhausted')
        self._enqueue([(tick, 'frame', frame, attempt)])

    def _receive(self, frame, attempt):
        self._validate_frame(frame)
        if frame.crc32 != frame.expected_crc32():
            self._stats['crc_errors'] += 1
            return
        if frame.generation != self.generation:
            self._stats['stale_frames'] += 1
            return
        state = self._requests.get((frame.generation, frame.request_id))
        if state is None:
            self._stats['unknown_frames'] += 1
            return
        source, destination = ((state.source, state.destination) if frame.kind == 'DATA'
                               else (state.destination, state.source))
        if (frame.sequence not in state.expected or frame.source != source
                or frame.destination != destination):
            self._stats['protocol_errors'] += 1
            return
        if frame.kind == 'DATA':
            if state.expected[frame.sequence] != frame.payload:
                self._stats['protocol_errors'] += 1
                return
            if state.status == 'aborted' or self.tick > state.deadline_tick:
                self._stats['late_frames'] += 1
                return
            if frame.sequence in state.delivered:
                self._stats['duplicates_suppressed'] += 1
            else:
                state.delivered[frame.sequence] = frame.payload
                self._stats['receiver_executions'] += 1
            ack = Frame.create(frame.request_id, frame.generation, frame.sequence,
                               frame.destination, frame.source, kind='ACK')
            self._emit(ack, attempt, state)
        else:
            if state.status != 'pending':
                self._stats['late_frames'] += 1
                return
            # Audit ACK provenance against actual execution, not mere arrival.
            if frame.sequence not in state.delivered:
                self._stats['protocol_errors'] += 1
                return
            state.acked.add(frame.sequence)
            if len(state.acked) == len(state.expected):
                self._terminal(state, 'completed')

    def _timeout(self, value, attempt):
        key, sequence = value
        state = self._requests.get(key)
        if (state is None or state.status != 'pending' or sequence in state.acked
                or state.attempts[sequence] != attempt + 1):
            self._stats['stale_timeouts'] += 1
            return
        if attempt >= self.config.max_retries:
            self._terminal(state, 'aborted', 'retry_exhausted')
            return
        frame = Frame.create(state.request_id, state.generation, sequence,
                             state.source, state.destination, state.expected[sequence])
        if self._emit(frame, attempt + 1, state, with_timeout=True):
            state.attempts[sequence] += 1

    def run(self, max_ticks=None):
        """Process queued events, optionally for at most max_ticks more ticks.

        An unlimited call drains a finite queue including late duplicates. The
        lifetime/session, retry, copy, payload and event limits bound all state.
        """
        until = None
        if max_ticks is not None:
            _integer(max_ticks, 'max_ticks', 0, MAX_TICK)
            until = _add_ticks(self.tick, max_ticks)
        while self._events and (until is None or self._events[0][0] <= until):
            tick, _, _, kind, value, attempt = heapq.heappop(self._events)
            self.tick = tick
            if kind == 'frame':
                self._receive(value, attempt)
            else:
                self._timeout(value, attempt)
        if until is not None:
            self.tick = until
        return self.report()

    def advance_generation(self, new_generation):
        _integer(new_generation, 'new_generation')
        if new_generation <= self.generation:
            raise ValueError('new_generation must increase monotonically')
        for state in self._requests.values():
            self._terminal(state, 'aborted', 'generation_changed')
        self.generation = new_generation
        return self.report()

    def delivered_payloads(self, request_id, generation=None):
        state = self._state(request_id, generation)
        return tuple(sorted(state.delivered.items()))

    def _state(self, request_id, generation):
        _request_id(request_id)
        if generation is None:
            generation = self.generation
        _integer(generation, 'generation')
        try:
            return self._requests[(generation, request_id)]
        except KeyError as error:
            raise ValueError('unknown request identity') from error

    def request_result(self, request_id, generation=None):
        state = self._state(request_id, generation)
        return {
            'request_id': state.request_id, 'generation': state.generation,
            'source': state.source, 'destination': state.destination,
            'status': state.status, 'reason': state.reason,
            'submitted_tick': state.submitted_tick, 'deadline_tick': state.deadline_tick,
            'terminal_tick': state.terminal_tick,
            'expected_sequences': sorted(state.expected),
            'acked_sequences': sorted(state.acked),
            'unacked_sequences': sorted(set(state.expected) - state.acked),
            'attempts': {str(sequence): attempts for sequence, attempts in sorted(state.attempts.items())},
            'delivered_payloads': [{'sequence': sequence, 'payload_hex': payload.hex()}
                                   for sequence, payload in sorted(state.delivered.items())],
            'receiver_execute_count': len(state.delivered),
            'reservation_bytes': state.reservation_bytes,
            'reservation_released': state.reservation_released,
            'uncertain_delivery': state.uncertain_delivery,
            'sender_outcome': ('acknowledged' if state.status == 'completed' else
                               'uncertain' if state.status == 'aborted' else 'pending'),
        }

    def report(self):
        requests = [self.request_result(request_id, generation)
                    for generation, request_id in self._requests]
        return {
            'schema_version': 1, 'generation': self.generation, 'tick': self.tick,
            'config': asdict(self.config), 'faults': [asdict(fault) for fault in self.faults],
            'submitted': len(requests),
            'completed': sum(item['status'] == 'completed' for item in requests),
            'aborted': sum(item['status'] == 'aborted' for item in requests),
            'pending': sum(item['status'] == 'pending' for item in requests),
            'reserved_bytes': self.reserved_bytes, 'queued_events': len(self._events),
            'stats': dict(self._stats), 'requests': requests,
            'evidence': 'bounded Python protocol reference in logical ticks; not RTL or calibrated link timing',
            'crc_scope': 'CRC32 covers frame identity and payload; accidental-error detection, no authentication',
        }


def demonstrate_transport(config=None):
    """Exercise real numeric payloads under recovery, partition and epoch change."""
    config = _config(config)
    values = (1.25, -2.0, 8.5)
    payloads = [(sequence, struct.pack('!d', value)) for sequence, value in enumerate(values)]
    scenarios = {}
    cases = {
        'healthy': (),
        'recover_drop_corrupt_duplicate_reorder': (
            Fault('drop', 'DATA', sequence=0, attempt=0),
            Fault('corrupt', 'DATA', sequence=1, attempt=0),
            Fault('duplicate', 'DATA', sequence=2, attempt=0, copies=2),
            Fault('reorder', 'DATA', sequence=2, attempt=0, delay_ticks=2),
            Fault('drop', 'ACK', sequence=0, attempt=1),
            Fault('duplicate', 'ACK', sequence=1, attempt=1),
        ),
        'partition': (Fault('partition', 'DATA'),),
        'all_acks_lost': (Fault('drop', 'ACK'),),
    }
    for name, faults in cases.items():
        transport = ReliableTransport(config, faults=faults)
        transport.submit('numeric-step', 0, 1, payloads, reservation_bytes=256)
        report = transport.run()
        report['reference_numeric_sum'] = sum(values)
        report['receiver_numeric_sum'] = sum(struct.unpack('!d', payload)[0]
                                             for _, payload in transport.delivered_payloads('numeric-step'))
        report['sender_verified_numeric_result'] = (report['receiver_numeric_sum']
                                                    if report['completed'] == 1 else None)
        scenarios[name] = report
    barrier = ReliableTransport(config, faults=(Fault('delay', 'DATA', generation=0, delay_ticks=6),))
    barrier.submit('reused-name', 0, 1, [(0, struct.pack('!d', 99.0))], reservation_bytes=128)
    barrier.run(max_ticks=1)
    barrier.advance_generation(1)
    barrier.submit('reused-name', 0, 1, [(0, struct.pack('!d', 3.5))], reservation_bytes=128)
    scenarios['generation_barrier'] = barrier.run()
    return {'schema_version': 1, 'scenarios': scenarios,
            'limitations': [
                'Logical ticks are not nanoseconds or hardware throughput measurements.',
                'CRC32 is not authentication and adversarial forgery is outside the fault model.',
                'Receiver admission, deadlines and generation barriers are globally coordinated in this harness.',
                'Exactly-once is per admitted contribution in a live finite session; restart durability is not modeled.',
                'ACK exhaustion is an uncertain sender outcome even if the receiver executed once.',
            ]}


def run_transport_demo(config=None):
    """Configuration-aware entry point shared with the prototype aggregate CLI."""
    return demonstrate_transport(config)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = demonstrate_transport()
    serialized = json.dumps(report, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding='utf-8')
        print(f'Wrote bounded transport reference to {args.output}')
    else:
        print(serialized, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
