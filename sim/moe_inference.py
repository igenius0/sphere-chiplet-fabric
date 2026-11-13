"""Tiny actual NumPy MoE inference with a separately assumed island schedule.

Numerics use synthetic float64 weights on one CPU process. Modeled network
cycles are not measured CPU or accelerator performance. Run as a module or a
direct script; importing the module performs no inference or file writes.
"""
import argparse
from dataclasses import asdict, dataclass, fields
import json
import math
from numbers import Integral
from pathlib import Path
import sys

import numpy as np

try:
    from .validation import finite, integer
except ImportError:
    from validation import finite, integer


@dataclass(frozen=True)
class InferenceConfig:
    seed: int = 7
    vocab_size: int = 32
    model_dim: int = 16
    attention_heads: int = 2
    expert_hidden: int = 32
    num_experts: int = 8
    top_k: int = 2
    num_islands: int = 4
    max_seq_len: int = 32
    prompt_tokens: tuple = (1, 4, 7, 2)
    decode_tokens: int = 4
    generation: int = 1
    layer_norm_epsilon: float = 1e-5
    compute_ops_per_cycle: int = 256
    island_capacity_bytes: object = 1048576
    flit_bytes: int = 64
    buffer_flits_per_vc: int = 4

    def __post_init__(self):
        limits = {'seed': (0, 2**32-1), 'vocab_size': (2, 512),
                  'model_dim': (2, 128), 'attention_heads': (1, 16),
                  'expert_hidden': (1, 512), 'num_experts': (1, 32),
                  'top_k': (1, 32), 'max_seq_len': (1, 256),
                  'decode_tokens': (0, 64), 'generation': (1, 2**31-1),
                  'compute_ops_per_cycle': (1, 10**9),
                  'flit_bytes': (1, 4096), 'buffer_flits_per_vc': (1, 1024)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            integer(value, name, low)
            if value > high:
                raise ValueError(f'{name} must be <= {high}')
            object.__setattr__(self, name, int(value))
        integer(self.num_islands, 'num_islands', 4)
        if self.num_islands != 4:
            raise ValueError('this prototype defines exactly four islands')
        object.__setattr__(self, 'num_islands', int(self.num_islands))
        if self.model_dim % self.attention_heads:
            raise ValueError('model_dim must be divisible by attention_heads')
        if self.top_k > self.num_experts:
            raise ValueError('top_k must not exceed num_experts')
        finite(self.layer_norm_epsilon, 'layer_norm_epsilon', strict=True)
        object.__setattr__(self, 'layer_norm_epsilon', float(self.layer_norm_epsilon))
        if not isinstance(self.prompt_tokens, (tuple, list)) or not self.prompt_tokens:
            raise ValueError('prompt_tokens must be a nonempty integer sequence')
        tokens = tuple(_token(t, self.vocab_size) for t in self.prompt_tokens)
        object.__setattr__(self, 'prompt_tokens', tokens)
        if len(tokens) + self.decode_tokens > self.max_seq_len:
            raise ValueError('prompt plus decode_tokens exceeds max_seq_len')
        capacity = self.island_capacity_bytes
        if isinstance(capacity, Integral) and not isinstance(capacity, bool):
            capacity = (integer(capacity, 'island_capacity_bytes', 1),) * 4
        if not isinstance(capacity, (tuple, list)) or len(capacity) != 4:
            raise ValueError('island_capacity_bytes must be a positive integer or four integers')
        capacity = tuple(int(integer(c, 'island_capacity_bytes', 1)) for c in capacity)
        object.__setattr__(self, 'island_capacity_bytes', capacity)

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict):
            raise ValueError('inference configuration must be an object')
        if not all(isinstance(key, str) for key in values):
            raise ValueError('inference configuration keys must be strings')
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError('unknown inference fields: ' + ', '.join(sorted(unknown)))
        return cls(**values)


def _config(value):
    if value is None:
        return InferenceConfig()
    if isinstance(value, InferenceConfig):
        return value
    return InferenceConfig.from_dict(value)


def _token(value, vocabulary):
    integer(value, 'token', 0)
    if value >= vocabulary:
        raise ValueError('token is outside vocabulary')
    return int(value)


def _tokens(values, vocabulary, name):
    if (not isinstance(values, (tuple, list, np.ndarray))
            or isinstance(values, np.ndarray) and values.ndim != 1
            or len(values) < 1):
        raise ValueError(f'{name} must be a nonempty one-dimensional integer sequence')
    return [_token(value, vocabulary) for value in values]


@dataclass
class ModelWeights:
    embedding: np.ndarray
    position: np.ndarray
    wq: np.ndarray
    wk: np.ndarray
    wv: np.ndarray
    wo: np.ndarray
    gate: np.ndarray
    expert_w1: np.ndarray
    expert_w2: np.ndarray
    lm_head: np.ndarray
    ln1_scale: np.ndarray
    ln2_scale: np.ndarray
    final_scale: np.ndarray


