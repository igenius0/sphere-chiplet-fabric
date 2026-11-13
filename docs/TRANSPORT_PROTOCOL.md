# Bounded DATA/ACK transport reference

Prototype release 1.2.0 — 2026-11-10.

`sim/transport.py` implements a deterministic Python protocol reference with
actual immutable byte payloads. It exercises accidental corruption, lost DATA
or ACKs, duplicates, reordering, bounded retries, and generation changes. Its
logical ticks have no calibrated relationship to the finite-buffer network
model, a PHY, or hardware throughput. It is not router RTL, a distributed
failure detector, authenticated messaging, or durable recovery after restart.

## Identity, payload and admission

An admitted contribution is identified by
`(request_id, generation, sequence, source, destination)`. A request has one
source, one destination, and a finite collection of unique sequence numbers.
Sequence numbers need not be contiguous. DATA carries the exact bytes submitted
for that sequence; an ACK has an empty payload and reverses the source and
destination. `request_id` is a nonempty string of at most 128 UTF-8 bytes without
NUL. Generation, sequence, endpoints and CRC32 are unsigned 32-bit integers;
booleans are rejected as integers.

CRC32 covers an unambiguous JSON array
`[kind, request_id, generation, sequence, source, destination]`, encoded as
compact ASCII JSON, followed by a NUL byte and the payload. The fixed reference
vector `DATA, "r", 3, 7, 2, 9, b"abc"` has CRC32 `0x5ce4d688`. This detects the
accidental bit changes exercised here. CRC collisions remain possible and a
sender can recompute CRC32; there is no authentication or adversarial integrity
claim. The fault injector's `corrupt` rule flips one CRC bit while preserving
the rest of the frame.

Admission validates the entire request and all initial event plans before
changing request records, reservations, counters, or the event queue. Invalid
payload types, oversized payloads, duplicate sequences, invalid identifiers,
wrong generations and exhausted capacities raise `ValueError`. Payloads must be
immutable `bytes`; a mutable `bytearray` is not accepted. The contribution
iterator is stopped at the configured maximum plus one item, so an infinite
iterator producing valid pairs cannot silently grow admission state. Arbitrary
blocking user iterator code is outside the scheduler model.

The receiver consults the same admitted request record as the sender. It checks
CRC before generation and identity, then checks the exact expected bytes. This
shared admission oracle is an explicit assumption, not a demonstrated method
for distributing admission state across hardware endpoints.

## Receiver acceptance and sender completion

The first valid DATA for a live contribution stores its payload and increments
the receiver execution counter. Another copy of that contribution does not
store or execute it again; it emits another ACK to recover ACK loss. A caller
obtains the unique accepted payloads through `delivered_payloads()`, sorted by
sequence, and can combine each once. The counter denotes acceptance in this
reference; it does not prove atomic execution of an arbitrary external side
effect.

An ACK is accepted only when its complete identity matches the request and the
receiver has actually accepted that sequence. CRC failures, unknown requests,
stale or future generations, foreign endpoints, unknown sequences, conflicting
payloads, and ACKs preceding receiver acceptance are discarded and counted.
A foreign CRC-valid ACK does not abort a valid request. ACKs carry no attempt
number: a delayed genuine ACK from an earlier retry can acknowledge the same
already accepted contribution. This provenance check relies on the shared
receiver record and is not a security mechanism.

Every sequence starts with attempt 0. `max_retries` counts additional attempts,
so there are at most `1 + max_retries` DATA transmissions per sequence. An
unacknowledged sequence is retried at each `timeout_ticks` interval. Frames win
exact ties against timeouts; equally timed frames are processed in insertion
order. A request completes when every sequence is acknowledged. The final
unacknowledged timeout aborts the entire request, including its other pending
sequences. Late DATA cannot execute after abort, and late ACKs cannot change a
terminal result.

For submission tick `s`, timeout `T`, and retry limit `R`, the terminal deadline
is `s + (R + 1) * T`. Event-capacity exhaustion, a generation change, or logical
tick exhaustion can abort earlier. Choosing `T` shorter than the DATA/ACK round
trip can legitimately produce an uncertain abort even on an otherwise healthy
link. The implementation makes no adaptive timeout or asynchronous liveness
claim.

The externally visible state is:

| Status | Sender outcome | Reservation | Meaning |
| --- | --- | --- | --- |
| `pending` | `pending` | Held | Some contributions lack accepted ACKs. |
| `completed` | `acknowledged` | Released | Every contribution was accepted once and acknowledged. |
| `aborted` | `uncertain` | Released | The sender cannot verify every contribution; some or all may have executed. |

`uncertain_delivery` is true for an aborted request with unacknowledged
contributions. It describes the sender's knowledge. For example, a DATA
partition has zero receiver executions in the shared observation, while all
ACKs lost has every receiver execution and no sender confirmation. Both are
uncertain sender outcomes. Retrying an uncertain application operation under a
new identity can repeat its effect; no cross-request transaction or rollback is
implemented. A partial receiver result is not promoted to a verified sender
result.

## State bounds and generation barrier

