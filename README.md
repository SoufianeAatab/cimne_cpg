# CIMNE CPG rule engine

A rule engine that checks a patient against the ESVS aortic guidelines and returns the recommendations that apply. It is the script version of `cimne_cpg.ipynb`.

> **Research prototype.** This is not a medical device and has not been clinically validated. Do not use it for real patient care.

## Requirements

- Python 3.8 or later
- `pandas`

```bash
pip install pandas
```

The rules are read from `rules_with_interventions.csv`, which must sit next to the script. To use a different file, pass `--rules path/to/rules.csv`.

## Quick start

```bash
python cimne_cpg.py run example_patient.json
```

For help on any command:

```bash
python cimne_cpg.py --help
python cimne_cpg.py run --help
```

## Commands

| Command | What it does |
|---|---|
| `run PATIENT.json [--json]` | Checks one patient. Prints a readable report, or the raw engine output with `--json`. Use `-` to read the JSON from stdin. |
| `batch ADMISSIONS.csv [-o OUT.csv]` | Checks every row of an admissions CSV in the `ds.csv` format. Writes one result row per patient and prints a REPAIR vs Exitus summary. |
| `vars` | Lists every variable the rules use, with its operators, priority levels, units and expected values. |

## Web app

[app.py](app.py) is a Streamlit page for trying the engine by hand. You either fill in a patient form or paste a patient JSON (same format as below) into the "Paste patient JSON" box. The page then shows the engine's status, recommendations and conditional alerts. It needs Streamlit 1.33 or later:

```bash
pip install "streamlit>=1.33"
streamlit run app.py
```

## The patient JSON

The patient is one flat JSON object. Each key is an engine variable name (run `vars` to see the full list):

```json
{
  "context": "aneurysm",
  "descending_thoracic": true,
  "aortic_diameter": 6.2,
  "rupture": false,
  "fit_for_repair": true,
  "repair_type": "TEVAR",
  "vessel_diameter": null
}
```

- **Booleans:** `true` / `false`
- **Numbers:** plain numbers, in the unit the rules use. `vars` shows each variable's unit; most diameters are in **cm**, e.g. `aortic_diameter`.
- **Categories:** strings, written exactly as `vars` shows them, e.g. `"acute_type_B"`, `"TEVAR"`, `"elective"`, `"high"`.
- **Unknown values:** leave the key out, or set it to `null`. The engine treats it as *unknown*, not false (see below).

[example_patient.json](example_patient.json) is a full example: a 68-year-old patient, fit for surgery, with a 6.2 cm descending thoracic aneurysm, planned for elective TEVAR.

## How the engine decides

1. **Rules and groups.** Each rule has one or more groups of conditions. All conditions in a group must hold (AND). A rule fires if any one of its groups holds (OR).
2. **Three outcomes per group.** Each group ends up as one of:
   - `MATCHED`: every condition holds.
   - `FAILED`: at least one condition is false.
   - `CONDITIONAL`: nothing is false, but some variables are missing.

   Conditional rules are reported as alerts, together with the variables needed to settle them.
3. **Emergencies first.** Priority 1 (emergency) rules are checked first. If any of them fires, the engine stops at once. It returns status `EMERGENCY` and suppresses conditional alerts. If none fires, it goes on to the priority 2 (elective/chronic) rules.
4. **Ranking.** The rules that fired are scored as `class weight × 10 + evidence weight`:
   - Class weights: I = 4, IIa = 3, IIb = 2, III = 0
   - Evidence weights: A = 3, B = 2, C = 1

   They are then sorted from highest to lowest score. Rules marked exactly `III` are removed. The current rules table uses `IIIa`/`IIIb`, which are not removed; they score 0 and come last. If a Class I `REPAIR` is present, weaker competing surgical options are dropped. Non-surgical advice (drugs, follow-up, diagnostics) is always kept.

## Output

```json
{
  "status": "STABLE_RECOMMENDATIONS",
  "recommendations": [
    {"id": 3, "rec": 61, "intervention": "REPAIR", "class": "IIa", "evidence": "C"}
  ],
  "conditional_alerts": [
    {"id": 8, "rec": 128, "missing_vars": ["vessel_diameter"]}
  ]
}
```

- `status`: `EMERGENCY`, `STABLE_RECOMMENDATIONS` or `NO_MATCH_FOUND`.
- `id`: the rule ID. `rec`: the ID of the guideline recommendation. The readable report also prints the recommendation text.
- `intervention`: `REPAIR`, or `null` for non-surgical advice.

## Batch mode

`batch` maps the `ds.csv` columns to engine variables:

| CSV column | Engine variable |
|---|---|
| `DIAMTER_MM` | `aortic_diameter` (divided by 10, so in cm) |
| `SHOCK_HYPOVOLEMIC` | `hypotension_during_imaging` |
| `HEMODYNAMIC_STABILITY` | `hemodynamic_stability` (not yet used by any rule; the rules check `haemodynamically_stable`) |
| `AGE` | `age` |

The other mapped columns are the comorbidities, blood pressure, haemoglobin and imaging. Every patient is assumed to be an abdominal-thoracic aneurysm with `rupture = true`, in the pre-operative phase, as in the notebook. The output CSV has these columns: engine status, interventions, active rule IDs, the missing variables blocking other rules, and a `REPAIR` flag.