def _weights(weights, config, copy=False):
    if isinstance(weights, dict):
        try:
            weights = ModelWeights(**weights)
        except TypeError as error:
            raise ValueError('weights must contain exactly the named model arrays') from error
    if not isinstance(weights, ModelWeights):
        raise ValueError('weights must be ModelWeights or a model-array object')
    d, h, e, v, n = (config.model_dim, config.expert_hidden,
                      config.num_experts, config.vocab_size, config.max_seq_len)
    shapes = {'embedding': (v, d), 'position': (n, d),
              'wq': (d, d), 'wk': (d, d), 'wv': (d, d), 'wo': (d, d),
              'gate': (d, e), 'expert_w1': (e, d, h), 'expert_w2': (e, h, d),
              'lm_head': (d, v), 'ln1_scale': (d,), 'ln2_scale': (d,),
              'final_scale': (d,)}
    arrays = {}
    for name, shape in shapes.items():
        try:
            raw = np.asarray(getattr(weights, name))
            if raw.shape != shape or raw.dtype.kind not in 'fiu':
                raise ValueError(f'{name} must be a real numeric array with shape {shape}')
            array = np.array(raw, dtype=np.float64, copy=True) if copy else np.asarray(raw, dtype=np.float64)
            if not np.isfinite(array).all():
                raise ValueError(f'{name} must contain finite float64 values')
            if copy:
                array.flags.writeable = False
            arrays[name] = array
        except (OverflowError, TypeError) as error:
            raise ValueError(f'{name} is not a representable float64 array') from error
    return ModelWeights(**arrays)


def create_weights(config=None):
    """Seeded synthetic weights; no learned-language-quality claim."""
    c = _config(config)
    rng = np.random.default_rng(c.seed)
    d, h, e, v = c.model_dim, c.expert_hidden, c.num_experts, c.vocab_size
    def normal(shape):
        return rng.normal(0.0, 0.12, shape).astype(np.float64)
    return ModelWeights(normal((v, d)), normal((c.max_seq_len, d)),
                        normal((d, d)), normal((d, d)), normal((d, d)), normal((d, d)),
                        normal((d, e)), normal((e, d, h)), normal((e, h, d)),
                        normal((d, v)), np.ones(d), np.ones(d), np.ones(d))


