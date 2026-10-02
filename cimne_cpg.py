#!/usr/bin/env python3
"""
CIMNE CPG rule engine (CDSS for aortic disease, ESVS guidelines).

Script version of cimne_cpg.ipynb. Evaluates a patient against the rules table
(rules_with_interventions.csv) using three-valued logic (MATCHED / CONDITIONAL /
FAILED), a priority short-circuit (emergencies first) and an arbitration layer
that ranks recommendations by ESVS class and level of evidence.

Examples
--------
  # Evaluate one patient described in a JSON file
  python cimne_cpg.py run example_patient.json

  # Same, but print the raw engine output as JSON
  python cimne_cpg.py run example_patient.json --json

  # Evaluate every patient in an admissions CSV (ds.csv format)
  python cimne_cpg.py batch ds.csv -o cdss_predictions.csv

  # List the variables the rules use (to know what to put in the patient JSON)
  python cimne_cpg.py vars
"""

import argparse
import json
import math
import os
import sys

import pandas as pd

DEFAULT_RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules_with_interventions.csv")


# ========================================================
# RULES LOADING
# ========================================================

def load_rules(path):
    df = pd.read_csv(path)
    return df.rename(columns={"class_x": "class", "desc_x": "desc", "evidence_x": "evidence", "reference_x": "reference"})


def clean_val(v):
    if v == "TRUE" or v is True: return True
    if v == "FALSE" or v is False: return False
    try:
        return float(v)
    except (ValueError, TypeError):
        # Some categorical values are stored quoted in the CSV (e.g. '"I"')
        return v.strip('"') if isinstance(v, str) else v


def build_engine_db(df):
    """
    Build the rule tree indexed by priority:
    { 1: {rule_id: {group_id: [conditions]}},   # Emergency / Acute
      2: {rule_id: {group_id: [conditions]}} }  # Elective / Chronic
    """
    engine_db = {1: {}, 2: {}}

    for _, row in df.iterrows():
        prio = int(row['priority'])
        r_id = row['rule']
        g_id = row['group']

        if prio not in engine_db:
            continue
        engine_db[prio].setdefault(r_id, {}).setdefault(g_id, []).append({
            'var': row['var'],
            'val': clean_val(row['value']),
            'op': row['op'],
            'rec': row['recommendation']
        })

    return engine_db


# ========================================================
# ENGINE
# ========================================================

def eval_group(conditions, patient_data):
    """
    Evaluate a group of AND conditions with three-valued logic.
    Returns ('MATCHED' | 'CONDITIONAL' | 'FAILED', recs, missing_vars).
    """
    has_unknown = False
    recs = []
    missing_vars = []

    for cond in conditions:
        var = cond['var']

        # A variable absent from the patient (or None) is UNKNOWN, not False
        if var not in patient_data or patient_data[var] is None:
            has_unknown = True
            missing_vars.append(var)
            continue

        actual = patient_data[var]
        target = cond['val']
        op = cond['op'].replace('"', '')

        try:
            if op == '==': is_met = (actual == target)
            elif op == '>=': is_met = (actual >= target)
            elif op == '>': is_met = (actual > target)
            elif op == '<': is_met = (actual < target)
            else: is_met = False
        except TypeError:
            # e.g. comparing a number against a placeholder such as "device_threshold"
            is_met = False

        if not is_met:
            return 'FAILED', [], []

        recs.append(cond['rec'])

    if has_unknown:
        return 'CONDITIONAL', recs, missing_vars

    return 'MATCHED', recs, []


def eval_priority(rules, patient_data):
    """Evaluate all rules of one priority level (OR across groups)."""
    matched = []
    conditional = []

    for r_id, groups in rules.items():
        rule_matched = False
        temp_conditionals = []

        for g_id, conditions in groups.items():
            status, recs, missing = eval_group(conditions, patient_data)

            if status == 'MATCHED':
                matched.append({'id': r_id, 'rec': conditions[0]['rec']})
                rule_matched = True
                break  # OR satisfied, skip the remaining groups of this rule

            elif status == 'CONDITIONAL':
                temp_conditionals.append({'id': r_id, 'rec': conditions[0]['rec'], 'missing_vars': missing})

        # No group matched but some could with more data: keep as conditional alert
        if not rule_matched and temp_conditionals:
            conditional.extend(temp_conditionals)

    return matched, conditional


def execute_engine(patient_data, db, df):
    """
    Run the engine on one patient. Returns:
    { "status": "EMERGENCY" | "STABLE_RECOMMENDATIONS" | "NO_MATCH_FOUND",
      "recommendations": [...], "conditional_alerts": [...] }
    """
    # PHASE 1: emergencies (priority 1)
    matched_p1, conditional_p1 = eval_priority(db.get(1, {}), patient_data)

    # Safety short-circuit: any fully matched emergency rule stops the engine
    if matched_p1:
        return {
            "status": "EMERGENCY",
            "recommendations": arbitration_layer(matched_p1, df),
            "conditional_alerts": []  # conditional noise is suppressed in emergencies
        }

    # PHASE 2: thresholds and elective (priority 2)
    matched_p2, conditional_p2 = eval_priority(db.get(2, {}), patient_data)

    # PHASE 3: arbitration and output
    final_p2 = arbitration_layer(matched_p2, df)

    clean_conditionals = []
    seen_rules = set()
    for cond in conditional_p1 + conditional_p2:
        if cond['id'] not in seen_rules:
            seen_rules.add(cond['id'])
            cond['missing_vars'] = list(set(cond['missing_vars']))
            clean_conditionals.append(cond)

    return {
        "status": "STABLE_RECOMMENDATIONS" if final_p2 else "NO_MATCH_FOUND",
        "recommendations": final_p2,
        "conditional_alerts": clean_conditionals
    }


