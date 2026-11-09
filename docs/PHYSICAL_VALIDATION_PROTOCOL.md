# Physical measurement evidence protocol

Revision **v1.2.0, 10 November 2026**. The dependency-free
[`sim.physical_validation`](../sim/physical_validation.py) module assesses declared
measurement records against user-configured limits. **No physical device,
laboratory capture or genuine instrument calibration has been supplied.** The
checked-in examples are explicitly synthetic or missing-hardware fixtures. They
cannot complete the physical demonstrator gate in the
[validation plan](VALIDATION_PLAN.md).

## Running the evidence gate

```bash
python -m sim.physical_validation \
  --manifest examples/measurements/missing_hardware.json \
  --output generated/physical.json
python -m sim.physical_validation \
  --manifest examples/measurements/synthetic_manifest.json \
  --output generated/physical_synthetic.json
python -m unittest tests.test_physical_validation
```

Both example commands intentionally exit **2**. They write deterministic JSON
reports; the synthetic example has numerically passing fixture values but cannot
qualify as measured evidence. The output directory is created when needed.

| Overall `status` | CLI exit | Meaning |
| --- | --- | --- |
| `PASS` | 0 | Complete required coverage, valid declared measured provenance and calibration metadata, and every configured numerical limit passes |
| `FAIL` | 1 | Qualified declared measured evidence violates a configured numerical limit |
| `INSUFFICIENT_EVIDENCE` | 2 | Missing, malformed, synthetic, simulated, unqualified, or incomplete evidence prevents a measured conclusion |

The Python entry point `assess_measurements(manifest)` accepts a dictionary or a
JSON manifest path and returns the report. Relative source paths resolve beside
a manifest file; for dictionary input they resolve beneath the current working
directory. Missing or invalid input becomes an insufficient-evidence report.
Output-path write failures and command-line argument errors remain ordinary
process errors rather than measurement conclusions.

## Manifest schema, version 1

Objects have exactly the documented keys. Unknown keys, duplicate JSON keys,
nonfinite numbers (`NaN`, infinities), booleans used as numbers, and malformed
records are rejected. All names and metadata strings must be trimmed, nonempty,
at most 256 characters and free of control characters. JSON numbers must be
numbers rather than numeric strings. Object key order is irrelevant; record
order within a lane or channel is significant.

| Top-level key | Requirement |
| --- | --- |
| `schema_version` | Required, exact JSON integer `1` |
| `configuration` | Required, fields below |
| `identity` | Required for qualification: exactly `dut_id`, `dut_revision`, `board_id`, `operator`, `laboratory`, all strings |
| `evidence` | Required for qualification: exactly `origin`, `capture_id`, `captured_at`, `description` |
| `instruments` | Required for qualification: calibration records for every instrument used in any measurement row |
| `sources` | Contains optional `electrical` and `telemetry` descriptors; both are required for complete coverage |

`origin` must be `measured`, `synthetic`, `simulated` or `none` in source and row
records. Qualification requires `measured` consistently in the manifest,
descriptors, JSON source envelopes and all rows, with identical `capture_id`
values. A missing or non-measured manifest origin cannot qualify. A `captured_at`
value is an ISO-8601 timestamp with a timezone, for example
`2026-11-10T00:00:00Z`. All row times are nonnegative seconds relative to that
common capture start, including across corners and instruments. Record corner
labels identify externally established test conditions; the loader does not
verify the conditions themselves.

### Configuration and limits

The configuration has exactly these keys:

| Key | Value |
| --- | --- |
| `lanes`, `corners`, `rails`, `thermal_channels`, `coolant_channels` | Each a list of 1–256 unique names; every listed profile is required |
| `min_duration_s` | Positive finite number, at most `1e12` seconds |
| `max_sample_gap_s` | Positive finite number, at most `1e12` seconds |
| `confidence_level` | Finite number strictly between 0 and 1; the one-sided confidence for each individual lane/corner BER bound |
| `limits` | Exactly `ber_upper_max`, `temperature_c`, `power_w`, `voltage_v`, `coolant_c` |

`ber_upper_max` is a finite number in `[0, 1]`. Each other limit maps **every and
only** its configured channel names to `{"min": number, "max": number}`, with
`min <= max`. Temperature and coolant values use degrees Celsius, power uses
watts, and voltage uses volts. Temperature channels come from
`thermal_channels`; power and voltage each use every `rails` name; coolant uses
`coolant_channels`. Each numerical bound must lie in `[-1e12, 1e12]`.
All limit comparisons are inclusive: equality passes.

