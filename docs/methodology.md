# Methodology

H3 computes empirical HTC (W/K) from HEM outputs using temperature-offset runs,
then summarises the result in a report. The aim is to keep the workflow
transparent and reproducible rather than hiding the calculation inside a single
headline number.

This page is the authoritative description of the calculation. For the code, see
[Codebase](codebase.md) and `src/htc_harness/empirical.py`.

## Inputs

H3 expects:

- a base HEM JSON model (HEM/FHS schema),
- either EPW weather or an `ExternalConditions` JSON,
- optionally a measured HTC JSON when you want comparison or calibration.

## Core idea

Instead of relying on one model run, H3 compares a base run with one or more
temperature-offset runs. It looks at how heat loss changes as external
temperature is shifted, then derives HTC from that relationship.

The offset temperature is:

`ΔT(k) = Tout(offset k) - Tout(base)`

## Loss power definition

The primary loss-power proxy sums the air-node loss terms, with the FIHC
correction applied by default:

- `ThermalBridges`
- `InfiltrationVentilation`
- `FabricHeatLoss` — includes `FabricIntHeatCool` unless `--no-fihc` is passed

An alternate proxy, external-boundary `HeatingOrCoolingSystemGains`, is computed
for comparison only. Air-node `HeatingOrCoolingSystemGains` and internal-boundary
`FabricIntHeatCool` are recorded as diagnostics.

### FabricIntHeatCool (FIHC)

FIHC is the radiative fraction of heating-system output that goes directly to
fabric surfaces, taken from the internal-boundary heat balance. HEM separates it
from solar (`FabricIntSol`) and internal gains (`FabricIntIntGains`) in distinct
columns, so FIHC captures only the heating system's contribution.

For emitters with a radiative fraction — wet radiators at `frac_convective ≈ 0.4`,
say — roughly 60% of heating output bypasses the air node entirely. Without the
correction, air-node `FabricHeatLoss` reflects convective transfer only and
systematically underestimates HTC.

Use `--no-fihc` only when you intentionally want an air-node-only methodology,
for example to replicate a co-heating test with 100% convective electric fan
heaters.

## Step-by-step workflow

### 1. Run the base and offset cases

H3 generates offset weather series and runs HEM for the base case and each
offset. The default offsets are `-1.0 K` and `+1.0 K`.

### 2. Apply optional weather normalisation

When using `ExternalConditions`, H3 can modify weather inputs before HEM runs:

- `--flatten-solar`: replace hourly `diffuse_horizontal_radiation` and
  `direct_beam_radiation` with their annual mean — a constant irradiance at
  every timestep, including night,
- `--remove-solar`: set all solar irradiance to zero,
- `--flatten-wind`: replace hourly `wind_speeds` with the annual mean,
- `--remove-wind`: set all wind speeds to zero.

Flatten and remove are mutually exclusive for the same variable. These options
require `--external-conditions`; an EPW cannot be normalised in place.

### 3. Select the analysis window

The analysis window can be:

- the full run (default),
- a manual index window with `--window start_idx,end_idx`,
- heating-only timesteps with `--heating-only`, which reads the heat-demand
  column from the base run's `results.csv`.

### 4. Compute loss power across offsets

For each offset run `k` and each timestep `t` in the selected window:

```
q_loss(t,k) = -(ThermalBridges + InfiltrationVentilation + FabricHeatLoss)
```

The negative sign makes positive values represent heat leaving the dwelling.
With FIHC enabled (the default):

```
q_loss(t,k)       += FabricIntHeatCool(t,k)
FabricHeatLoss(t,k) += FabricIntHeatCool(t,k)
```

FIHC is folded into `FabricHeatLoss` so the three-way component breakdown still
sums to the corrected total.

### 5. Estimate HTC from the offset relationship

H3 first derives a per-timestep HTC by regressing across offsets, then
aggregates to blocks — daily, weekly or monthly. For each block `b`:

- aggregate loss energy over the block: `Q_k(b) = Σ_{t∈b} q_loss(t,k) · Δt`
- fit a line across offsets: `Q_k(b) ≈ A(b) + m(b) · ΔT(k)`
- convert the slope to HTC: `HTC(b) = -m(b) / hours_in_block`

## Masking and block filtering