def arbitration_layer(triggered_rules, df):
    """
    Enrich triggered rules with their metadata (intervention, class, evidence),
    sort them by clinical strength and remove weaker competing surgical options.
    """
    if not triggered_rules:
        return []

    # 1. Attach metadata from the rules table
    for rule in triggered_rules:
        r_id = rule['id']
        rec = rule['rec']
        row = df.query("rule == @r_id and recommendation == @rec")
        intervention = row['intervention'].values[0]
        rule['intervention'] = None if pd.isna(intervention) else intervention
        rule['class'] = row['class'].values[0]
        rule['evidence'] = row['evidence'].values[0]

    # 2. Class III vetoes (disabled, as in the notebook)
    valid_rules = list(triggered_rules)

    # 3. Sort by score = class weight * 10 + evidence weight (e.g. I/A = 43, IIa/C = 31)
    class_weights = {'I': 4, 'IIa': 3, 'IIb': 2, 'III': 0}
    evidence_weights = {'A': 3, 'B': 2, 'C': 1}

    def calculate_score(rule):
        return (class_weights.get(rule['class'], 0) * 10) + evidence_weights.get(rule['evidence'], 0)

    valid_rules.sort(key=calculate_score, reverse=True)

    # 4. Therapeutic competition: find a dominant Class I surgical strategy
    dominant_surgery = None
    for rule in valid_rules:
        if rule['intervention'] in ['REPAIR'] and rule['class'] == 'I':
            dominant_surgery = rule['intervention']
            break

    final_output = []
    for rule in valid_rules:
        # Class III never appears as an active recommendation
        if rule['class'] == 'III':
            continue

        interv = rule['intervention']

        # Non-surgical recommendations (drugs, follow-up, diagnostics) are cumulative
        if interv is None:
            final_output.append(rule)
            continue

        # Drop weaker alternatives when a dominant Class I surgery exists
        if dominant_surgery and interv != dominant_surgery:
            if class_weights.get(rule['class'], 0) < 4:
                continue

        final_output.append(rule)

    return final_output


# ========================================================
# OUTPUT
# ========================================================

def print_results(data, df):
    print(f"CDSS STATUS: {data['status']}\n")

    print("RECOMMENDATIONS")
    recommendations = data.get('recommendations', [])
    if not recommendations:
        print("   No recommendations triggered for this profile.")
    else:
        for rec in recommendations:
            r_id = rec['id']
            rec_id = rec['rec']
            print(f"   • [Rule {r_id}] (Rec ref: {rec_id}) | ESVS class: {rec['class']} | "
                  f"Evidence: {rec['evidence']} | Intervention: {rec['intervention'] or '-'}")
            desc = df.query("rule == @r_id and recommendation == @rec_id")['desc'].values[0]
            print(f"     {str(desc).strip()}")

    print("\nCONDITIONAL ALERTS (missing data blocking rules)")
    alerts = data.get('conditional_alerts', [])
    if not alerts:
        print("   None. The clinical profile is complete for the evaluated rules.")
    else:
        for alert in alerts:
            missing = ", ".join(f"'{v}'" for v in alert['missing_vars'])
            print(f"   • [Rule {alert['id']:02d}] (Rec ref: {alert['rec']}) -> missing: [{missing}]")


def to_jsonable(obj):
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    if hasattr(obj, 'item'):  # numpy scalars
        obj = obj.item()
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


# ========================================================
# BATCH (admissions CSV, ds.csv format)
# ========================================================

def admission_row_to_patient(row):
    """Map an admissions row (ds.csv columns) to engine variables."""
    row = row.to_dict() if isinstance(row, pd.Series) else row

    def flag(col):
        return row.get(col) == 1

    def num(col, cast=float):
        return cast(row[col]) if pd.notna(row.get(col)) else None

    hgb = row.get('HGB_IMPUTED') if pd.notna(row.get('HGB_IMPUTED')) else row.get('HAEMOGLOBIN')

    return {
        'context': 'aneurysm',
        'abdominal_thoracic': True,
        'descending_thoracic': False,
        'aortic_diameter': float(row['DIAMTER_MM']) / 10.0 if pd.notna(row.get('DIAMTER_MM')) else None,
        # Every patient in the dataset is assumed ruptured (as in the notebook)
        'rupture': True,  # flag('RETROPERITONEAL_HEMATOMA')
        'hypotension_during_imaging': flag('SHOCK_HYPOVOLEMIC'),
        'hemodynamic_stability': flag('HEMODYNAMIC_STABILITY'),
        'hypertension': flag('HYPERTENSION'),
        'diabetes': flag('DIABETES'),
        'smoker': flag('SMOKER'),
        'dislipemia': flag('DISLIPEMIA'),
        'age': num('AGE', int),
        'systolic_blood_pressure': num('SYSTOLIC_PRESSURE'),
        'diastolic_blood_pressure': num('DIASTOLIC_PRESSURE'),
        'hemoglobin': float(hgb) if pd.notna(hgb) else None,
        'ct_angiography_done': flag('CT_ANGIOGRAPHY'),
        'ultrasound_done': flag('US'),
        'pre_op_phase': True
    }