| Configuration | Default | Bound or role |
| --- | ---: | --- |
| `timeout_ticks` | 4 | Positive logical ticks between attempts. |
| `max_retries` | 2 | Additional DATA attempts, at most 16 configurable. |
| `max_inflight` | 16 | Simultaneously pending requests. |
| `max_requests` | 64 | Total admitted requests over the object's lifetime, across generations. |
| `max_contributions` | 128 | Sequences per request. |
| `max_payload_bytes` | 4096 | Bytes per contribution or injected frame. |
| `max_reserved_bytes` | 1,048,576 | Sum of caller-declared reservations held by pending requests. |
| `link_delay_ticks` | 1 | Base one-way delay for every emitted DATA or ACK. |
| `max_queued_events` | 65,536 | Pending frame arrivals plus timeout events. |
| `max_wire_copies` | 16 | Conservative maximum copies from the configured duplicate rules. |

All terminal request records and deduplication records are retained. There is
no eviction, including of unacknowledged or completed contributions. A request
name cannot be reused within its generation, even after termination. Exhausting
`max_requests` rejects a new request and requires a new session; advancing a
generation does not reset this lifetime bound. Expected and accepted payload
state is bounded by request count, sequence count and payload size. Reservations
are logical declarations, not measured allocation or Python process memory;
payload records, interpreter overhead and queued frame objects are separate.

`advance_generation(new_generation)` requires a strictly larger unsigned
32-bit generation. It aborts every pending old request once and releases its
reservation before new-generation admission. A request name may then be reused,
but old DATA and ACKs cannot match the new generation. Already queued old frames
remain bounded in-flight events and drain as stale arrivals; the barrier does
not claim to flush a physical network. Old timeout events become inert. These
in-flight entries can conservatively consume event capacity until drained.

Reservation release occurs exactly once on any terminal transition, including
retry exhaustion, event-capacity exhaustion and tick exhaustion. Initial
capacity or timestamp failures reject admission without a record. A valid
request can still encounter an event-capacity limit on an ACK or retry; this is
an explicit `event_capacity_exhausted` abort. Scheduling beyond the signed
63-bit logical tick limit produces `event_tick_exhausted` and releases the
reservation, including when the receiver already accepted DATA.

The model is a live, finite session. Discarding the object discards replay
history. Safe replacement with a new session, durable deduplication, persistent
generations, endpoint crashes, atomic application commit, and reconciliation of
an uncertain result require mechanisms outside this prototype.

## Deterministic fault injection

`Fault` selectors can match kind (`DATA` or `ACK`), request name, sequence,
generation, and attempt. Omitted selectors match all corresponding
transmissions. Rules are reusable and applied in order on each transmission;
ACKs inherit the attempt index of the DATA that caused them.

| Action | Behavior |
| --- | --- |
| `drop`, `partition` | Remove all selected copies; an unqualified partition persists through every retry. |
| `corrupt` | Flip one CRC bit in each selected copy. |
| `duplicate` | Add `copies` extra frames, optionally staggered by `delay_ticks`. |
| `delay` | Add positive logical ticks to selected copies. |
| `reorder` | Use the same delay mechanism to move an earlier frame after a later one. |

Extra copies also pass through later rules. The product of every duplicate
rule's copy factor must fit `max_wire_copies`; this check is conservative even
for nonoverlapping selectors. At most 32 rules are accepted. This test harness
does not inject bit errors into a serialized PHY packet, model retransmission
bandwidth on the finite-buffer router, or simulate a failed endpoint's recovery.
`inject_frame()` is an explicit test hook for already-on-wire frames; it validates
fields and capacity before enqueueing and does not reapply fault rules.

## Reproduction and evidence

Run the standalone demonstration and focused tests from the repository root:

```sh
python -m sim.transport --output artifacts/transport-reference.json
python -m unittest discover -s tests -p test_transport.py -v
```

The importable `demonstrate_transport(config=None)` and
`run_transport_demo(config=None)` return JSON-compatible reports. Optional
configuration is a `TransportConfig` or a strict dictionary of its named fields.
Per-request reports include expected, acknowledged and unacknowledged
sequences; attempt counts; accepted payload hex; receiver execution count;
submission, deadline and terminal ticks; reservation status; and sender outcome.
Every report accounts for `submitted = completed + aborted + pending`.
`run()` drains late events as well as live work, so the final report tick can
exceed a request's terminal deadline. `run(max_ticks=N)` can intentionally leave
requests pending and reservations held.

The default demo transmits the actual network-order float64 values
`(1.25, -2.0, 8.5)` and combines the unique receiver payloads to `7.75`:

| Scenario | Final request accounting | Receiver sum | Sender-verified result |
| --- | --- | ---: | ---: |
| `healthy` | One completed | 7.75 | 7.75 |
| `recover_drop_corrupt_duplicate_reorder` | One completed after bounded retry | 7.75 | 7.75 |
| `partition` | One aborted, no pending requests | 0.0 | `null` |
| `all_acks_lost` | One aborted with uncertain outcome | 7.75 | `null` |
| `generation_barrier` | Old request aborted; reused name in new generation completed | New payload is 3.5; old payload is never accepted | Per-request ledger |

The focused suite includes hand-derived DATA/ACK timelines and timeout ties;
CRC coverage of identity and payload; malformed admissions without partial
mutation; foreign, stale and premature ACKs; request-name reuse; late frames
after abort; bounded partitions; queue and tick exhaustion; generation aborts;
and reservation/terminal accounting. Held-out seeds 100–109 generate exact
float64 payload bytes against fixed drop, corruption, duplicate, delay,
reordering and ACK-loss rules. The assertions compare every sequence and byte,
not only an aggregate checksum, and separately show that lost ACKs never cause
a receiver sum to include the same contribution twice.

These are executable software reference checks. They establish neither a
hardware implementation nor security, a durable exactly-once service, physical
fault recovery, calibrated latency, or performance under a real interconnect.