def reference_logits(weights, tokens, config=None):
    """Independent vectorized full-prefix causal decoder; never uses KV cache.

    Expert contributions are formed as full sequence-by-expert tensors and
    summed through routing masks, independently of the island execution path.
    """
    c = _config(config)
    w = _weights(weights, c)
    ids = _tokens(tokens, c.vocab_size, 'tokens')
    n, d, heads = len(ids), c.model_dim, c.attention_heads
    if n > c.max_seq_len:
        raise ValueError('sequence exceeds max_seq_len')
    try:
        with np.errstate(over='raise', invalid='raise', divide='raise'):
            def norm(x, scale):
                centered = x - x.mean(axis=-1, keepdims=True)
                variance = np.mean(centered * centered, axis=-1, keepdims=True)
                return centered / np.sqrt(variance + c.layer_norm_epsilon) * scale
            x = w.embedding[ids] + w.position[:n]
            z = norm(x, w.ln1_scale)
            q = (z @ w.wq).reshape(n, heads, d // heads)
            k = (z @ w.wk).reshape(n, heads, d // heads)
            v = (z @ w.wv).reshape(n, heads, d // heads)
            scores = np.einsum('thd,shd->hts', q, k) / math.sqrt(d // heads)
            scores = np.where(np.tril(np.ones((n, n), dtype=bool))[None], scores, -np.inf)
            attention = np.exp(scores - scores.max(axis=-1, keepdims=True))
            attention /= attention.sum(axis=-1, keepdims=True)
            context = np.einsum('hts,shd->thd', attention, v).reshape(n, d)
            x = x + context @ w.wo
            z = norm(x, w.ln2_scale)
            gate_logits = z @ w.gate
            selected = np.argsort(-gate_logits, axis=-1, kind='stable')[:, :c.top_k]
            picked = np.take_along_axis(gate_logits, selected, axis=1)
            probabilities = np.exp(picked - picked.max(axis=1, keepdims=True))
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            routing = np.zeros((n, c.num_experts), dtype=np.float64)
            np.put_along_axis(routing, selected, probabilities, axis=1)
            hidden = np.maximum(np.einsum('td,edh->teh', z, w.expert_w1), 0.0)
            expert_outputs = np.einsum('teh,ehd->ted', hidden, w.expert_w2)
            combined = np.einsum('te,ted->td', routing, expert_outputs)
            logits = norm(x + combined, w.final_scale) @ w.lm_head
            if not np.isfinite(logits).all():
                raise ValueError('non-finite reference result')
            return logits
    except FloatingPointError as error:
        raise ValueError('weights produce unrepresentable reference arithmetic') from error


@dataclass
class KVCache:
    keys: np.ndarray
    values: np.ndarray
    generation: int
    length: int = 0


def island_topology():
    """Logical tree, not optimized wiring: 1--0--2--3, attention owner 0."""
    return {'name': 'four_island_hierarchical_tree', 'nodes': [0, 1, 2, 3],
            'adj': {0: [1, 2], 1: [0], 2: [0, 3], 3: [2]}}


class IslandDecoder:
    """Cached numerical decoder and conservative serialized-token schedule.

    Dispatch and its credits drain before expert/combine work starts. Experts sharing an
    island execute serially; different islands execute in parallel in the
    assumed cycle model. A next token starts after every combine completes.
    """
    def __init__(self, weights=None, config=None, contribution_transport=None):
        self.config = _config(config)
        if contribution_transport is not None:
            try:
                from .transport import ReliableTransport
            except ImportError:
                from transport import ReliableTransport
            if not isinstance(contribution_transport, ReliableTransport):
                raise ValueError('contribution_transport must be ReliableTransport or None')
            if contribution_transport.generation != self.config.generation:
                raise ValueError('contribution transport generation must match the decoder')
        self.contribution_transport = contribution_transport
        self.weights = _weights(create_weights(self.config) if weights is None else weights,
                                self.config, copy=True)
        c = self.config
        self.cache = KVCache(np.zeros((c.max_seq_len, c.attention_heads, c.model_dim // c.attention_heads)),
                             np.zeros((c.max_seq_len, c.attention_heads, c.model_dim // c.attention_heads)),
                             c.generation)
        # These are views of the single host model allocation, with explicit
        # logical owners. The compute path can only address its owner's views.
        self.expert_arrays = [{expert: (self.weights.expert_w1[expert],
                                       self.weights.expert_w2[expert])
                               for expert in range(c.num_experts) if expert % 4 == owner}
                              for owner in range(4)]
        self.workspace = []
        for island in range(4):
            workspace = {'expert_input': np.zeros(c.model_dim),
                         'expert_hidden': np.zeros(c.expert_hidden),
                         'expert_output': np.zeros(c.model_dim)}
            if island == 0:
                workspace.update({'attention_scores': np.zeros((c.max_seq_len, c.attention_heads)),
                                  'gate_logits': np.zeros(c.num_experts),
                                  'contributions': np.zeros((c.top_k, c.model_dim)),
                                  'token_state': np.zeros(c.model_dim),
                                  'logits': np.zeros(c.vocab_size)})
            self.workspace.append(workspace)
        self.cycle = 0
        self.traces = []
        self.tokens = []
        self.memory = self._memory_report()
        for island in self.memory['islands']:
            if island['total_budgeted_tensor_bytes'] > island['capacity_bytes']:
                raise ValueError(f"island {island['island']} exceeds its tensor capacity")

    def _memory_report(self):
        c, w = self.config, self.weights
        inventory = []
        def record(name, owner, array, category):
            inventory.append({'name': name, 'owner_island': owner, 'category': category,
                              'shape': list(array.shape), 'dtype': str(array.dtype),
                              'itemsize_bytes': int(array.itemsize), 'elements': int(array.size),
                              'bytes': int(array.nbytes)})
        for field in fields(w):
            if field.name not in ('expert_w1', 'expert_w2'):
                record(field.name, 0, getattr(w, field.name), 'resident_weights')
        for expert in range(c.num_experts):
            record(f'expert_{expert}_w1', expert % 4, w.expert_w1[expert], 'resident_weights')
            record(f'expert_{expert}_w2', expert % 4, w.expert_w2[expert], 'resident_weights')
        record('kv_keys', 0, self.cache.keys, 'kv_cache')
        record('kv_values', 0, self.cache.values, 'kv_cache')
        for owner, workspace in enumerate(self.workspace):
            for name, array in workspace.items():
                record(name, owner, array, 'reserved_workspace')
        islands = []
        for owner in range(4):
            entry = {'island': owner, 'capacity_bytes': c.island_capacity_bytes[owner],
                     'owned_experts': [e for e in range(c.num_experts) if e % 4 == owner]}
            for category in ('resident_weights', 'kv_cache', 'reserved_workspace'):
                entry[category + '_bytes'] = sum(a['bytes'] for a in inventory
                                                if a['owner_island'] == owner and a['category'] == category)
            entry['total_budgeted_tensor_bytes'] = sum(entry[k + '_bytes'] for k in
                                                       ('resident_weights', 'kv_cache', 'reserved_workspace'))
            entry['available_tensor_bytes'] = entry['capacity_bytes'] - entry['total_budgeted_tensor_bytes']
            islands.append(entry)
        return {'islands': islands, 'array_inventory': inventory,
                'total_budgeted_tensor_bytes': sum(a['bytes'] for a in inventory),
                'interpretation': 'Actual NumPy resident/cache/reserved-array nbytes with logical ownership; '
                                  'not physical allocation across devices or complete CPU peak/RSS.',
                'excluded_memory': ['Python/runtime objects', 'NumPy/BLAS temporary allocations',
                                    'network simulator state', 'host copies of model inputs/reference']}

    def _network(self, assignments, phase):
        try:
            from .finite_buffer import NetworkConfig, Packet, simulate_network
        except ImportError:
            from finite_buffer import NetworkConfig, Packet, simulate_network
        c = self.config
        itemsize = self.workspace[0]['expert_input'].itemsize
        packets = [Packet(packet_id=a['expert'], source=0 if phase == 'dispatch' else a['island'],
                          destination=a['island'] if phase == 'dispatch' else 0,
                          flits=math.ceil(c.model_dim * itemsize / c.flit_bytes),
                          release_cycle=a.get('release_cycle', 0))
                   for a in assignments if not a['local']]
        report = simulate_network(island_topology(), packets, NetworkConfig(
            buffer_flits_per_vc=c.buffer_flits_per_vc, flit_bytes=c.flit_bytes,
            max_cycles=max(100000, max((p.release_cycle for p in packets), default=0) + 10000)))
        entries = report['deliveries']
        if (report['status'] != 'completed' or not report['all_requested_delivered']
                or not report['idle'] or report['pending_packets'] or report['rejected_packets']
                or len(entries) != len(packets)):
            raise ValueError('network phase did not deliver every required contribution')
        deliveries = {p['packet_id']: p['completion_cycle'] for p in entries}
        if len(deliveries) != len(entries) or set(deliveries) != {p.packet_id for p in packets}:
            raise ValueError('network delivery identities differ from required contributions')
        expected = {packet.packet_id: packet for packet in packets}
        elapsed = integer(report['elapsed_cycles'], 'network elapsed_cycles', 0)
        for entry in entries:
            packet = expected[entry['packet_id']]
            completion = integer(entry['completion_cycle'], 'packet completion_cycle', packet.release_cycle)
            if (completion >= elapsed or entry['source'] != packet.source
                    or entry['destination'] != packet.destination or entry['flits'] != packet.flits
                    or entry['release_cycle'] != packet.release_cycle):
                raise ValueError('network delivery does not match the required packet')
        accounting = report['accounting']
        required_flits = sum(packet.flits for packet in packets)
        if (not accounting['conservation_ok'] or accounting['requested_packets'] != len(packets)
                or accounting['accepted_packets'] != len(packets)
                or accounting['delivered_packets'] != len(packets)
                or accounting['pending_packets'] or accounting['rejected_packets']
                or accounting['requested_flits'] != required_flits
                or accounting['accepted_flits'] != required_flits
                or accounting['injected_flits'] != required_flits
                or accounting['delivered_flits'] != required_flits
                or any(accounting[key] for key in ('buffered_flits', 'inflight_flits',
                                                   'uninjected_flits', 'rejected_flits'))
                or report['inflight'] or report['credit_returns']
                or any(q['current'] for q in report['occupancies'])
                or any(credit['available'] != credit['capacity'] or credit['resident']
                       or credit['inflight'] or credit['return_pending'] for credit in report['credits'])):
            raise ValueError('network phase has an incomplete flit or credit ledger')
        return deliveries, report

    def _reliable_contributions(self, assignments, payloads, position):
        """Optional DATA/ACK correctness harness in a separate tick domain.

        Finite-buffer phase cycles do not acquire a fabricated retransmission
        cost. Only fully acknowledged, byte-exact, unique receiver outputs may
        enter the numerical combine. A failed transport leaves the KV cache
        uncommitted and requires a fresh request/session for another attempt.
        """
        transport = self.contribution_transport
        if transport is None:
            return payloads, []
        c = self.config
        if transport.generation != c.generation:
            raise ValueError('stale contribution transport generation')
        requests, received = [], {}
        for assignment in assignments:
            if assignment['local']:
                continue
            expert = assignment['expert']
            request_id = f'token-{position}-expert-{expert}'
            transport.submit(request_id, assignment['island'], 0, [(0, payloads[expert])],
                             reservation_bytes=len(payloads[expert]), generation=c.generation)
            requests.append((expert, request_id))
            # Finish each admitted contribution before admitting another. A
            # later capacity rejection must not strand earlier reservations.
            # Numerical combine and cache commit still wait for every output.
            transport.run()
            result = transport.request_result(request_id, c.generation)
            delivered = transport.delivered_payloads(request_id, c.generation)
            if (result['status'] != 'completed' or result['sender_outcome'] != 'acknowledged'
                    or result['uncertain_delivery'] or not result['reservation_released']
                    or result['receiver_execute_count'] != 1
                    or delivered != ((0, payloads[expert]),)):
                raise ValueError('expert contribution transport did not acknowledge one exact payload')
            received[expert] = delivered[0][1]
        return received, [request_id for _, request_id in requests]

    def _norm(self, x, scale):
        centered = x - np.mean(x)
        return centered / np.sqrt(np.mean(centered * centered) + self.config.layer_norm_epsilon) * scale

    def _cycles(self, operations):
        return max(1, math.ceil(operations / self.config.compute_ops_per_cycle))

    def step(self, token, generation=None):
        c, w = self.config, self.weights
        token = _token(token, c.vocab_size)
        actual_generation = c.generation if generation is None else integer(generation, 'generation', 1)
        if actual_generation != self.cache.generation or actual_generation != c.generation:
            raise ValueError('stale or mismatched execution generation')
        if self.cache.length >= c.max_seq_len:
            raise ValueError('KV cache capacity exhausted')
        pos, d, heads = self.cache.length, c.model_dim, c.attention_heads
        try:
            with np.errstate(over='raise', invalid='raise', divide='raise'):
                x = w.embedding[token] + w.position[pos]
                z = self._norm(x, w.ln1_scale)
                q = (z @ w.wq).reshape(heads, d // heads)
                new_k = (z @ w.wk).reshape(heads, d // heads)
                new_v = (z @ w.wv).reshape(heads, d // heads)
                # Stage updates; the cache commits only after a completed token.
                keys = np.concatenate((self.cache.keys[:pos], new_k[None]), axis=0)
                values = np.concatenate((self.cache.values[:pos], new_v[None]), axis=0)
                scores = self.workspace[0]['attention_scores'][:pos+1]
                scores[:] = np.einsum('hd,thd->th', q, keys) / math.sqrt(d // heads)
                scores -= scores.max(axis=0, keepdims=True)
                np.exp(scores, out=scores)
                scores /= scores.sum(axis=0, keepdims=True)
                context = np.einsum('th,thd->hd', scores, values).reshape(d)
                x = x + context @ w.wo
                z = self._norm(x, w.ln2_scale)
                gate = self.workspace[0]['gate_logits']
                np.matmul(z, w.gate, out=gate)
                selected = np.argsort(-gate, kind='stable')[:c.top_k]
                picked = gate[selected]
                probabilities = np.exp(picked - picked.max())
                probabilities /= probabilities.sum()
                shared_ops = 8*d*d + 4*(pos+1)*d + 2*d*c.num_experts + 12*d
                dispatch_start = self.cycle + self._cycles(shared_ops)
                assignments = [{'expert': int(e), 'island': int(e % 4),
                                'gate_weight': float(probabilities[i]), 'local': bool(e % 4 == 0)}
                               for i, e in enumerate(selected)]
                # The flit simulator models byte counts/identities, not data
                # bits. Stage the actual vector bytes and expose them to the
                # remote owner only after its packet has completed.
                dispatch_payload = z.tobytes(order='C')
                dispatch, dispatch_report = self._network(assignments, 'dispatch')
                dispatch_barrier = dispatch_start + dispatch_report['elapsed_cycles']
                island_ready = [dispatch_barrier] * 4
                contributions = self.workspace[0]['contributions']
                output_payloads = {}
                seen = set()
                for index, assignment in enumerate(assignments):
                    expert, owner = assignment['expert'], assignment['island']
                    identity = (c.generation, pos, expert)
                    if identity in seen:
                        raise ValueError('duplicate expert contribution')
                    seen.add(identity)
                    workspace = self.workspace[owner]
                    workspace['expert_input'][:] = (z if assignment['local'] else
                        np.frombuffer(dispatch_payload, dtype=z.dtype))
                    expert_w1, expert_w2 = self.expert_arrays[owner][expert]
                    np.matmul(workspace['expert_input'], expert_w1, out=workspace['expert_hidden'])
                    np.maximum(workspace['expert_hidden'], 0.0, out=workspace['expert_hidden'])
                    np.matmul(workspace['expert_hidden'], expert_w2, out=workspace['expert_output'])
                    if assignment['local']:
                        contributions[index] = workspace['expert_output']
                    else:
                        output_payloads[expert] = workspace['expert_output'].tobytes(order='C')
                    start = island_ready[owner]
                    end = start + self._cycles(4*d*c.expert_hidden + c.expert_hidden)
                    island_ready[owner] = end
                    assignment.update({'contribution_id': list(identity),
                                       'dispatch_completion_cycle': dispatch_start + dispatch.get(expert, 0),
                                       'compute_start_cycle': start, 'compute_end_cycle': end,
                                       'dispatch_payload_bytes': len(dispatch_payload),
                                       'combine_payload_bytes': int(workspace['expert_output'].nbytes),
                                       'release_cycle': end - dispatch_barrier})
                combine, combine_report = self._network(assignments, 'combine')
                output_payloads, transport_requests = self._reliable_contributions(assignments, output_payloads, pos)
                for index, assignment in enumerate(assignments):
                    if not assignment['local']:
                        contributions[index] = np.frombuffer(output_payloads[assignment['expert']], dtype=z.dtype)
                    contributions[index] *= assignment['gate_weight']
                    assignment['combine_completion_cycle'] = (assignment['compute_end_cycle']
                        if assignment['local'] else dispatch_barrier + combine[assignment['expert']])
                    assignment['completed_exactly_once'] = True
                    del assignment['release_cycle']
                if len(seen) != c.top_k:
                    raise ValueError('missing expert contribution')
                combined = np.sum(contributions, axis=0)
                logits = self._norm(x + combined, w.final_scale) @ w.lm_head
                if not np.isfinite(logits).all():
                    raise ValueError('non-finite inference result')
                combine_barrier = max(dispatch_barrier + combine_report['elapsed_cycles'],
                                      max(a['combine_completion_cycle'] for a in assignments))
                end_cycle = combine_barrier + self._cycles(2*d*c.vocab_size + 2*d*c.top_k + 6*d)
        except FloatingPointError as error:
            raise ValueError('weights produce unrepresentable decoder arithmetic') from error
        # No generation/capacity/numeric/network failure can extend this cache.
        self.cache.keys[pos] = new_k
        self.cache.values[pos] = new_v
        self.cache.length += 1
        self.workspace[0]['token_state'][:] = x + combined
        self.workspace[0]['logits'][:] = logits
        self.tokens.append(token)
        local_count = sum(a['local'] for a in assignments)
        payload_bytes = int(z.nbytes)
        itemsize = int(z.itemsize)
        weight_reads = [0] * 4
        for a in assignments:
            weight_reads[a['island']] += int(w.expert_w1[a['expert']].nbytes + w.expert_w2[a['expert']].nbytes)
        by_island = [{'island': owner, 'dispatch_payload_sent': 0, 'dispatch_payload_received': 0,
                      'combine_payload_sent': 0, 'combine_payload_received': 0,
                      'dispatch_local_bypass': 0, 'combine_local_bypass': 0,
                      'expert_local_weight_reads': weight_reads[owner],
                      'kv_local_read': pos*2*d*itemsize if owner == 0 else 0,
                      'kv_local_write': 2*d*itemsize if owner == 0 else 0}
                     for owner in range(4)]
        for assignment in assignments:
            owner = assignment['island']
            if assignment['local']:
                by_island[0]['dispatch_local_bypass'] += payload_bytes
                by_island[0]['combine_local_bypass'] += payload_bytes
            else:
                by_island[0]['dispatch_payload_sent'] += payload_bytes
                by_island[owner]['dispatch_payload_received'] += payload_bytes
                by_island[owner]['combine_payload_sent'] += payload_bytes
                by_island[0]['combine_payload_received'] += payload_bytes
        remote_flit_bytes = ((c.top_k-local_count) * math.ceil(payload_bytes / c.flit_bytes)
                             * c.flit_bytes)
        trace = {'token_position': pos, 'token_id': token, 'generation': c.generation,
                 'start_cycle': self.cycle, 'dispatch_start_cycle': dispatch_start,
                 'dispatch_barrier_cycle': dispatch_barrier, 'combine_barrier_cycle': combine_barrier,
                 'end_cycle': end_cycle, 'assignments': assignments,
                 'contributions_expected': c.top_k, 'contributions_completed': len(seen),
                 'transport_request_ids': transport_requests,
                 'activation_dtype': str(z.dtype), 'activation_itemsize_bytes': itemsize,
                 'modeled_operations': {'shared_before_dispatch': shared_ops,
                                        'each_selected_expert': 4*d*c.expert_hidden + c.expert_hidden,
                                        'final_combine_and_head': 2*d*c.vocab_size + 2*d*c.top_k + 6*d},
                 'bytes': {'dispatch_remote_payload': (c.top_k-local_count)*payload_bytes,
                           'combine_remote_payload': (c.top_k-local_count)*payload_bytes,
                           'dispatch_remote_flit_bytes': remote_flit_bytes,
                           'combine_remote_flit_bytes': remote_flit_bytes,
                           'dispatch_remote_padding': remote_flit_bytes - (c.top_k-local_count)*payload_bytes,
                           'combine_remote_padding': remote_flit_bytes - (c.top_k-local_count)*payload_bytes,
                           'dispatch_link_flit_bytes': sum(p['flits']*(len(p['route'])-1)*c.flit_bytes
                                                           for p in dispatch_report['deliveries']),
                           'combine_link_flit_bytes': sum(p['flits']*(len(p['route'])-1)*c.flit_bytes
                                                          for p in combine_report['deliveries']),
                           'dispatch_local_bypass': local_count*payload_bytes,
                           'combine_local_bypass': local_count*payload_bytes,
                           'expert_local_weight_reads_by_island': weight_reads,
                           'kv_local_read': pos*2*d*itemsize, 'kv_local_write': 2*d*itemsize,
                           'by_island': by_island},
                 'network': {'dispatch': dispatch_report, 'combine': combine_report}}
        self.traces.append(trace)
        self.cycle = end_cycle
        return logits.copy()

    def prefill(self, tokens, generation=None):
        checked = _tokens(tokens, self.config.vocab_size, 'prefill tokens')
        if self.cache.length + len(checked) > self.config.max_seq_len:
            raise ValueError('prefill exceeds KV cache capacity')
        return np.stack([self.step(token, generation) for token in checked])

    def report(self):
        totals = {name: sum(trace['bytes'][name] for trace in self.traces)
                  for name in ('dispatch_remote_payload', 'combine_remote_payload',
                               'dispatch_remote_flit_bytes', 'combine_remote_flit_bytes',
                               'dispatch_remote_padding', 'combine_remote_padding',
                               'dispatch_link_flit_bytes', 'combine_link_flit_bytes',
                               'dispatch_local_bypass', 'combine_local_bypass',
                               'kv_local_read', 'kv_local_write')}
        totals['expert_local_weight_reads_by_island'] = [
            sum(trace['bytes']['expert_local_weight_reads_by_island'][owner] for trace in self.traces)
            for owner in range(4)]
        totals['by_island'] = [
            {name: owner if name == 'island' else sum(trace['bytes']['by_island'][owner][name]
                                                      for trace in self.traces)
             for name in ('island', 'dispatch_payload_sent', 'dispatch_payload_received',
                          'combine_payload_sent', 'combine_payload_received',
                          'dispatch_local_bypass', 'combine_local_bypass',
                          'expert_local_weight_reads', 'kv_local_read', 'kv_local_write')}
            for owner in range(4)]
        return {'generation': self.cache.generation, 'cache_length': self.cache.length,
                'processed_tokens': list(self.tokens), 'modeled_cycles': self.cycle,
                'memory': self.memory, 'token_traces': self.traces,
                'contributions_expected': len(self.traces)*self.config.top_k,
                'contributions_completed': sum(t['contributions_completed'] for t in self.traces),
                'bytes': totals,
                'contribution_transport_enabled': self.contribution_transport is not None,
                'expert_ownership': {str(e): e % 4 for e in range(self.config.num_experts)}}


def run_inference_demo(config=None):
    c = _config(config)
    weights = create_weights(c)
    decoder = IslandDecoder(weights, c)
    prompt = list(c.prompt_tokens)
    prefill = decoder.prefill(prompt)
    reference = reference_logits(weights, prompt, c)
    max_error = float(np.max(np.abs(prefill-reference)))
    all_close = bool(np.allclose(prefill, reference, rtol=1e-10, atol=1e-10))
    generated, reference_generated = [], []
    sequence, reference_sequence = prompt[:], prompt[:]
    logits = prefill[-1]
    for _ in range(c.decode_tokens):
        token = int(np.argmax(logits))
        reference_token = int(np.argmax(reference[-1]))
        generated.append(token)
        reference_generated.append(reference_token)
        sequence.append(token)
        reference_sequence.append(reference_token)
        logits = decoder.step(token)
        reference = reference_logits(weights, reference_sequence, c)
        max_error = max(max_error, float(np.max(np.abs(logits-reference[-1]))))
        all_close = all_close and bool(np.allclose(logits, reference[-1], rtol=1e-10, atol=1e-10))
    report = {'schema_version': 1, 'experiment': 'tiny_actual_causal_moe_inference',
              'config': asdict(c), 'prompt_tokens': prompt, 'generated_tokens': generated,
              'reference_generated_tokens': reference_generated, 'sequence_tokens': sequence,
              'final_logits': logits.tolist(),
              'numerical_validation': {'reference': 'independent vectorized full-prefix attention and all-expert tensor evaluation',
                                       'dtype': 'float64', 'all_logits_close': all_close,
                                       'token_logits_compared': len(prompt) + c.decode_tokens,
                                       'maximum_absolute_logit_error': max_error,
                                       'absolute_tolerance': 1e-10, 'relative_tolerance': 1e-10,
                                       'greedy_tokens_match': generated == reference_generated},
              'execution': decoder.report(),
              'assumptions': {'weights': 'seeded synthetic arrays; no pretrained language quality',
                              'numerics': 'actual CPU NumPy evaluation; logical ownership on one process',
                              'cycle_model': 'assumed operations/cycle, fixed network timing; no measured wall time or ASIC projection',
                              'schedule': 'sequential tokens, dispatch and credit-drain barrier, per-island serial experts, dependency-released combine, final credit drain',
                              'network': 'finite-buffer credit/backpressure logical four-island tree; no physical PHY mapping',
                              'payload': 'actual float64 activation/output byte handoffs gated by complete packet identities; no packet headers/metadata or retries charged',
                              'memory': 'actual resident/cache/reserved tensor nbytes; excludes host/runtime and temporary peak allocations'}}
    # Roundtrip makes tuples/NumPy scalar avoidance explicit for Python callers.
    return json.loads(json.dumps(report, allow_nan=False))


def run_inference_fault_demo():
    """One fixed actual token, four experts, recoverable faults and ACK loss.

    ReliableTransport is a separate bounded correctness oracle. Its ticks are
    never added to, or interpreted as, the finite-buffer cycle estimate.
    """
    try:
        from .transport import Fault, ReliableTransport, TransportConfig
    except ImportError:
        from transport import Fault, ReliableTransport, TransportConfig
    c = InferenceConfig(seed=107, num_experts=4, top_k=4, max_seq_len=1,
                        prompt_tokens=(1,), decode_tokens=0)
    weights = create_weights(c)
    weights.gate[:] = 0.0  # Deterministically select all owners with equal gates.
    faults = (Fault('drop', 'DATA', request_id='token-0-expert-1', sequence=0, attempt=0),
              Fault('drop', 'ACK', request_id='token-0-expert-1', sequence=0, attempt=1),
              Fault('corrupt', 'DATA', request_id='token-0-expert-2', sequence=0, attempt=0),
              Fault('duplicate', 'DATA', request_id='token-0-expert-3', sequence=0, attempt=0, copies=2))
    transport = ReliableTransport(TransportConfig(), generation=c.generation, faults=faults)
    decoder = IslandDecoder(weights, c, contribution_transport=transport)
    logits = decoder.step(c.prompt_tokens[0])
    reference = reference_logits(weights, c.prompt_tokens, c)[-1]
    close = bool(np.allclose(logits, reference, rtol=1e-10, atol=1e-10))
    recovery_report = transport.report()
    all_acks_lost = ReliableTransport(TransportConfig(), generation=c.generation,
                                      faults=(Fault('drop', 'ACK'),))
    blocked = IslandDecoder(weights, c, contribution_transport=all_acks_lost)
    failure = None
    try:
        blocked.step(c.prompt_tokens[0])
    except ValueError as error:
        failure = str(error)
    abort_report = all_acks_lost.report()
    report = {'schema_version': 1, 'experiment': 'actual_moe_expert_contribution_fault_recovery',
              'config': asdict(c), 'token_id': c.prompt_tokens[0],
              'final_logits': logits.tolist(), 'reference_logits': reference.tolist(),
              'numerical_validation': {'all_logits_close': close,
                                       'maximum_absolute_logit_error': float(np.max(np.abs(logits-reference))),
                                       'absolute_tolerance': 1e-10, 'relative_tolerance': 1e-10,
                                       'remote_contributions_executed_once': recovery_report['stats']['receiver_executions'] == 3,
                                       'weighted_contributions_combined_once': decoder.report()['contributions_completed'] == 4},
              'execution': decoder.report(), 'recovery': recovery_report,
              'all_acks_lost': {'error': failure, 'cache_length': blocked.cache.length,
                                'combined_contributions': blocked.report()['contributions_completed'],
                                'aborted_without_cache_commit': failure is not None and blocked.cache.length == 0,
                                'logits_accepted': False if failure is not None else True,
                                'transport': abort_report},
              'assumptions': {'weights': 'seeded synthetic float64 model with a zero gate selecting four real expert MLPs',
                              'fault_channel': 'actual serialized expert output vectors through a bounded DATA/ACK reference',
                              'combine': 'one owner-local bypass plus three acknowledged unique remote outputs, weighted exactly once',
                              'timing': 'transport logical ticks are separate from uncalibrated finite-buffer modeled cycles',
                              'failure': 'lost acknowledgments forbid combine even when the receiver executed; no distributed commit claim'}}
    return json.loads(json.dumps(report, allow_nan=False))


def _read_json_config(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate inference configuration key: {key}')
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f'non-finite JSON constant is not allowed: {value}')

    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object,
                      parse_constant=reject_constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        config = None if args.config is None else _read_json_config(args.config)
        report = run_inference_demo(config)
        serialized = json.dumps(report, indent=2, allow_nan=False) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding='utf-8')
    except (OSError, ValueError, TypeError, OverflowError) as error:
        parser.error(str(error))
    sys.stdout.write(serialized)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
