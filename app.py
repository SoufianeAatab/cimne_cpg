import json
import os

import streamlit as st
from cimne_cpg import DEFAULT_RULES, build_engine_db, execute_engine, load_rules, to_jsonable


@st.cache_resource
def load_engine(path=DEFAULT_RULES):
    df = load_rules(path)
    return df, build_engine_db(df)


UNKNOWN = "Unknown"


def unit_of(var):
    """Unit the rules use for a variable, e.g. 'cm' (empty string if none)."""
    units = df_reglas.loc[df_reglas["var"] == var, "unit"].dropna()
    units = [u for u in units if u != "-"]
    return units[0] if units else ""


def optional_number(label, var, **kwargs):
    """Number input with a 'Provide' toggle. Returns None (unknown) when the toggle is off.
    The input is always shown because widgets inside a form only rerun on submit."""
    unit = unit_of(var)
    full_label = f"{label} ({unit})" if unit else label
    provided = st.checkbox(f"Provide {full_label}", value=False, key=f"inc_{var}")
    value = st.number_input(full_label, key=var, **kwargs)
    return value if provided else None


def choice(label, options, index=0):
    """Selectbox with an extra 'Unknown' option. options maps label -> engine value."""
    labels = list(options) + [UNKNOWN]
    picked = st.selectbox(label, options=labels, index=index)
    return None if picked == UNKNOWN else options[picked]


YES_NO = {"No": False, "Yes": True}


def rule_text(rule_id, rec):
    rows = df_reglas.query("rule == @rule_id and recommendation == @rec")["desc"]
    return str(rows.values[0]).strip() if len(rows) else ""

# 1. WEB PAGE CONFIGURATION
st.set_page_config(
    page_title="CDSS - Aortic Rules Validator",
    page_icon="🩺",
    layout="wide"
)

# Load the rules after set_page_config: the cache spinner counts as a Streamlit command
df_reglas, engine_db = load_engine()

st.title("🩺 Clinical Decision Support System (CDSS)")
st.subheader("Rule Engine Testing & Validation Environment (ESVS 2026)")
st.write("Modify the patient variables in the left panel to evaluate the engine's response in real time.")

st.divider()

# Layout setup: Column 1 (Inputs) | Column 2 (Engine Output)
col_input, col_output = st.columns([1, 1.2], gap="large")