Masking is optional but recommended. The per-timestep mask keeps a timestep only
when both hold:

- **heating active**: `HeatingOrCoolingSystemGains` — or a fallback heating proxy
  — is greater than `--heating-eps-w`,
- **demand met**: `_unmet_demand:*` is at most `--unmet-eps-kwh`.

Block gating then drops whole blocks whose valid fraction falls below
`--valid-min-fraction`. `--mask-block-hours` sets the block size; `0` disables
gating.

One caveat worth knowing: the summary bins gating blocks uniformly over the
selected timesteps, while the HTML report bins them on calendar boundaries. The
two agree whenever no timesteps were filtered out, and can differ under
`--heating-only` or a sparse window.

## Diagnostics

- **Component breakdown**: each loss component is regressed separately, giving
  the `FabricHeatLoss` (convective + radiative heating), `InfiltrationVentilation`
  and `ThermalBridges` contributions.
- **System vs loss**: air-node drivers and the system split are compared against
  the loss terms.
- **Residuals across offsets**: reveal non-linearity and outliers.
- **Air-node energy balance**: drivers plus residual, in raw HEM sign
  conventions.

## Aggregation and calendar handling

Daily and weekly blocks use fixed timestep counts. Monthly blocks are derived
from a 365-day calendar and the selected indices, labelled Jan…Dec. There is no
leap-year handling.

## Key fields in `htc_summary.json`

| Field | Meaning |
|---|---|
| `summary.htc_empirical_w_per_k` | Mean HTC over the selected window |
| `summary.htc_design_w_per_k` | Design HTC from the base run's `results_static.csv` |
| `derived.q_loss_w_by_offset_k` | Loss power by offset (W), including the FIHC correction |
| `derived.q_loss_components_w_by_offset_k` | Loss components by offset; `FabricHeatLoss` includes FIHC when enabled |
| `derived.q_system_total_w_by_offset_k` | System gains (air + fabric) |
| `derived.q_system_fabric_int_heat_cool_w_by_offset_k` | Raw FIHC values, before folding |
| `derived.air_node_energy_balance` | Air-node drivers and residual |
| `derived.temperature_series.external_air_temp_c` | External air temperature; hourly EPW data is upsampled |

See [Outputs](outputs.md) for the full file-by-file reference.

## Calibration methodology

`h3 calibrate` fits **one shared scalar** for ventilation and fabric against a
measured HTC target. It does not separately identify the two.

### 1. Baseline, probe and calibrated runs

- The baseline run gives `htc_baseline`.
- One global probe run at `1 + delta_scalar` gives `htc_global`.
- A calibrated run applies the solved scalar.

### 2. Sensitivity and fit

Sensitivity comes from the probe:

```
s_global = (htc_global - htc_baseline) / delta_scalar
```

The solver fits `htc_pred = htc_baseline + s_global · (scalar - 1)` by
minimising:

```
Σ(htc_pred - htc_target)² + λ(scalar - 1)²
```

`λ` is `--lambda-reg`; bounds come from `--scalar-min` and `--scalar-max`.

### 3. Target metric

The target is the masked **monthly** HTC aggregate, with the statistic set by
`--calibration-stat mean|median`. The same masking policy applies to the
baseline, probe and calibrated runs, and the baseline mask is frozen so the
objective does not move between iterations.

### 4. Iterative refinement

With `--opt-steps > 0`, the calibrated point is refined in further single-scalar
steps. Iteration stops at the step limit, within tolerance
(`--opt-tol-w-per-k`), or on a near-zero increment.

### 5. What the scalar actually scales

- The ACH scalar scales the effective ventilation heat-transfer term used in the
  zone balance, via `HEM_ACH_CAL_FACTOR`.
- Fabric scaling is applied to the input model before the run: element
  `u_value`, `thermal_resistance_construction`, and linear and point thermal
  bridges. Ground-floor `u_value` is capped to keep `r_vi > 0`, matching HEM's
  own validation, so ground elements may move less than the scalar implies.

## Current limitations

- Monthly aggregation uses a 365-day calendar with no leap-year handling.
- System-based HTC is reported for comparison, not as the primary metric.
- Calibration does not separately identify ventilation from fabric.
- No automated model validation beyond HEM's own schema checks.
- Additional filters — high-ΔT, low-solar, night-only — are not implemented.