The caller must establish appropriate limits, corners, durations, gap budgets
and measurement bandwidth before capture. These example limits are parser test
inputs, not specifications for any PHY, package, component or safety standard.
The software permits permissive limits, including a BER maximum of 1; acceptance
therefore requires independent review of the configuration as well as the data.

### Calibration records

Every instrument entry has exactly `instrument_id`, `serial_number`,
`calibration_id`, `calibrated_at`, `valid_until`, `uncertainty`, and
`uncertainty_method`. IDs must be unique. The first three fields and the method
are strings; the two dates require timezone-bearing ISO-8601 timestamps.
The calibration interval must include the capture start and the latest timestamp
or electrical interval end attributed to that instrument, inclusive. Expiry
during a capture prevents qualification.

`uncertainty` is an object containing a finite, nonnegative value for every
quantity that instrument measures: `ber`, `temperature_c`, `power_w`,
`voltage_v` or `coolant_c`. Other keys are rejected; additional entries for those
five recognized quantities are allowed. BER uncertainty is in dimensionless
probability units; other entries use the measurement's units. The method must
explain the uncertainty convention and how it was obtained. Zero uncertainty is
accepted structurally and requires scientific review. The loader records
uncertainty; it does **not** apply uncertainty budgets, guard bands or a decision
rule automatically.

### Sources and records

Each source descriptor requires `origin` and `capture_id`, plus exactly one
storage mode:

```json
{"origin": "synthetic", "capture_id": "example", "records": []}
```

or

```json
{"origin": "synthetic", "capture_id": "example",
 "path": "capture/electrical.json", "format": "json"}
```

File format is `json` or `csv`. Paths must be relative and remain within the
manifest directory after resolution, including symlinks. JSON source files have
exactly `schema_version` (integer `1`), `origin`, `capture_id` and `records` (a
list). Inline sources put that list directly in `records` without an envelope.
The report hashes original file bytes using SHA-256, or canonical sorted,
compact JSON bytes for inline records. The hash identifies the assessed bytes;
it does not authenticate their origin.

Every electrical row has exactly:

| Field | Meaning |
| --- | --- |
| `corner`, `lane` | Configured corner and lane names |
| `origin`, `capture_id`, `instrument_id` | Provenance and instrument strings |
| `start_s`, `end_s` | Finite seconds in `[0, 1e12]`, with `end_s > start_s` |
| `errors`, `trial_bits` | Exact integer counts in `[0, 10**18]`, with `errors <= trial_bits` |

Counts are bit trials, not bytes, packets, decoded payload bits or elapsed time.
Counter resets, rollover, repeated captures and overlapping acquisition windows
must be reconciled before export. For each lane/corner, rows must be supplied in
time order and intervals must not overlap; contiguous intervals are allowed.
Errors and trial bits are summed per lane/corner, never pooled across lanes or
corners. The summed counts must also remain within `10**18`.

Every telemetry row has exactly:

| Field | Meaning |
| --- | --- |
| `corner` | A configured corner name |
| `origin`, `capture_id`, `instrument_id` | Provenance and instrument strings |
| `quantity`, `channel` | One of the four quantity names and its configured channel |
| `timestamp_s` | Finite seconds in `[0, 1e12]` |
| `value` | Finite number in `[-1e12, 1e12]` in the quantity's units |

Timestamps must be strictly increasing within each corner/quantity/channel;
duplicates and reordered samples are invalid. Rows for different profiles may
be interleaved. Raw observed extrema are compared with the configured range.
There is no interpolation, smoothing or inference about unobserved excursions.

CSV headers must contain exactly the applicable row fields once each. UTF-8
with an optional BOM is accepted. Numeric cells permit ordinary signed decimal
or scientific notation, with surrounding whitespace trimmed; count cells permit
only unsigned decimal digits. Thus `10000` is a valid bit count, while `1e4`,
`10000.0`, `true`, `NaN`, missing cells and extra cells are rejected. Provenance
is repeated in CSV rows because there is no JSON envelope.

## Coverage and BER calculation

For every corner, the loader requires electrical evidence for every lane and
telemetry for every temperature, power, voltage and coolant channel.

For each lane/corner, positive-trial-bit intervals must start at time 0, extend
to at least `min_duration_s`, sum to at least that much observed duration, and
have no gap larger than `max_sample_gap_s`. A zero-bit interval contributes no
coverage and cannot fill an active-data gap. A bounded gap is allowed only when
the remaining observed time still satisfies the duration requirement; continuous
coverage can be required externally by supplying contiguous intervals. Counts
over all supplied intervals are assessed, including those beyond the minimum
duration.

For each telemetry profile, at least two samples must start at time 0, end at or
beyond `min_duration_s`, and have no consecutive gap larger than
`max_sample_gap_s`. Sparse, absent or truncated traces cannot pass. Sampling
coverage does not establish that no excursion occurred between samples.