# ========================================================
# LEFT COLUMN: PATIENT CLINICAL VARIABLES FORM
# ========================================================
with col_input:
    st.header("📋 Patient Clinical Data")

    # --- OPTION A: PASTE A PATIENT JSON (same format as `cimne_cpg.py run`) ---
    json_patient = None
    with st.expander("📥 Paste patient JSON", expanded=False):
        with st.form("json_form"):
            example_path = os.path.join(os.path.dirname(DEFAULT_RULES), "example_patient.json")
            example = open(example_path).read() if os.path.exists(example_path) else '{"aortic_diameter": 6.2}'
            json_text = st.text_area("Patient JSON", height=300, placeholder=example,
                                     help="A flat JSON object of engine variables. Missing or null variables are treated as unknown.")
            json_btn = st.form_submit_button("Run Engine on JSON", type="primary")

        if json_btn:
            try:
                parsed = json.loads(json_text)
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")
            else:
                if not isinstance(parsed, dict):
                    st.error("The JSON must be an object, e.g. {\"aortic_diameter\": 6.2}.")
                else:
                    json_patient = parsed
                    unknown_keys = sorted(set(parsed) - set(df_reglas["var"]) - {"context"})
                    if unknown_keys:
                        st.warning("Not used by any rule (check for typos): " + ", ".join(f"`{k}`" for k in unknown_keys))

    # --- OPTION B: FILL IN THE FORM ---
    with st.form("patient_form"):
        
        # --- SECTION 1: DEMOGRAPHICS & BASICS ---
        with st.expander("👤 1. Demographics & Global Status", expanded=True):
            age = st.number_input("Age", min_value=0, max_value=120, value=60)
            
            st.markdown("**Patient Suitability & Choices:**")
            fit_for_repair = st.checkbox("Fit for Repair", value=True)
            unfit_for_repair = st.checkbox("Unfit for Repair", value=False)
            open_surgery_contraindicated = st.checkbox("Open Surgery Contraindicated", value=False)
            
            # Adjusted based on extracted categorical values ['high', 'low']
            surgical_risk = choice("Surgical Risk Level", {"low": "low", "high": "high", "moderate": "moderate"})
            severe_comorbidities = st.checkbox("Severe Comorbidities Present", value=False)
            frailty = st.checkbox("Patient Frailty Present", value=False)
            limited_life_expectancy = st.checkbox("Limited Life Expectancy", value=False)
            decline_intervention = st.checkbox("Patient Declined Intervention", value=False)

        # --- SECTION 2: ANATOMICAL MEASUREMENTS ---
        with st.expander("📐 2. Anatomical & Hemodynamic Measurements", expanded=True):
            st.markdown("**Main Aortic Metrics:**")
            
            # Numbers are unknown (None) until their 'Provide' toggle is ticked (three-valued logic)
            aortic_diameter = optional_number("Aortic Diameter", "aortic_diameter", min_value=0.0, max_value=16.0, value=5.0, step=0.1)
            systolic_pressure_gradient = optional_number("Systolic Pressure Gradient", "systolic_pressure_gradient", min_value=0.0, max_value=150.0, value=20.0, step=1.0)

            st.markdown("**Secondary Vessels & Structure Dimensions:**")
            vessel_diameter = optional_number("Vessel Diameter", "vessel_diameter", min_value=0.0, max_value=10.0, value=3.0, step=0.1)
            transverse_vessel_diameter = optional_number("Transverse Vessel Diameter", "transverse_vessel_diameter", min_value=0.0, max_value=10.0, value=3.0, step=0.1)
            accessory_renal_diameter = optional_number("Accessory Renal Artery Diameter", "accessory_renal_diameter", min_value=0.0, max_value=50.0, value=4.0, step=0.1)
            diverticulum_aorta_distance = optional_number("Diverticulum to Aorta Distance", "diverticulum_aorta_distance", min_value=0.0, max_value=10.0, value=5.5, step=0.1)
            sac_growth = optional_number("Aneurysm Sac Growth", "sac_growth", min_value=0.0, max_value=5.0, value=1.0, step=0.1)

        # --- SECTION 3: CORE PATHOLOGIES & CLASSIFICATIONS ---
        with st.expander("🫀 3. Pathology Type & Severity Classifications", expanded=False):
            st.markdown("**Primary Diagnoses:**")
            aortic_dissection_type = choice("Aortic Dissection Type", {"None": False, "acute_type_B": "acute_type_B"})
            
            aortic_coarctation = st.checkbox("Aortic Coarctation")
            mycotic_aneurysm = st.checkbox("Mycotic Aneurysm")
            thoracic_aortic_disease = st.checkbox("Thoracic Aortic Disease")
            pau_or_imh = st.checkbox("Penetrating Aortic Ulcer (PAU) or Intramural Hematoma (IMH)")
            
            st.markdown("**Status & Timings:**")
            chronic_type_class = choice("Chronic Type Classification", {"None": False, "B": "B"})
            complicated_status_val = choice("Complicated Status", YES_NO)
            rupture = st.checkbox("Aortic Rupture Present")
            malperfusion = st.checkbox("Malperfusion Syndrome Present")
            haemodynamically_stable = st.checkbox("Haemodynamically Stable", value=True)
            early_aortic_expansion = st.checkbox("Early Aortic Expansion")
            
            st.markdown("**Blunt Traumatic Aortic Injury (BTAI):**")
            btai_status = st.checkbox("BTAI Active")
            # Rules store the grade as a number (1.0, 2.0, 3.0)
            btai_grade_val = choice("BTAI Grade (ESVS)", {g: float(g) for g in ["0", "1", "2", "3", "4"]})
            high_risk_features_val = choice("BTAI High-Risk Features Present", YES_NO)
            traumatic_brain_injury = st.checkbox("Concomitant Traumatic Brain Injury")

        # --- SECTION 4: LOCATION & MORPHOLOGY ---
        with st.expander("🗺️ 4. Location & Specific Morphologies", expanded=False):
            descending_thoracic = st.checkbox("Descending Thoracic Location")
            
            abdominal_thoracic_val = choice("Abdominal-Thoracic Involvement", YES_NO)
            aneurysm_morphology_val = choice("Aneurysm Morphology", {"fusiform": "fusiform", "saccular": "saccular", "Other": "other"})
            
            st.markdown("**Specific Structural Signs:**")
            mural_thrombus_status_val = choice("Mural Thrombus Status", {"None": "none", "asymptomatic": "asymptomatic", "symptomatic": "symptomatic"})
            
            floating_thrombus = st.checkbox("Floating Thrombus Identified")
            shaggy_aorta = st.checkbox("Shaggy Aorta Present")
            infected = st.checkbox("Infected / Septic Aorta Status")
            active_aortitis = st.checkbox("Active Aortitis")
            
            st.markdown("**Diverticula & Congenital Anomalies:**")
            kommerell_diverticulum = st.checkbox("Kommerell's Diverticulum")
            d_diverticulum_adjacent_aorta = optional_number("Diverticulum + Adjacent Aorta Diameter", "d_diverticulum_adjacent_aorta", min_value=0.0, max_value=10.0, value=5.5, step=0.1)
            aberrant_subclavian_artery = st.checkbox("Aberrant Subclavian Artery")

        # --- SECTION 5: CLINICAL FINDINGS & CLINICAL PHASES ---
        with st.expander("🏥 5. Clinical Findings, Complications & Phases", expanded=False):
            upper_limb_hypertension = st.checkbox("Upper Limb Hypertension")
            left_ventricular_hypertrophy = st.checkbox("Left Ventricular Hypertrophy (LVH)")
            compression_symptoms = st.checkbox("Compression Symptoms")
            bleeding_risk = st.checkbox("High Bleeding Risk")
            organized_haematoma = st.checkbox("Organized Haematoma Present")
            inadequate_percutaneous_drainage = st.checkbox("Inadequate Percutaneous Drainage")
            
            st.markdown("**Clinical Workflow Phases:**")
            pre_op_phase = st.checkbox("Pre-operative Phase")
            post_op_phase = st.checkbox("Post-operative Phase")
            hypotension_during_imaging = st.checkbox("Hypotension Experienced During Pre-op Imaging")

        # --- SECTION 6: SURGICAL STRATEGIES & MATERIALS ---
        with st.expander("🛠️ 6. Procedures, Coverage & Materials", expanded=False):
            repair_type_val = choice("Intended Repair Type", {"None": "none", "open": "open", "endovascular": "endovascular", "TEVAR": "TEVAR", "complex_endovascular": "complex_endovascular"})
            repair_timing_val = choice("Repair Timing Strategy", {"elective": "elective", "urgent": "urgent"})
            
            endovascular_feasible = st.checkbox("Endovascular Treatment Structurally Feasible", value=True)
            unsuitable_for_endovascular = st.checkbox("Unsuitable for Endovascular Approach")
            
            st.markdown("**Device Deployment & Coverage Criteria:**")
            lsa_coverage = st.checkbox("Left Subclavian Artery (LSA) Coverage Required")
            coeliac_coverage = st.checkbox("Coeliac Artery Coverage Required")
            extensive_repair = st.checkbox("Extensive Aortic Repair Planned")
            balloon_moulding = st.checkbox("Balloon Moulding Employed")
            excessive_oversizing = st.checkbox("Excessive Stent Graft Oversizing Required")
            
            endoleak_type_val = choice("Identified Endoleak Type", {"None": "none", "I": "I", "II": "II", "III": "III", "IV": "IV"})
            
            st.markdown("**Collateral Status:**")
            sma_collateral_flow_val = choice("Superior Mesenteric Artery (SMA) Collateral Flow", {"Intact/Sufficient": "sufficient", "insufficient": "insufficient"})
            cad_status_val = choice("Coronary Artery Disease (CAD) Status", {"None": "none", "symptomatic": "symptomatic", "significant_asymptomatic": "significant_asymptomatic"})

        # --- SECTION 7: GENETICS & BACKGROUND ---
        with st.expander("🧬 7. Genetic Conditions & Family History", expanded=False):
            genetic_aortopathy = st.checkbox("Confirmed Genetic Aortopathy")
            marfan = st.checkbox("Marfan Syndrome")
            loeys_dietz = st.checkbox("Loeys-Dietz Syndrome")
            syndromic_features = st.checkbox("Syndromic Features Identified")
            family_history_aortopathy = st.checkbox("Family History of Aortopathy / Dissection")

        # Submit button to trigger evaluation
        submit_btn = st.form_submit_button("Run Engine Inference", type="primary")

