# Tiny causal MoE inference prototype

Version 1.2.0 · 2026-11-10

`sim/moe_inference.py` executes a complete, small causal decoder using actual
NumPy float64 arithmetic. It evaluates token and position embeddings,
multi-head causal attention with a KV cache, normalization, stable top-k expert
routing, expert MLPs, weighted combination, vocabulary logits and greedy token
generation. A separate assumed cycle schedule couples token dependencies to the
finite-buffer network. Synthetic weights provide a reproducible correctness
workload; they have no pretrained language quality.

## Run and inspect

From the repository root:

```bash
python -m sim.moe_inference --config examples/moe_inference.json --output /tmp/moe-inference.json
python sim/moe_inference.py --config examples/moe_inference.json
python -m unittest discover -s tests -p 'test_moe_inference.py' -v
```

The module and direct-script commands emit the same strict JSON report. Import
performs no inference or file writes. The CLI rejects unknown fields, duplicate
JSON keys, nonstandard NaN/Infinity constants, nonfinite values, booleans in
integer fields, invalid tokens and capacity violations. A scalar tensor capacity
applies to every island; four integers set distinct island budgets.

The Python entry points are:

```python
from sim.moe_inference import (
    InferenceConfig, IslandDecoder, create_weights, reference_logits,
    run_inference_demo, run_inference_fault_demo,
)

config = InferenceConfig()
weights = create_weights(config)
decoder = IslandDecoder(weights, config)
prefill_logits = decoder.prefill(config.prompt_tokens)
next_token_logits = decoder.step(3)
full_prefix_logits = reference_logits(weights, [*config.prompt_tokens, 3], config)
report = run_inference_demo(config)  # Also accepts a configuration dictionary.
fault_report = run_inference_fault_demo()  # Fixed four-expert, one-token experiment.
```

`prefill` returns one vocabulary-logit vector per input token; `step` returns
one vector. Greedy generation chooses the lowest vocabulary index on an exact
logit tie. Every generated token is fed through the decoder, including the last,
so a demonstration processes `len(prompt_tokens) + decode_tokens` tokens and
returns logits for that complete sequence. That total must fit `max_seq_len`.
Zero generated tokens and an exactly full cache are supported. Empty prompts
and stepping beyond the cache capacity are rejected.

## Numerical model and reference

The model has one pre-normalized attention block, one pre-normalized MoE block
and a final normalization/head. Normalization centers each token and divides by
the square root of its population variance plus `layer_norm_epsilon`, then
applies a learned-scale-shaped synthetic vector. There are no bias or shift
parameters. Attention divides query/key scores by the square root of head size
and uses a numerically stable causal softmax. Keys and values are projected once
per committed token and retained in a bounded cache.

Expert scores select the largest `top_k` values. Stable sorting resolves equal
scores in ascending expert index. A stable softmax over the selected scores
produces weights summing to one. Each selected expert computes
`ReLU(normalized_state @ expert_w1) @ expert_w2`; the weighted sum is added to the
attention residual before final normalization and vocabulary projection.

`reference_logits` independently evaluates the entire prefix with vectorized
causal attention, full sequence-by-expert intermediate tensors and a routing
mask. It shares validated model arrays and configuration with the cached path;
it never uses the decoder, its KV cache, its island schedule or its numerical
helpers. The demo compares every prefill logit and the final logit vector after
each generated token, using absolute and relative tolerances of `1e-10`, and
checks that greedy generation agrees. Finite input arrays whose arithmetic
overflows are rejected rather than reported as valid inference.

## Logical placement and dependency schedule

All arithmetic runs in one CPU process. Expert views have explicit logical
owners, and the compute path accesses only the selected owner's expert views.
The fixed tree has links `0–1`, `0–2`, `2–3`.

| Island | Resident tensors and role | Experts with eight configured experts |
| --- | --- | --- |
| 0 | Embeddings, attention/gate/head weights, normalization scales, full KV cache, attention and combine workspaces | 0, 4 |
| 1 | Owned expert weights and expert workspace | 1, 5 |
| 2 | Owned expert weights and expert workspace | 2, 6 |
| 3 | Owned expert weights and expert workspace | 3, 7 |

For any expert count, expert `e` belongs to island `e % 4`.

1. The token's embedding, cached attention and gate execute before dispatch.
2. Every remote selected expert receives a complete activation-vector packet.
   Actual vector bytes are staged on the host and become available to its owner
   after the finite-buffer simulation validates the required packet identity.
   Owner-0 experts bypass the network.
3. Dispatch and its credit returns drain completely. Experts on the same island
   execute serially in routing order; different islands overlap in the assumed
   cycle schedule. The CPU itself evaluates these experts serially.
