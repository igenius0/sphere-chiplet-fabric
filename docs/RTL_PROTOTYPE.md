# Local word-fabric RTL prototype

Version 1.2.0, 10 November 2026.

The repository implements a board-agnostic, single-clock word switch in
`rtl/word_fabric_top.sv`, backed by `rtl/sync_fifo.sv`. This is an executable
hardware-control prototype for four endpoints. It is separate from the Python
flit/credit network model and does not implement the accelerator, HBM protocol,
chiplet PHY, spherical wiring, CDC, or board integration.

## Interface and cycle contract

The top defaults to `PORTS=4`, `FIFO_DEPTH=4`, 32 payload bits, 16 sequence bits,
and 8 generation bits. Port indices are packed from the least significant slice
upward: endpoint `i` occupies `[i*WIDTH +: WIDTH]`. `PORT_WIDTH` is
`max(1, ceil(log2(PORTS)))`. Supported parameter values are `PORTS >= 2`,
`FIFO_DEPTH >= 1`, and positive payload, sequence and generation widths.
The FIFO likewise requires positive `WIDTH` and `DEPTH`. Derived `PORT_WIDTH`,
`PTR_WIDTH` and `COUNT_WIDTH` must equal their documented/default expressions;
overriding them inconsistently is rejected. Both modules reject unsupported
parameters at elaboration or simulation startup, and the runner exercises eleven invalid
configurations rather than silently treating them as supported hardware.

| Signal | Direction | Meaning |
| --- | --- | --- |
| `clk`, `rst` | Input | One clock; active-high synchronous reset |
| `s_valid`, `s_ready` | Input, output | One ready/valid ingress per endpoint |
| `s_payload` | Input | Packed ingress payload words |
| `s_destination` | Input | Packed output endpoint indices |
| `s_sequence`, `s_generation` | Input | Opaque identity metadata preserved unchanged |
| `m_valid`, `m_ready` | Output, input | One ready/valid egress per endpoint |
| `m_payload` | Output | Accepted payload word |
| `m_source` | Output | Source endpoint index attached by the ingress |
| `m_destination` | Output | Original destination endpoint index |
| `m_sequence`, `m_generation` | Output | Original identity metadata |

A transfer occurs only on a rising edge with `valid && ready`. A producer must
hold its valid word and metadata until acceptance. A stalled output retains both
valid and every identity/data field until acceptance or reset. Invalid destination
encodings have ingress `ready=0` and are never accepted; this matters for a
non-power-of-two endpoint count.

Each ingress uses one first-word-visible synchronous FIFO. Newly accepted words
become visible after their acceptance edge, with no empty bypass. A full FIFO can
accept a replacement on the same edge that removes its old head, including depth
one. Explicit pointer wrap supports non-power-of-two depth. Memory contents need
not be reset because reset clears validity, pointers and occupancy. Reset cancels
all queued and stalled words and resets arbitration priority; it does not promise
delivery of pre-reset words. The surrounding system owns generation assignment.

Each output has round-robin arbitration across eligible ingress heads. A stall
locks the winning ingress so newly arriving contenders cannot change the visible
word. Priority advances only after an accepted output transfer. A continuously
eligible source can be bypassed by at most `PORTS-1` accepted transfers on that
output. Output readiness must eventually recur for progress; a permanently
stalled consumer cannot have a finite cycle service bound. A source with a head
for another stalled output is not eligible, so head-of-line blocking is explicit.
Each ingress head has exactly one destination and can be popped at most once.

Output grants depend on stored heads and arbitration state, not current ingress
valid or downstream ready. Ready qualifies a pop and can enable same-edge full
replacement. There is no internal combinational ready loop. The integration must
also avoid building a loop through external endpoint logic.

## Reproduce actual RTL evidence

Install Icarus Verilog (`iverilog`, `vvp`) and Yosys, then run from the repository
root:

```bash
python scripts/run_rtl_validation.py --output artifacts/rtl_validation.json
python scripts/run_rtl_validation.py --check --output artifacts/rtl_validation.json
python -m unittest discover -s tests -p test_rtl_contract.py -v
```

The first command requires the tools and fails clearly if any are absent. It
executes three self-checking FIFO simulations at depths 1, 3 and 4, and four
self-checking fabric simulations at `(ports, depth) = (4,1), (4,3), (4,4), (3,3)`.
Each fabric simulation includes at least 1,000 contended-output transfers,
periodic and sustained stalls, a new contender arriving behind a stalled winner,
deterministic randomized traffic, full replacement,
FIFO pointer wrap, 16-bit sequence metadata wrap, and reset while full and stalled.
The three-port case rejects the unused destination encoding.

An independent Python model uses deques, abstract owners and round-robin lists.
It generates every expected ready/valid/identity/data cycle. The SystemVerilog
bench compares those cycles against the actual RTL and separately checks output
stability, conservation, reset flush counts, and the contention service bound.
The oracle tracks accepted identity sets and per-source acceptance order across
sequence wrap and generation changes. No dropped, duplicated, stale-generation,
misrouted, or reordered accepted word can pass the tested traces.

The runner also executes generic flattened Yosys synthesis of the four-port,
depth-four default. `check -assert` must pass without conflicting drivers or
combinational loops; inferred latch cells and latch/multidriver warnings fail the
runner. The report records source SHA-256 hashes, exact tool versions, deterministic
trace hashes, transfer/coverage counts and generic netlist cell/state counts.
It omits temporary paths and wall-clock data. Repeated runs with the same tool
versions and sources produce identical JSON. `--check` verifies saved source
provenance, regenerated oracle trace hashes and required coverage without tools;
it also rejects inconsistent cell/state totals, empty tool attribution and missing
parameter-rejection evidence. It does not rerun simulation or synthesis, nor does
it authenticate a report against deliberate forgery.

The ordinary Python suite skips actual-tool tests if tools are absent. Dedicated
RTL CI must install both tools and call the runner, so an absent tool cannot count
as a passing RTL result. The reference-only tests still run without those tools.

## Scope of the evidence

Simulation covers these deterministic finite traces rather than a formal proof
over all input sequences. Generic gate and sequential-cell counts describe this
specific synthesis flow; they are not ASIC area, FPGA LUT usage, maximum clock,
power, energy, or throughput measurements. Memory is mapped into generic logic
for these counts. No FPGA board target, pin constraints, routed design, timing
closure report, bitstream or silicon result is claimed. The result establishes a
small synthesizable local-switch implementation and regression contract, leaving
physical implementation and full accelerator integration as separate work.
