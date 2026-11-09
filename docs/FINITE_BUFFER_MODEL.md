# Finite-buffer flit network

Version 1.2.0 · 2026-11-10

`sim/finite_buffer.py` is an executable, deterministic network prototype with
finite input FIFOs, virtual channels, explicit link flight, delayed credit
return, bounded endpoint rates, and sink backpressure. Its cycle counts are
uncalibrated model results. They are not measured chiplet timing or hardware
performance predictions.

This module complements the repository's analytic timing model. It does not
replace that model's physical assumptions, and it does not implement wormhole
header/body locking, hardware VC allocation, adaptive routing, or RTL.

## API

The module uses the Python standard library only:

```python
from sim.finite_buffer import NetworkConfig, Packet, simulate_network

topology = {"nodes": [0, 1], "adj": {0: [1], 1: [0]}}
report = simulate_network(
    topology,
    [Packet(packet_id=8, source=0, destination=1, flits=2)],
    NetworkConfig(
        buffer_flits_per_vc=1,
        link_latency_cycles=2,
        credit_latency_cycles=3,
        record_trace=True,
    ),
)
assert report["deliveries"][0]["completion_cycle"] == 10
assert report["elapsed_cycles"] == 14
assert report["idle"]
```

`Packet(packet_id, source, destination, flits, release_cycle=0, vc=0)` is an
immutable request. IDs, endpoints, release cycles, and VC indices must be
nonnegative integers; `flits` must be a positive integer. IDs must be unique
within a run. Packet IDs and flit indices are preserved exactly in all ledgers
and trace events. Flit indices start at zero.

`simulate_network(topo, packets, config=None)` accepts a topology mapping, an
iterable of `Packet` objects, and an optional `NetworkConfig`. It returns a
JSON-serializable dictionary with `schema_version: 1`. A topology must have a
nonempty iterable of unique nonnegative integer `nodes` and an `adj` mapping
with exactly those keys. Neighbors must be unique, known, and distinct from the
node itself. Every link must be symmetric. Node labels need not be contiguous.
Other topology fields, including physical lengths, are unused by this model.

Booleans, fractional values, nonfinite numbers, and numeric strings are rejected
for integer fields, including adjacency keys. Unknown endpoints, duplicate
packet IDs, out-of-range VCs, and unknown paused sinks raise `ValueError` before
traffic executes. The simulator copies and normalizes its inputs; it does not
reorder the caller's packet list or edit the topology.

`NetworkConfig` has these fields. All numeric fields require positive integers.

| Field | Default | Meaning |
| --- | ---: | --- |
| `buffer_flits_per_vc` | 4 | Capacity of each physical input port's FIFO for each VC |
| `virtual_channels` | 2 | Independent FIFO/credit classes; packets keep their original VC |
| `link_latency_cycles` | 1 | Cycles from a link send to downstream arrival |
| `credit_latency_cycles` | 1 | Cycles from downstream dequeue to upstream credit availability |
| `max_cycles` | 100000 | Maximum executed cycles, including idle release waits and credit drain |
| `injection_flits_per_cycle` | 1 | Maximum flits accepted from each source endpoint per cycle |
| `ejection_flits_per_cycle` | 1 | Maximum flits consumed by each sink per cycle, across distinct input ports |
| `router_latency_cycles` | 1 | Minimum wait after injection or arrival before arbitration |
| `flit_bytes` | 64 | Payload-size metadata; callers determine packet flit counts |
| `paused_sinks` | `()` | Iterable of `(node, start_cycle, end_cycle)` triples |
| `record_trace` | `False` | Record every injection, send, arrival, ejection, and credit return |

Sink pauses use half-open intervals `[start_cycle, end_cycle)`. `None` as the
end means indefinite pause. An end equal to the start is an empty interval;
overlapping intervals have their ordinary union effect. Pausing a sink blocks
its ejection, while transit forwarding at that node remains eligible.

## Buffers, arbitration, and cycle order

