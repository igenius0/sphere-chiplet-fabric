# Independent adversarial review

This review checks whether the implementation can falsify its own results. It
does not establish hardware performance or prove that a topology is optimal.

## Findings and required resolutions

| Finding | Reproduction | Required behaviour |
| --- | --- | --- |
| Finite inputs could create non-finite derived timing | `LatencyModel(link_rate_gbps=1e308)` produced zero serialization; `wire_speed_mm_per_ns=1e-320` produced infinite propagation | Reject an unrepresentable model or produce finite, positive service intervals |
| Large absolute timestamps could erase positive latency | A default four-flit packet injected at `1e17` ns over a 4 mm link returned `0` ns instead of approximately `10.026667` ns | Preserve physical latency or reject inadequate numeric resolution explicitly |
| A very large packet could be counted as a delivery with infinite latency | `send_batch([(0, 1, 10**308, 0)])` on a one-link graph | Reject unrepresentable service time; never report an infinite-latency successful measurement |

These are numeric correctness defects, rather than evidence for a particular
fabric. The regression tests accept an explicit `ValueError` for an
unrepresentable simulation. Ordinary engineering configurations must continue
to run. All three findings are resolved in this implementation: derived
numbers and service durations are checked, and timestamp advances reject
overflow or inadequate resolution. The adversarial regression suite covers
these failures. The table records the original failures so they remain
reproducible. Run the suite and inspect the exact commit's Python 3.10/3.12
GitHub Actions results when validating a revision; an earlier passing run does
not certify later changes.

## Independent invariants

`tests/test_adversarial.py` checks:

- Shuffling a batch with distinct event-arrival times preserves each packet's
  result on both torus and icosphere graphs. This detects submission-order
  effects without assuming a preferred topology.
- A saturated stream cannot transmit more than its directed link's capacity.
  The last completion must include all serialized bits exactly once.
- Opposing saturated streams use independent full-duplex capacity.
- Queueing cannot beat the sum of a route's router, serialization and
  propagation delays.
- Invalid batch prevalidation does not leave partial reservations, and an
  unreachable destination does not consume unrelated links.
- Derived rates, service times and output latencies remain finite and
  physically meaningful, or fail explicitly.

The fixed seeds make failures repeatable. Small analytic graphs supply timing
oracles; graph comparisons do not hard-code a scientific winner.

## Interpretation limits that remain

1. Infinite output queues do not test finite-buffer deadlock, credit
   starvation, head-of-line blocking, packet drops or VC arbitration. The
   `vcs` argument is metadata in this model.
2. `send_packet` retains previously submitted reservations. Concurrent
   experiments must use `send_batch` or the batched traffic runners.
3. Delivered-only percentiles can improve when a failure removes difficult
   traffic. Review latency together with delivered and unreachable counts.
4. Equal aggregate link bandwidth does not equalize physical wire length,
   port area, signal integrity, energy or cooling. The resource report exposes
   these differences, but does not price them.
5. The periodic stencil maps directly onto torus edges. That experiment tests
   this placement; it cannot decide all unstructured-grid HPC applications.
6. Aggregate HBM capacity and local stack bandwidth do not imply uniform
   remotely accessible bandwidth. The endpoint ceiling in the reference
   model remains an optimistic bound before contention and cuts.
7. A paper or archived figure does not inherit new validation merely because
   the code has been corrected. The current paper and figures are regenerated
   with explicit model/configuration attribution and source/output hashes in
   `artifacts/publication_manifest.json`. The original archive retains its
   original evidence status; see [publication revision](PUBLICATION_REVISION.md).

Run the focused review with:

```bash
python -m unittest discover -s tests -p test_adversarial.py -v
```
