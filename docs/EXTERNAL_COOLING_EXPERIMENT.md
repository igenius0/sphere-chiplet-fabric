# Segmented external cooling experiment

Architecture study v0.3, 13 November 2026. Proposed experiment only: no CAD,
thermal solution, instrument capture, equipment purchase or hardware acceptance
is supplied by this document. External 360-degree cooling means distributed
coverage of the faceted shell, not guaranteed uniform temperature or literally
complete coverage beneath connectors and supports.

## Candidate construction

Use flat replaceable carriers with representative compute and HBM heater
footprints. Fit removable cold-plate segments to outward-facing thermal
surfaces through specified thermal interface material. Keep coolant inside
sealed plate channels, separated from electronics. Supply the segments through
parallel manifold branches with flow measurement/balancing. Assess hose routing,
purge/drain access, mechanical loads and replacement access before fixing shape.

Removing the internal core is a candidate change. It does not eliminate the need
for direct, low-resistance heat paths from every hot component. A coolant jacket
around an insulating enclosure is not a substitute for cold-plate contact.
Do not extend this proposal to bare-die liquid contact. Dielectric immersion is
a separate design alternative requiring its own compatible materials, fluid,
electrical, service and safety review; it is not the baseline here.

## Freeze before fabrication

| Item | Required selection or record |
|---|---|
| Module geometry | Package or surrogate dimensions, heater map, HBM-height surrogates, keep-outs, supports, tolerances and usable contact area |
| Thermal path | Heater/silicon surrogate, sensor locations, package layers, TIM thickness, cold plate and spreading model; distinguish surface from junction temperature |
| Coolant loop | Fluid/properties at test temperature, material compatibility, channels, manifold, pump curve, radiator/heat exchanger, filter and purge/drain procedure |
| Mechanical integrity | Contact force/pressure limits from selected package/TIM, flatness, compliant mounts, differential expansion, hose forces and reassembly repeatability |
| Electrical/power | Insulated heaters, protected supply, independently verified overtemperature cutoff and component-rated protection |
| Instrumentation | Sensor/channel map, calibration records, uncertainty, synchronized timestamps and sample rates for both steady and transient behavior |
| Operating envelope | Heat map/load steps, inlet temperatures, flow range, ambient conditions, allowable temperatures/pressure, margins and trip thresholds |

All numerical limits remain unresolved until components and the operating
envelope are selected. Do not invent universal safe temperatures, pressures,
clamp loads or FPGA power values. Below-dew-point operation needs a reviewed
condensation-control design; otherwise keep exposed surfaces above the measured
dew point with a specified margin.

## Comparison matrix

Begin with one heater coupon to characterize contact and flow, then several
representative facets, then four-module packaging. Choose representative
locations and orientations, including likely poorly supplied branches.

| Case | Purpose | Required controls |
|---|---|---|
| Flat cold-plate reference | Establish conventional baseline | Same heater packages, TIM/contact rules, heat map and sensor method |
| Faceted external segments | Test the proposed shell layout | Same applied heat, ambient and inlet coolant temperature; record different hardware/area |
| Equal total flow sweep | Reveal temperature versus hydraulic cost | Record branch flows, pressure drop and total pump input power; equal flow alone is not equal energy |
| Equal cooling-power comparison | Compare thermal outcome at a fixed cooling-energy budget | Match total measured cooling power boundary, including pump and heat rejection equipment |
| Uniform and concentrated loads | Find local hotspots and HBM-neighbor heating | Same per-module load map and dwell/steady-state criterion |
| Load steps and reduced branch flow | Assess transient behavior and maldistribution | Predetermined trip criteria, capture rate and independent cutoff |
| Reassembly and thermal cycling | Check contact repeatability and package stress | Record interfaces, clamp loads, cycle profile and inspection results |

Use an independent hardware cutoff for pump-loss tests. Start at reduced heater
power and establish shutdown behavior before the maximum-load case. Pressure
and leak tests must follow the selected parts' ratings and a reviewed procedure;
perform initial leak testing without energized electronics.

## Capture and acceptance

Use [thermal_samples.csv](templates/thermal_samples.csv) as an empty long-form
capture layout and [thermal_acceptance.csv](templates/thermal_acceptance.csv) as
an empty decision worksheet. They are not measured data or inputs accepted by
the existing measurement harness. Capture repeated channel rows at each sample
time; `time_s` is elapsed seconds since the common run start. Define channel
names, allowed quantity names and their units in each run metadata record.
Each run also needs an immutable metadata record with configuration and
source hashes, origin, operator, fixture revision, calibration/uncertainty map,
fluid properties, load map, raw capture hashes and units. Define repeat count,
run order, warm-up, steady-state slope/window and missing-data policy in advance.

Record at least:

- Every compute and HBM surrogate temperature, plate temperatures, inlet/outlet
  coolant temperatures and ambient temperature/humidity/dew point.
- Per-branch and total flow, pressure drop, heater electrical power, pump power,
  heat-rejection power and the defined complete cooling-power boundary.
- Mount/contact configuration, leakage, deformation, TIM condition and faults.
- Cutoff trigger, hottest observed temperature and time to safe shutdown during
  controlled faults. Sensor absence/failure must have a defined safe response.

Compare maximum component temperatures and gradients, not just average shell
temperature. Estimate thermal resistance using a declared temperature pair and
that path's heat load; do not divide each branch temperature rise by total
system power. Check coolant calorimetry Q = mass_flow * specific_heat * delta_T
against measured heat input, accounting for pump heat, ambient losses and
instrument uncertainty. Poor energy balance is an investigation trigger, not
proof of cooling capacity. Low delta_T can make calorimetry uncertainty large.

For each acceptance row, freeze the method, limit, margin and uncertainty rule
before the run. Missing required evidence is INSUFFICIENT_EVIDENCE, an exceeded
qualified limit is FAIL, and only complete qualified coverage permits PASS for
the declared experiment. A passing heater test validates that fixture and heat
map; real junction temperature requires a correlated package thermal model or
appropriate on-die measurement. It does not qualify an accelerator.

The [existing physical measurement checker](PHYSICAL_VALIDATION_PROTOCOL.md)
checks declared BER, voltage, power and temperature coverage/provenance. It does
not assess flow, pressure, leak integrity, contact mechanics, humidity,
calorimetric balance or fault-transient acceptance. Review these in the separate
worksheet; its automated PASS alone cannot accept this cooling experiment.

## Geometry sanity check, not a cooling rating

The historical radius of 40 mm gives 4*pi*r^2 = 0.020106 m^2, or approximately
201.06 cm^2 gross sphere area. An illustrative 2,000 W divided by that area is
9.95 W/cm^2. Neither number specifies a safe load: usable contact area is smaller,
heat is nonuniform, and package/TIM/plate/fluid resistances and pumping remain
unknown. This radius is not a frozen enclosure dimension or a completed module
floorplan. Do not infer a sphere advantage by comparing differently sized
packages or by confusing surface area with heat-transfer conductance.

## Deliverable and selection

Produce drawings, a quoted BOM, risk review, raw calibrated captures, analysis
with uncertainty, failed cases and the completed worksheet. Select external
cooling over an internal core only when the required hotspot/fault limits pass
and the compared thermal or energy objective is met within declared uncertainty.
Select physical shell packaging against the planar reference separately.

Technology references (not validation of this design):
[OCP cold-plate work](https://www.opencompute.org/projects/cooling-environments/cold-plate/)
and [OCP immersion work](https://www.opencompute.org/projects/cooling-environments/immersion/).