def process_batch(df_patients, engine_db, df_rules):
    results = []
    for _, row in df_patients.iterrows():
        output = execute_engine(admission_row_to_patient(row), engine_db, df_rules)
        recs = output['recommendations']
        interventions = [r['intervention'] for r in recs]
        results.append({
            'NHC': row.get('NHC'),
            'AGE': row.get('AGE'),
            'Exitus': row.get('EXITUS'),
            'Diameter_CM': float(row['DIAMTER_MM']) / 10.0 if pd.notna(row.get('DIAMTER_MM')) else None,
            'Shock': row.get('SHOCK_HYPOVOLEMIC', 0),
            'Engine_Status': output['status'],
            'Interventions': interventions,
            'Num_Recommendations': len(recs),
            'Active_Rule_IDs': [r['id'] for r in recs],
            'Missing_Blocking_Vars': sorted({v for c in output['conditional_alerts'] for v in c['missing_vars']}),
            'REPAIR': 'REPAIR' in interventions,
        })
    return pd.DataFrame(results)


# ========================================================
# CLI
# ========================================================

def cmd_run(args, df_rules, engine_db):
    with open(args.patient) if args.patient != '-' else sys.stdin as f:
        patient = json.load(f)
    output = execute_engine(patient, engine_db, df_rules)
    if args.json:
        print(json.dumps(to_jsonable(output), indent=2, ensure_ascii=False))
    else:
        print_results(output, df_rules)


def cmd_batch(args, df_rules, engine_db):
    df_patients = pd.read_csv(args.admissions)
    print(f"Processing {len(df_patients)} patients...", file=sys.stderr)
    df_out = process_batch(df_patients, engine_db, df_rules)
    df_out.to_csv(args.output, index=False)
    print(f"Saved predictions to {args.output}", file=sys.stderr)
    print("\nREPAIR recommended:")
    print(df_out['REPAIR'].value_counts().to_string())
    if 'Exitus' in df_out:
        print("\nREPAIR vs Exitus:")
        print(pd.crosstab(df_out['REPAIR'], df_out['Exitus']).to_string())


def cmd_vars(args, df_rules, engine_db):
    print(f"{'variable':32} {'ops':8} {'prio':6} {'unit':6} values")
    for var, g in df_rules.groupby('var'):
        ops = ",".join(sorted({o.replace('"', '') for o in g['op']}))
        prio = ",".join(str(p) for p in sorted(g['priority'].unique()))
        vals = ", ".join(sorted({str(clean_val(v)) for v in g['value']}))
        unit = ",".join(sorted({str(u) for u in g['unit'].dropna()})) or "-"
        print(f"{var:32} {ops:8} {prio:6} {unit:6} {vals}")


def main():
    parser = argparse.ArgumentParser(
        prog="cimne_cpg.py",
        description="CIMNE CPG rule engine: evaluate patients against ESVS aortic guideline rules.",
        epilog=__doc__.split("Examples", 1)[1].strip("\n-"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--rules", default=DEFAULT_RULES,
                        help="rules CSV (default: rules_with_interventions.csv next to this script)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="evaluate a single patient from a JSON file",
                           description="Evaluate a single patient. The JSON is a flat object of "
                                       "engine variables (see `vars`). Missing or null variables are "
                                       "treated as UNKNOWN and reported as conditional alerts.")
    p_run.add_argument("patient", help="patient JSON file ('-' to read from stdin)")
    p_run.add_argument("--json", action="store_true", help="print raw engine output as JSON")
    p_run.set_defaults(func=cmd_run)

    p_batch = sub.add_parser("batch", help="evaluate all patients in an admissions CSV (ds.csv format)",
                             description="Map each admissions row (ds.csv columns) to engine variables, "
                                         "run the engine and save one row of results per patient.")
    p_batch.add_argument("admissions", help="admissions CSV (e.g. ds.csv)")
    p_batch.add_argument("-o", "--output", default="cdss_predictions.csv",
                         help="output CSV (default: cdss_predictions.csv)")
    p_batch.set_defaults(func=cmd_batch)

    p_vars = sub.add_parser("vars", help="list the variables used by the rules",
                            description="List every variable referenced by the rules with its "
                                        "operators, priority levels, units and expected values.")
    p_vars.set_defaults(func=cmd_vars)

    args = parser.parse_args()
    df_rules = load_rules(args.rules)
    engine_db = build_engine_db(df_rules)
    args.func(args, df_rules, engine_db)


if __name__ == "__main__":
    main()