# ========================================================
# PATIENT DATA COMPILATION OBJECT
# ========================================================
# Construct the structured payload using the sanitized widget variables
patient_data = {
    "aortic_dissection": aortic_dissection_type, 
    "complicated_status": complicated_status_val, 
    "rupture": rupture,
    "malperfusion": malperfusion, 
    "unsuitable_for_endovascular": unsuitable_for_endovascular,
    "pre_op_phase": pre_op_phase, 
    "descending_thoracic": descending_thoracic,
    "hypotension_during_imaging": hypotension_during_imaging, 
    "abdominal_thoracic": abdominal_thoracic_val,
    "endovascular_feasible": endovascular_feasible, 
    "btai_grade": btai_grade_val, 
    "high_risk_features": high_risk_features_val,
    "post_op_phase": post_op_phase, 
    "endoleak_type": endoleak_type_val, 
    "genetic_aortopathy": genetic_aortopathy,
    "open_surgery_contraindicated": open_surgery_contraindicated, 
    "surgical_risk": surgical_risk,
    "repair_timing": repair_timing_val, 
    "mycotic_aneurysm": mycotic_aneurysm, 
    "aortic_diameter": aortic_diameter,
    "chronic_type": chronic_type_class, 
    "marfan": marfan, 
    "loeys_dietz": loeys_dietz,
    "systolic_pressure_gradient": systolic_pressure_gradient, 
    "upper_limb_hypertension": upper_limb_hypertension,
    "left_ventricular_hypertrophy": left_ventricular_hypertrophy, 
    "vessel_diameter": vessel_diameter,
    "d_diverticulum_adjacent_aorta": d_diverticulum_adjacent_aorta, 
    "repair_type": repair_type_val, 
    "cad_status": cad_status_val,
    "shaggy_aorta": shaggy_aorta, 
    "thoracic_aortic_disease": thoracic_aortic_disease, 
    "unfit_for_repair": unfit_for_repair,
    "severe_comorbidities": severe_comorbidities, 
    "frailty": frailty, 
    "limited_life_expectancy": limited_life_expectancy,
    "decline_intervention": decline_intervention, 
    "lsa_coverage": lsa_coverage, 
    "extensive_repair": extensive_repair,
    "early_aortic_expansion": early_aortic_expansion, 
    "balloon_moulding": balloon_moulding,
    "excessive_oversizing": excessive_oversizing, 
    "pau_or_imh": pau_or_imh, 
    "aneurysm_morphology": aneurysm_morphology_val,
    "infected": infected, 
    "fit_for_repair": fit_for_repair, 
    "coeliac_coverage": coeliac_coverage,
    "sma_collateral_flow": sma_collateral_flow_val, 
    "bleeding_risk": bleeding_risk, 
    "accessory_renal_diameter": accessory_renal_diameter,
    "haemodynamically_stable": haemodynamically_stable, 
    "organized_haematoma": organized_haematoma,
    "inadequate_percutaneous_drainage": inadequate_percutaneous_drainage, 
    "traumatic_brain_injury": traumatic_brain_injury,
    "btai_status": btai_status, 
    "sac_growth": sac_growth, 
    "age": age, 
    "family_history_aortopathy": family_history_aortopathy,
    "syndromic_features": syndromic_features, 
    "mural_thrombus_status": mural_thrombus_status_val,
    "floating_thrombus": floating_thrombus, 
    "active_aortitis": active_aortitis, 
    "aortic_coarctation": aortic_coarctation,
    "aberrant_subclavian_artery": aberrant_subclavian_artery, 
    "compression_symptoms": compression_symptoms,
    "transverse_vessel_diameter": transverse_vessel_diameter, 
    "kommerell_diverticulum": kommerell_diverticulum,
    "diverticulum_aorta_distance": diverticulum_aorta_distance
}