Each router has one local endpoint input and one input for each physical
neighbor. Every input has a separate FIFO of capacity `buffer_flits_per_vc` for
each VC. Only a FIFO's head can request an output, so head-of-line blocking is
explicit. Released requests that have not entered the local input remain in
the finite traffic inventory and are reported as uninjected; this inventory is
not a modeled hardware command FIFO.

Each cycle executes these steps:

1. Apply due credit-return events, then append due link arrivals to their
   reserved downstream input FIFOs.
2. Arbitrate ready FIFO heads. Each directed physical link sends at most one
   flit per cycle across all VCs. Opposite directions are independent. Each
   physical input port supplies at most one flit per cycle across its VCs,
   including ejection. The sink also obeys its configured ejection rate.
3. Inject eligible endpoint flits, up to each source's configured rate, into
   available local input space.

An injected flit at cycle `t` is ready at `t + router_latency_cycles`. A link
send at `t` arrives at `t + link_latency_cycles`, and becomes ready another
`router_latency_cycles` later. Positive latencies prevent same-cycle forwarding
through another router. A high ejection rate cannot multiply the bandwidth of
one physical input; it can consume flits from multiple inputs.

Output priority rotates every cycle. Each output round-robins over input/VC
FIFOs after successful grants; endpoint injection round-robins over eligible
packets at that source. Blocked contenders do not prevent consideration of an
eligible contender. The deterministic greedy matching does not seek a maximum
matching. Fairness tests cover persistent input, VC, and injection contention;
these tests are not a universal hardware arbitration proof or a latency bound
under arbitrary sink pauses.

## Credit and flit conservation

A directed link/VC starts with one credit per downstream FIFO slot. Sending a
flit reserves a slot immediately, before the flit arrives. Dequeuing a flit from
a network input frees that slot locally and schedules a return credit at
`dequeue_cycle + credit_latency_cycles`. The sender can reuse the slot only
when that event is applied. Local endpoint inputs have finite capacity but no
remote credit-return channel.

For every directed physical link and VC, the model checks:

```text
available + downstream_resident + link_inflight + return_pending = capacity
```

Every term is nonnegative and `available <= capacity`. Event-heap counts are
checked independently against reservation counters. A flit must be exactly
once in the buffered, in-flight, or delivered set after injection. At run end:

```text
requested_flits = delivered_flits + buffered_flits + inflight_flits
                  + uninjected_flits + rejected_flits
```

The model checks these invariants after every cycle and at termination. It
does not silently drop, overwrite, or reinject blocked flits. `stalls` contains
counts of arbitration attempts that encountered unavailable credits, an
already-used input, a paused sink, or a full injection FIFO. These are event
counters, not mutually exclusive flit counts or calibrated utilization values.

## Completion, cutoff, and result fields

Cycles are zero-indexed. `completion_cycle` is the cycle of a packet's final
ejection; `latency_cycles` is that cycle minus `release_cycle`. `elapsed_cycles`
counts all executed cycles, including the final cycle and credit drain. The
run completes only after every accepted flit is delivered and all FIFO, link,
and credit-return state is idle.

For a single uncontended one-hop flit released at zero with default latency
values, the exact timeline is:

| Cycle | Event |
| ---: | --- |
| 0 | Endpoint injects into its local FIFO |
| 1 | Source sends and reserves downstream space |
| 2 | Flit arrives downstream |
| 3 | Sink ejects and schedules a credit return |
| 4 | Source receives its returned credit; run completes |

Its packet completion is cycle 3 and its elapsed run length is 5 cycles.
With router wait `R` and link latency `L`, a single one-hop flit's ejection is
`release + 2R + L`; two hops take `release + 3R + 2L`. The final credit returns
`credit_latency_cycles` after the final ejection.

