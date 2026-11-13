# Implementation and evidence status

Revision **v1.2.0, 10 November 2026**. This revision moves several design gaps into executable reference implementations. The artifact inventory and remaining gates below distinguish numerical execution, logical simulation, RTL simulation/synthesis and physical measurements.

| Area | Implemented in this revision | Evidence and remaining boundary |
|---|---|---|
| Finite buffers and credits | Bounded input/VC flit queues, delayed credits, finite injection/ejection, fair arbitration, sink stalls and explicit cutoff accounting | [Model](FINITE_BUFFER_MODEL.md); independent flit/credit replay and hand-derived timing. Routes use a fixed spanning forest; no adaptive/wormhole VC allocation or calibrated PHY timing. |
| Routing dependency analysis | Complete channel dependencies of the actual fixed up/down routes, with a checkable increasing channel order | A certificate for this routing policy. It is not a formal proof of arbitrary RTL/protocol liveness, especially with indefinitely stalled sinks. |
| Numerical inference | Tiny causal NumPy MoE decoder with prefill/decode KV cache, stable gating, resident expert ownership, exact contribution ledger and optional reliable expert-output transport | [Inference](INFERENCE_PROTOTYPE.md); independent full-prefix numerical reference and actual expert-vector fault recovery. Synthetic weights, one CPU process, logical islands and assumed compute cycles. |
| Fault protocol | CRC-protected contribution frames, ACK/retry, duplicate suppression, generation barriers and bounded abort/resource release | [Transport](TRANSPORT_PROTOCOL.md); actual numeric payload recovery. No authentication, crash-durable exactly-once service or modeled physical BER. |
| RTL and FPGA preparation | Synthesizable synchronous FIFO and four-endpoint word fabric, testbenches and generic synthesis | [RTL](RTL_PROTOTYPE.md). No FPGA board selection, pin constraints, timing closure, programmed device or board measurement. |
| Physical validation | Strict measurement loader, confidence bounds, configured limits and required coverage checks | [Protocol](PHYSICAL_VALIDATION_PROTOCOL.md). Missing or synthetic measurements produce `INSUFFICIENT_EVIDENCE`; no real measurements were supplied. |

The [prototype evidence](../artifacts/prototype_validation.json) records the executed scenarios and explicit scope. [RTL evidence](../artifacts/rtl_validation.json) records actual tool versions and synthesis statistics. The [prototype manifest](../artifacts/prototype_manifest.json) identifies source/config/test bytes and generated outputs. Unit-test files are identified by hash; CI separately executes them.

## Integration contract

The inference prototype uses the finite-buffer network for dispatch and combine scheduling and checks complete flit/credit accounting before phase transitions. CPU arrays supply actual numerical values and a separate logical ownership/byte ledger. The optional reliable contribution path carries serialized outputs from actual expert MLPs; a recoverable-fault case matches reference logits, while lost ACKs prevent combine, accepted logits and cache commit even when the receiver accepted the payloads. The standalone protocol reference also exercises numeric payload recovery, generation barriers and uncertain sender outcomes. Protocol ticks are separate from network cycles. The RTL is a word-switch baseline, not a bit-for-bit hardware realization of the Python multi-hop flit model.

The original sphere/torus sensitivity plots remain results from the separate infinite-output-queue baseline. They do not silently become finite-buffer, inference or RTL results after these additions.

## Gates still requiring evidence

| Gate | Current boundary | Required next evidence |
|---|---|---|
| Application value | Tiny numerical correctness and communication accounting are executable | Real trained model/traces, quality target, optimized equal-resource baselines and calibrated end-to-end performance/energy |
| Transport/RTL proof | Software invariants, finite routing certificate and RTL tests | Formal RTL assertions/liveness under explicit assumptions, CDC/RDC, protocol-to-RTL refinement and fault-transition coverage |
| FPGA demonstrator | Board-agnostic synthesizable logic | Target board, clocks/resets, constraints, place/route, bitstream, external trace I/O and measured bring-up |
| Physical demonstrator | Measurement procedure and assessment software | Instrument/calibration records, lane/corner BER, SI/PI, sustained power, voltage and thermal/coolant traces |
| Silicon commitment | Architecture and prototype evidence | Costed package, PPA, DFT, timing and manufacturing/yield signoff |

No throughput/J selection gate or physical compliance gate is claimed complete. Labeled synthetic fixtures exercise the assessment software and cannot qualify as measured power, temperature or instrument evidence. No real hardware measurements were supplied. See [validation plan](VALIDATION_PLAN.md) for acceptance criteria.