For `e` errors among `N > 0` independent identically distributed Bernoulli bit
trials and confidence `c`, the BER limit is the one-sided exact
Clopper–Pearson upper bound `p_U` satisfying

\[
\Pr_{p_U}(X \le e)=1-c,\qquad X\sim\mathrm{Binomial}(N,p_U).
\]

For zero errors it evaluates
`-expm1(log1p(-c) / N)`, equivalent to `1 - (1-c)**(1/N)` while retaining
precision at large bit counts. A positive mathematical result below the smallest
representable float is rounded up to that positive float. For `e == N` the
upper bound is 1. Other cases invert a finite binomial tail using a log-domain
sum, its complementary tail when shorter, and floating-point bisection. Extreme
confidence (`min(c, 1-c) < 0.01`) uses additional decimal precision to
avoid losing a near-one CDF target. Both paths have fixed bisection limits (200
and 1,200 iterations respectively), stopping earlier at adjacent float
endpoints. Decimal precision is bounded by 374 significant digits for accepted
float confidence values; unsupported decimal arithmetic fails closed.
Arithmetic remains subject to finite-precision rounding;
these are numerical evaluations of an exact statistical method, not certified
interval arithmetic. Normal, Poisson and rule-of-three approximations are not
substituted.

The exact binomial method and one-sided options are described in the
[NIST Dataplot reference](https://itl.nist.gov/div898/software/dataplot/refman2/auxillar/exacbino.htm)
and [NIST confidence-interval guidance](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
Burst errors, correlated patterns, varying BER and tester limitations require
an appropriate externally justified statistical model. Confidence applies per
profile; the harness applies no simultaneous multiple-profile correction.

The electrical result passes its numerical limit only when `p_U <=
ber_upper_max`; an observed BER of zero alone is insufficient. A limit breach
is kept visible even if coverage or provenance is incomplete.

## Report boundaries and resource limits

Reports contain `report_version` (`1.2.0`), `schema_version` (`1`), `status`,
`numeric_status`, `evidence_qualification`, normalized `configuration`, metadata,
source hashes/counts, per-profile `electrical` and `telemetry` results, sorted
`issues`, `scope`, and `assumptions`. Invalid input may prevent some profile or
metadata fields from being produced.

`numeric_status` is `FAIL` if a numerical breach was established, otherwise
`INSUFFICIENT_EVIDENCE` for invalid input or missing coverage, otherwise `PASS`.
`evidence_qualification` is `QUALIFIED_MEASURED` only when all declared identity,
provenance, instrument and input requirements qualify; otherwise it is
`UNQUALIFIED`. Overall status follows numeric status only for qualified evidence;
unqualified evidence always returns `INSUFFICIENT_EVIDENCE`. A per-profile
numerical `PASS` in a synthetic report is not an overall hardware pass.

| Resource | Limit; exceeding it is insufficient evidence |
| --- | --- |
| Manifest file | 1 MiB |
| Each source, including serialized inline records | 8 MiB |
| Each source's records | 100,000 |
| Instruments | 256 |
| Required lane/channel/corner profiles | 1,024, counting power and voltage separately |
| Individual and aggregate errors/bit trials | `10**18` |
| One exact binomial tail | 10,001 terms; zero/all-error cases use one |
| Sum of exact-tail terms over electrical profiles | 100,000 |

The file-byte limit is enforced before parsing file contents; dictionary input
has no manifest-file byte cap but each inline source retains its source-byte
and row caps. The evaluator rejects excessive arithmetic instead of silently
replacing the exact bound with an approximation. Detailed issue categories are
`invalid_input`, `qualification`, `coverage` and `failure`.

Origin declarations and calibration authenticity require independent review.
Relabeling only a manifest or descriptor cannot hide synthetic source envelopes
or rows, but software cannot detect deliberate consistent fabrication of all
metadata and data. A passing schema therefore establishes neither provenance
authenticity nor electrical compliance, SI/PI signoff, thermal safety, cooling
capacity, sustained application performance or energy efficiency. Real work
still requires a defined DUT and setup, calibrated acquisition, independently
reviewed limits and uncertainty, archived raw captures and all additional
component/PHY-specific signoff tests.

The [regression tests](../tests/test_physical_validation.py) use hand-derived
cases and an independent high-precision polynomial oracle for BER; they also
exercise strict input rejection, origin/capture mismatches, calibration expiry,
required-profile coverage, gaps, limits, file formats, hashes and CLI exit codes.
Test fixtures labeled `measured` exist only to exercise that declared code path;
they are not physical measurements and are never presented as a hardware result.
