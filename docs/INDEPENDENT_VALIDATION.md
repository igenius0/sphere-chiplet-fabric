# Independent integration validation

Revision **v1.2.0, 10 November 2026**. The checks in
[`tests/test_prototype_integration.py`](../tests/test_prototype_integration.py)
exercise the public interfaces of the finite-buffer network, numerical decoder
and reliable transport. They use explicit event replay, hand-derived timings,
exhaustive small-graph enumeration and a separate scalar numerical oracle.
They do not import private simulator scheduling or normalization helpers.

Run the integration checks from the repository root:

```sh
python -m unittest discover -s tests -p test_prototype_integration.py -v
```

This review executed **17 integration tests, all passing**. It also reran the
focused numerical decoder, finite-buffer network and transport suites: **23,
24 and 32 tests respectively, all passing**. These counts describe the checked
revision; rerun the commands after any source change.

## Cases checked

| Boundary | Independent check | What a passing result establishes |
|---|---|---|
| Flits and credits | Replay held-out traffic seeds 100–109 from public injection, send, arrival, ejection and credit events; reconstruct FIFO readiness, physical-input grants, link service, endpoint rates, every flit's residence and every reserved receiver slot | The checked executions preserve unique flits, finite storage and the credit identity `available + resident + inflight + return_pending = capacity`; the reported final ledgers match the replay |
| Timing and quiescence | Enumerate router/link/credit timing combinations for a one-slot link, check a large credit window against one flit/cycle, and check the exact cutoff between last delivery and final credit return | Delivered packets and idle resources are distinct; elapsed model cycles include the final credit drain |
| Failure accounting | Permanently pause one sink while another packet completes locally and an unreachable request is rejected | The checked cutoff reports buffered, in-flight, uninjected, delivered and rejected flits without silently losing a request |
| Fixed-route dependency certificate | Enumerate all 64 undirected graphs on four non-contiguously numbered nodes; independently enumerate the forest's unique paths, derive every successive-channel dependency and run Kahn's DAG check | The reported dependencies are complete and acyclic for these fixed spanning-forest routes, including disconnected graphs |
| Cached numerical decoder | Use model/traffic seeds 100–109, several widths, expert counts and top-k values; evaluate each causal prefix through scalar attention and independently selected dense all-expert outputs | Cached logits and the production vectorized reference agree with the scalar definition within `rtol=atol=1e-10` on the checked synthetic float64 models |
| Ownership, bytes and scheduling | Independently count resident model/cache/workspace elements, local versus remote payloads, rounded flits, KV reads/writes, expert weight reads, per-island serialization and unique contribution identities | Logical ownership and the reported byte/schedule ledgers match actual array sizes and selected experts for the checked models |
| Cross-phase resource barriers | Run a real decoder with a slower public network configuration; compare its dispatch and combine barriers with last delivery plus the hand-derived final credit-return delay | Expert compute and the completed token do not reuse a phase whose modeled credits remain outstanding |
| Actual contribution transport | Serialize real expert output vectors, recover them under DATA/ACK faults, compare their values with the scalar oracle and compare a cached multi-token decoder's final logits | The optional decoder transport path combines one byte-correct acknowledged output per selected remote expert in the checked executions |
| Ambiguous ACK loss | Lose every ACK for the second token after a valid first token; inspect receiver execution, sender abort, resources and the committed cache/logits | Receiver execution is not treated as sender success; failed work neither commits part of a token nor changes the previously committed prefix |
| Transport admission limits | Use one allowed in-flight request for a token with three remote experts, then exhaust a one-request finite session during a token | Sequential remote admission completes with bounded capacity; a later admission failure leaves no live reservation/event or partial cache commit, although an earlier remote execution may already have completed |
| Protocol combinations | Exercise all 16 pairs of first-attempt DATA and ACK drop/corrupt/duplicate/reorder faults, CRC identity coverage, premature ACK rejection and delayed old-generation traffic during request-name reuse | Exact payloads and unique receiver execution are preserved in these bounded scenarios; old generations and unproven ACKs cannot complete new work |

The independent admission-limit case found a cross-layer defect during this
review: batching remote submissions could raise after the first admission and
leave live reserved work behind. The decoder now drains and validates each
remote request before admitting the next, while keeping numerical combine and
cache commit behind the all-contribution barrier. The regression checks both
successful operation with one allowed in-flight request and explicit failure
after the finite session's request capacity is exhausted.

## Evidence boundaries

These are finite executable checks, not a formal proof or physical signoff.
The dependency argument applies to the fixed tree routing policy with unchanged
VCs; it does not establish adaptive routing, wormhole allocation, arbitrary VC
transitions, dynamic failure rerouting or liveness with an indefinitely paused
sink. No BookSim cross-check is claimed.

The numerical workload is a tiny seeded synthetic model in one CPU process.
Logical islands do not establish physical memory isolation, parallel accelerator
execution, trained-model quality or useful hardware throughput. Tensor ledgers
exclude Python/NumPy runtime overhead and temporary peak allocations. Protocol
ticks are a separate correctness domain from the uncalibrated network/compute
cycles. The transport reference is a finite live-session oracle with global
admission and generation knowledge; it does not establish crash durability,
authentication or distributed atomic commit.

RTL simulation/generic synthesis and measurement-assessment results are recorded
separately by their respective validation runners. These integration tests do
not establish that the word-switch RTL implements the Python multi-hop network
or protocol. No FPGA board, bitstream, timing closure, power, temperature, BER,
cooling or silicon result is inferred from a passing software test. Missing
physical data must remain `INSUFFICIENT_EVIDENCE`.

The [prototype manifest](../artifacts/prototype_manifest.json) identifies the
tested source bytes and generated report bytes. Its test-source hashes identify
files; they are not evidence that the aggregate experiment runner executed the
unit tests. Run the command above or the full test suite to check those files.