# ========================================================
# RIGHT COLUMN: REFACTORIZED ENGINE OUTPUT RESPONSE
# ========================================================
with col_output:
    st.header("⚡ Inference Engine Output")
    
    # The JSON input wins when its button was pressed, otherwise use the form
    run_patient = json_patient if json_patient is not None else (patient_data if submit_btn else None)

    if run_patient is not None:
        st.caption("Source: pasted JSON" if json_patient is not None else "Source: form")
        # PURE PYTHON INFERENCE MOTOR EXECUTION
        response = execute_engine(run_patient, engine_db, df_reglas)
        
        status = response.get("status")
        recs = response.get("recommendations", [])
        alerts = response.get("conditional_alerts", [])
        
        # 1. STATUS RENDERING
        if status == "EMERGENCY":
            st.error(f"🚨 STATUS: {status} (Safety Short-Circuit Activated)")
        elif status == "STABLE_RECOMMENDATIONS":
            st.success(f"✅ STATUS: {status} (Elective Evaluation Completed)")
        else:
            st.info(f"ℹ️ STATUS: {status}")
            
        st.divider()
        
        # 2. ACTIVE RECOMMENDATIONS RENDERING ('REPAIR')
        st.subheader("🎯 Surgical Guidelines / Recommendations")
        if recs:
            for r in recs:
                with st.container(border=True):
                    st.markdown(f"**Rule {r['id']}** · Recommendation {r['rec']} · "
                                f"ESVS class **{r['class']}** · Evidence **{r['evidence']}**")
                    if r['intervention'] == "REPAIR":
                        st.html('<span style="background-color:#ff4b4b; color:white; padding:3px 8px; border-radius:5px; font-weight:bold;">REPAIR</span>')
                    else:
                        st.html('<span style="background-color:#777; color:white; padding:3px 8px; border-radius:5px; font-weight:bold;">NON-SURGICAL</span>')
                    st.write(rule_text(r['id'], r['rec']))
        else:
            st.write("_No surgical activation rules met 100% of the required criteria._")
            
        st.divider()
        
        # 3. CONDITIONAL ALERTS RENDERING (Three-Valued Logic)
        st.subheader("⚠️ Conditional Alerts (Missing Data)")
        if alerts:
            st.warning("The patient could be a candidate for intervention if the following variables are provided:")
            for a in alerts:
                with st.expander(f"Pending Rule: {a['id']}"):
                    st.write(f"**Latent recommendation {a['rec']}:** {rule_text(a['id'], a['rec'])}")
                    st.write("**Required clinical variables not provided or null:**")
                    for var in a['missing_vars']:
                        st.markdown(f"- `{var}`")
        else:
            st.write("_No latent conditional alerts found for this clinical scenario._")
            
        st.divider()
        
        # 4. DEBUGGING LAYER
        with st.expander("🔍 View Engine Response JSON (API Structure)"):
            st.json(to_jsonable(response))
            
    else:
        st.info("Configure the patient variables on the left panel and click 'Run Engine Inference', or paste a patient JSON, to process the case.")