| Result field | Meaning |
| --- | --- |
| `status` | `completed` only when accepted traffic and credit returns are idle; otherwise `max_cycles` |
| `idle` | True only with all accepted packets delivered, empty FIFOs/links, and all credits restored |
| `cutoff_reason` | `None` on idle completion, else `undelivered_packets` or `pending_credit_returns` |
| `all_requested_delivered` | True only when every requested packet was delivered, with no rejected or pending packet |
| `deliveries` | Completed packet records, ordered by completion cycle then original packet ID |
| `pending_packets` | Accepted but incomplete packets, with injected/delivered/uninjected counts and reason |
| `rejected_packets` | Cross-component requests with original identities and `unreachable_in_fixed_graph` reason |
| `packet_routes` | Fixed routes for accepted packets, ordered by packet ID |
| `accounting` | Packet/flit totals, residency categories, and `conservation_ok` |
| `occupancies` | Per-input/VC capacity, current and maximum occupancy, and ordered resident flit records |
| `credits` | Per-link/VC capacity, available/minimum credits, downstream residency, in-flight and return-pending counts |
| `inflight` / `credit_returns` | All outstanding scheduled events at cutoff |
| `trace` | Optional ordered event stream, empty when recording is disabled |
| `routing_certificate` | Spanning-forest/channel-dependency evidence described below |

The executed window is cycles `0` through `max_cycles - 1`. A request released
at `max_cycles` has not entered the network at cutoff. A cutoff after the last
packet delivery but before the final credit return has `all_requested_delivered`
true, `pending_packets` empty, `status` equal to `max_cycles`, `idle` false, and
`cutoff_reason` equal to `pending_credit_returns`. The pending return events
remain explicit. No resumable simulator state is returned; a report is an
inspection ledger, and another invocation starts a new run.

Empty traffic is idle with zero elapsed cycles. Cross-component requests are
rejected before injection and counted separately. A run with only rejected
requests is likewise idle at cycle zero, with `all_requested_delivered` false.
Consumers that require every original request must check that flag and exact
delivery IDs, in addition to `status` and `idle`.

## Fixed routing and dependency certificate

`routing_dependency_report(topo)` returns the same certificate as a simulation
without executing traffic. The graph is normalized by node label and neighbor
label. The smallest unvisited node roots each component's deterministic
breadth-first spanning tree. Each accepted route goes up to the least common
ancestor and then down to the destination, using only that tree. A self route
is the single endpoint. Non-tree physical links have buffers/credits but carry
no traffic under this routing policy.

The certificate reports component membership, tree edges, physical/tree link
counts, every directed tree channel, and every consecutive-channel dependency
from every ordered nonlocal route. If `H` is the forest's maximum depth and a
tree edge joins a child at depth `d` to its parent, the assigned ranks are:

- Child to parent: `H - d`.
- Parent to child: `H + d`.

Ranks strictly increase on every listed dependency: upward steps increase as
depth falls, the up-to-down turn increases, and downward steps increase as
depth rises. The returned channel ordering is therefore a checkable
topological order. Each VC has this same dependency graph and packets never
change VC.

This certifies acyclicity of the **fixed routing channel-dependency graph**. It
is not a formal hardware deadlock proof, an adaptive-routing/VC-transition
proof, or a liveness guarantee when sinks stop consuming. It does not establish
packet/VC allocation correctness for a future wormhole router.

## Validation and remaining limits

Run the focused suite with:

```sh
python -m unittest tests.test_finite_buffer -v
```

The suite contains hand-derived local, one-hop, two-hop, full-duplex, credit
round-trip, endpoint-rate, sink-pause, and cutoff timelines. An independent
trace replay checks each actual FIFO operation, one grant per physical input,
link and endpoint bandwidth, exact flit identities, and per-event credit/flit
conservation. A separate graph traversal reconstructs all routes from the
reported forest and verifies the CDG with Kahn's algorithm rather than trusting
the reported rank test. All 75 simple undirected graphs with one through four
nodes exercise every ordered endpoint pair, including disconnected graphs.
Fixed random seeds 0–9 stress multiple VCs, unequal latencies/rates, finite sink
pauses, and incomplete cutoffs; separate integration tests reserve seeds
100–109 for held-out adversarial validation.

The model has no calibrated clock period, physical link bandwidth variation,
errors/retries, packet retransmission, protocol queues, hardware allocator,
HBM contention, power, thermal behavior, or silicon measurement. It stores
per-flit route metadata and complete injected/delivered identity sets; optional
trace recording and enumeration of all fixed routes are intended for small
prototype validation rather than large-scale simulation efficiency.