4. Each remote output packet is released only after that expert's modeled
   compute end. Actual output bytes are reconstructed at the combine owner only
   after every required packet completes. Local outputs use the local bypass.
5. Each `(generation, token_position, expert)` output is weighted and combined
   exactly once. Final normalization/head wait for all contributions and network
   credit drain. The next token starts after that token finishes.

Every network phase checks unique delivery identities, source/destination,
release/completion cycles and flit counts. Completion also requires no pending
or rejected packets, conserved/injected/delivered flits, empty buffers and
in-flight queues, and fully restored credits. A cache update commits only after
the token's numeric, packet and optional reliable-transport checks pass.

The bounded flit network uses fixed tree routes, explicit credits and
backpressure. Its default link/router/credit latencies are each one cycle. It
does not implement packet headers, wormhole allocation, PHYs or adaptive
routing. Compute cycle costs are configurable operation-count proxies divided
by `compute_ops_per_cycle`, rounded up. Traces expose the assumed operation
counts and phase boundaries. These cycles are uncalibrated assumptions, with no
claim about measured CPU time, accelerator throughput, power or ASIC feasibility.

## Tensor capacity and byte accounting

`execution.memory.array_inventory` names every resident weight view, full KV
array and allocated reserved workspace, with owner, shape, dtype, element count,
item size and actual NumPy `nbytes`. Expert views partition the host expert
arrays and are counted once. The per-island category totals and overall total
equal that inventory. Construction rejects an island whose named tensor budget
exceeds its configured capacity; equality is admitted.

This is a logical budget for those named arrays. It does not allocate separate
physical island memories or bound complete process peak/RSS. Python objects,
NumPy/BLAS temporaries, staged payload byte strings, network/protocol state,
reference computation and host model/input copies are excluded explicitly.

`execution.bytes` aggregates the per-token `bytes` ledgers:

| Key | Meaning |
| --- | --- |
| `dispatch_remote_payload`, `combine_remote_payload` | Actual serialized remote input/output vector bytes, excluding local bypass |
| `dispatch_remote_flit_bytes`, `combine_remote_flit_bytes` | Endpoint payload rounded to whole flits |
| `dispatch_remote_padding`, `combine_remote_padding` | Difference between rounded flit bytes and payload |
| `dispatch_link_flit_bytes`, `combine_link_flit_bytes` | Rounded flit bytes multiplied by each packet's tree hop count |
| `dispatch_local_bypass`, `combine_local_bypass` | Vector bytes handled locally without packets |
| `expert_local_weight_reads_by_island` | Selected experts' actual two-matrix `nbytes`, charged to their owner |
| `kv_local_read`, `kv_local_write` | Prior-prefix keys/values read and the new keys/values written on island 0 |
| `by_island` | Separate send/receive payload, local bypass, local expert-read and KV counters for every owner |

All counters are integers derived from array item sizes and actual vector
lengths. Payload, endpoint flit bytes and hop-weighted link bytes are distinct
quantities. The ledger does not count complete CPU memory traffic, metadata,
control flits, SRAM implementation overhead or retransmission bytes.

## Actual contribution fault recovery

`IslandDecoder(..., contribution_transport=ReliableTransport(...))` optionally
passes every actual remote expert output through the bounded DATA/ACK reference
before numerical combination. Each expert has a distinct request identity;
only a completed, acknowledged request with one byte-exact receiver payload may
be combined. The transport generation must match the decoder generation.

`run_inference_fault_demo()` selects all four experts with equal gate weights
for one fixed token. It drops the first DATA frame for one expert and that
expert's first retry ACK, corrupts another expert's first DATA frame, and
duplicates a third expert's first DATA frame. Retries restore three remote
outputs; receiver duplicate suppression preserves one execution per output.
Those three real vectors and one local bypass produce the same final logits as
the independent full-prefix reference.

A second run loses every ACK. Receivers can execute the remote outputs while
senders eventually abort with an uncertain outcome. The decoder refuses to
combine or commit its cache, and all transport reservations are released. This
demonstrates the distinction between unique receiver execution and acknowledged
completion; it does not claim a general distributed transaction protocol.
After a transport failure, retained request identities require a fresh session
or an appropriately coordinated new generation before resubmission.

Transport logical ticks remain separate from finite-buffer modeled cycles.
The correctness harness does not invent a mapping between retransmissions,
clock domains and hardware timing. CRC32 detects modeled accidental corruption
and provides no authentication.

The tests cover held seeds 100–109, cached/reference logits and greedy tokens,
causality, exact KV contents, tie handling, prompt/generation/cache boundaries,
invalid configuration/arrays, per-island capacity and byte conservation,
dependency ordering, rejected/duplicate/missing packet reports, local bypass,
actual contribution recovery, ambiguous ACK loss and both CLI/import paths.
