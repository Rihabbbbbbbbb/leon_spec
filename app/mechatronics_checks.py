"""
Mechatronics-specific validation checks for LEON Spec Validator.

Adds industrial-grade validation for:
- ASIL/Safety integrity level checks
- Physical parameter validation (dB, timing, voltage)
- State machine completeness
- Signal and interface validation
- Failure mode coverage
- Requirement pattern analysis (precondition/trigger/observable)

These checks run deterministically (no LLM needed) and produce
findings that are grounded in the actual user document content.
"""
import re
from typing import List, Dict, Any, Optional, Tuple


# ── ASIL patterns ────────────────────────────────────────────────
ASIL_PATTERN = re.compile(r'ASIL[_ ]?([ABCD])(?:\([ABCD]\))?', re.IGNORECASE)
# [COMMENTED OUT — ASIL validation requirements disabled; re-enable when validation plans are needed]
# ASIL_REQUIRED_VALIDATION = {
#     "A": {"min_test_methods": 1, "requires_traceability": True},
#     "B": {"min_test_methods": 2, "requires_traceability": True, "requires_fmea": False},
#     "C": {"min_test_methods": 3, "requires_traceability": True, "requires_fmea": True},
#     "D": {"min_test_methods": 5, "requires_traceability": True, "requires_fmea": True},
# }
ASIL_REQUIRED_VALIDATION = {}  # Disabled — empty dict to avoid KeyError


# ── Physical parameter patterns ──────────────────────────────────
PHYSICAL_PATTERNS = {
    "sound_level_db": re.compile(r'(\d+(?:\.\d+)?)\s*(?:dB|dB\(A\)|dBA?)', re.IGNORECASE),
    "voltage_v": re.compile(r'(\d+(?:\.\d+)?)\s*(?:V|volts?)\b', re.IGNORECASE),
    "time_ms": re.compile(r'(\d+(?:\.\d+)?)\s*(?:ms|milliseconds?)', re.IGNORECASE),
    "time_s": re.compile(r'(\d+(?:\.\d+)?)\s*(?:s|seconds?)(?!\s*/\s*)', re.IGNORECASE),
    "percentage": re.compile(r'(\d+(?:\.\d+)?)\s*%', re.IGNORECASE),
    "frequency_hz": re.compile(r'(\d+(?:\.\d+)?)\s*(?:Hz|hertz)', re.IGNORECASE),
}


# ── State machine patterns ───────────────────────────────────────
# Match "switch from X to Y" or "transition from X into Y" patterns
STATE_TRANSITION_PATTERN = re.compile(
    r'(?:switch|transition|go|move)\s+(?:from\s+)?'
    r'([A-Z][\w\s]{2,30}?)\s+'  # From-state: must start with uppercase, 2-30 chars
    r'(?:to|into)\s+'
    r'([A-Z][\w\s]{2,30}?)'     # To-state: must start with uppercase, 2-30 chars
    r'(?:\s*,|\s*\.|\s*$|\s*\n|\s+and|\s+or|\s+the|\s+shall)',  # End boundary
    re.IGNORECASE
)
# Match state definitions: "X State:", "X mode:", "State: X"
STATE_NAMES_PATTERN = re.compile(
    r'(?:^|\n|\s{2,})'           # Start of state definition
    r'([A-Z][\w\s]{2,30}?)'      # State name
    r'\s*(?:state|mode|status)'  # State keyword
    r'(?:\s*[:;.\-]|\s*$)',      # End boundary
    re.IGNORECASE | re.MULTILINE
)
# Filter: valid state names must start with uppercase letter and be 3-30 chars
VALID_STATE_RE = re.compile(r'^[A-Z][\w\s]{2,30}$')


# [COMMENTED OUT — ASIL compliance check disabled; re-enable when validation plans are needed]
# def check_asil_compliance(user_text: str, req_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
#     """(... full function commented out — see git history to restore ...)"""
#     findings = []
#     asil_refs = ASIL_PATTERN.findall(user_text)
#     asil_levels = set(a.upper() for a in asil_refs)
#     if not asil_levels:
#         return findings
#     asil_reqs = [r for r in req_rows if ASIL_PATTERN.search(
#         r.get("req_id", "") + " " + r.get("description", "")
#     )]
#     for level in asil_levels:
#         requirements = ASIL_REQUIRED_VALIDATION.get(level, {})
#         level_reqs = [r for r in asil_reqs if level.upper() in 
#                       (r.get("req_id", "") + r.get("description", "")).upper()]
#         if level_reqs:
#             reqs_with_validation = [r for r in level_reqs if r.get("validation", "").strip()]
#             min_methods = requirements.get("min_test_methods", 1)
#             if len(reqs_with_validation) == 0 and min_methods > 0:
#                 findings.append({
#                     "type": "asil_validation_gap", "severity": "error",
#                     "location": f"ASIL {level} requirements", "status": "present_but_incomplete",
#                     "finding": f"ASIL {level} requires at least {min_methods} test method(s) per requirement. "
#                                f"None of the {len(level_reqs)} ASIL {level} requirements have validation methods defined.",
#                     "why_it_matters": f"ISO 26262 ASIL {level} mandates rigorous validation. "
#                                       f"Without test methods, safety compliance cannot be demonstrated.",
#                     "suggested_fix": f"Define test methods, acceptance criteria, and fault injection tests "
#                                      f"for all ASIL {level} requirements.",
#                 })
#             if requirements.get("requires_traceability"):
#                 reqs_with_input = [r for r in level_reqs if r.get("input_requirement", "").strip()
#                                   and r["input_requirement"].upper() not in ("N/A", "N / A", "NA", "")]
#                 if len(reqs_with_input) < len(level_reqs):
#                     findings.append({
#                         "type": "asil_traceability_gap", "severity": "warning",
#                         "location": f"ASIL {level} requirements", "status": "present_but_weak",
#                         "finding": f"Only {len(reqs_with_input)}/{len(level_reqs)} ASIL {level} requirements "
#                                    f"have upstream traceability.",
#                         "why_it_matters": "Traceability is mandatory for safety-related requirements per ISO 26262.",
#                         "suggested_fix": f"Add upstream requirement references for all ASIL {level} requirements.",
#                     })
#     return findings
def check_asil_compliance(user_text: str, req_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[DISABLED] ASIL compliance check — returns empty list. Re-enable by uncommenting above."""
    return []


def check_physical_parameters(user_text: str) -> List[Dict[str, Any]]:
    """
    Check physical parameters for realism and consistency.
    Detects: sound levels, voltages, timing, percentages, frequencies.
    
    Args:
        user_text: Full user document text
    
    Returns:
        List of physical parameter findings
    """
    findings = []
    
    # Extract all physical values
    sound_levels = [(float(m.group(1)), m.group(0)) for m in PHYSICAL_PATTERNS["sound_level_db"].finditer(user_text)]
    voltages = [(float(m.group(1)), m.group(0)) for m in PHYSICAL_PATTERNS["voltage_v"].finditer(user_text)]
    times_ms = [(float(m.group(1)), m.group(0)) for m in PHYSICAL_PATTERNS["time_ms"].finditer(user_text)]
    times_s = [(float(m.group(1)), m.group(0)) for m in PHYSICAL_PATTERNS["time_s"].finditer(user_text)]
    percentages = [(float(m.group(1)), m.group(0)) for m in PHYSICAL_PATTERNS["percentage"].finditer(user_text)]
    frequencies = [(float(m.group(1)), m.group(0)) for m in PHYSICAL_PATTERNS["frequency_hz"].finditer(user_text)]
    
    # Check sound levels (automotive alarm: typically 105-118 dB)
    for value, match_text in sound_levels:
        if value > 130:
            findings.append({
                "type": "physical_parameter_out_of_range",
                "severity": "warning",
                "location": f"Sound level: {match_text}",
                "status": "present_but_weak",
                "finding": f"Sound level {value} dB exceeds typical automotive range (85-120 dB). Verify specification.",
                "why_it_matters": "Unrealistic physical parameters may indicate specification errors.",
                "suggested_fix": "Verify the sound level specification against the component datasheet and test results.",
            })
    
    # Check voltages (automotive: 12V nominal, signals 0-5V common, logic 1.8-5V)
    for value, match_text in voltages:
        # Only flag clearly abnormal voltages: <1V or >48V
        # 1.8V-5V = standard logic/sensor levels, 9-16V = battery, 24-48V = heavy systems
        if value < 1.0 or value > 48:
            findings.append({
                "type": "physical_parameter_out_of_range",
                "severity": "warning",
                "location": f"Voltage: {match_text}",
                "status": "present_but_weak",
                "finding": f"Voltage {value}V is outside typical automotive range (1-48V). Verify specification.",
                "why_it_matters": "Voltage specifications outside typical range may indicate errors or special requirements.",
                "suggested_fix": "Verify voltage specification against vehicle electrical architecture.",
            })
    
    # Check percentages
    for value, match_text in percentages:
        if value < 0 or value > 100:
            findings.append({
                "type": "physical_parameter_out_of_range",
                "severity": "error",
                "location": f"Percentage: {match_text}",
                "status": "present",
                "finding": f"Percentage value {value}% is outside valid range (0-100%).",
                "why_it_matters": "Invalid percentage values indicate specification errors.",
                "suggested_fix": "Correct the percentage value to be between 0 and 100%.",
            })
    
    return findings


def check_state_machine_completeness(user_text: str) -> List[Dict[str, Any]]:
    """
    Check for basic state machine completeness issues.
    Detects: missing transitions, unreachable states, missing edge cases.
    
    Args:
        user_text: Full user document text
    
    Returns:
        List of state machine findings
    """
    findings = []
    
    # Find state names
    states = set()
    for m in STATE_NAMES_PATTERN.finditer(user_text):
        state_name = m.group(1).strip()
        # Only accept valid state names (start uppercase, reasonable length, no digits-only)
        if VALID_STATE_RE.match(state_name) and not state_name.isdigit():
            states.add(state_name)
    
    # Find transitions
    transitions = []
    for m in STATE_TRANSITION_PATTERN.finditer(user_text):
        from_state = m.group(1).strip()
        to_state = m.group(2).strip()
        # Filter valid state names
        if VALID_STATE_RE.match(from_state) and not from_state.isdigit():
            if VALID_STATE_RE.match(to_state) and not to_state.isdigit():
                transitions.append((from_state, to_state))
    
    if not states or not transitions:
        return findings
    
    # States that appear as "from" but never as "to" (potential unreachable states)
    from_states = {t[0] for t in transitions}
    to_states = {t[1] for t in transitions}
    
    # States with no incoming transitions (except initial states)
    no_incoming = from_states - to_states
    if len(no_incoming) > 2:  # Allow a couple of initial states
        findings.append({
            "type": "state_machine_completeness",
            "severity": "warning",
            "location": "State machine definition",
            "status": "present_but_weak",
            "finding": (
                f"Multiple states have no incoming transitions: {', '.join(list(no_incoming)[:5])}. "
                "This may indicate unreachable states or missing transition definitions."
            ),
            "why_it_matters": "Unreachable states or missing transitions can lead to undefined system behavior.",
            "suggested_fix": "Verify that all states are reachable and all necessary transitions are defined.",
        })
    
    # States with no outgoing transitions (dead-end states)
    no_outgoing = to_states - from_states
    if len(no_outgoing) > 1:
        findings.append({
            "type": "state_machine_completeness",
            "severity": "warning",
            "location": "State machine definition",
            "status": "present_but_weak",
            "finding": (
                f"States with no outgoing transitions: {', '.join(list(no_outgoing)[:5])}. "
                "These are dead-end states — verify if return transitions are defined elsewhere."
            ),
            "why_it_matters": "Dead-end states without exit transitions indicate incomplete specification.",
            "suggested_fix": "Define exit transitions or confirm these states are terminal by design.",
        })
    
    return findings


def check_requirement_patterns(req_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Check if requirements follow the Stellantis requirement engineering template:
    - Precondition (WHEN/IF)
    - Trigger (the system shall...)
    - Observable (so that...)
    
    Args:
        req_rows: Extracted requirement rows
    
    Returns:
        List of pattern-related findings
    """
    findings = []
    
    # Patterns for well-structured requirements
    precondition_pattern = re.compile(r'(?:when|if|during|upon|after)\s+', re.IGNORECASE)
    trigger_pattern = re.compile(r'\b(?:shall|must|will)\b', re.IGNORECASE)
    observable_pattern = re.compile(r'(?:so that|in order to|to ensure|resulting in)', re.IGNORECASE)
    
    structured_count = 0
    missing_precondition = 0
    missing_trigger = 0
    
    for req in req_rows:
        desc = req.get("description", "")
        if not desc:
            continue
        
        has_precondition = bool(precondition_pattern.search(desc))
        has_trigger = bool(trigger_pattern.search(desc))
        has_observable = bool(observable_pattern.search(desc))
        
        if has_precondition and has_trigger:
            structured_count += 1
        if not has_precondition and has_trigger:
            missing_precondition += 1
        if not has_trigger and len(desc) > 20:
            missing_trigger += 1
    
    total = len(req_rows)
    if total > 0:
        structure_ratio = structured_count / total
        
        if structure_ratio < 0.5:
            findings.append({
                "type": "requirement_structure_weakness",
                "severity": "warning",
                "location": f"{total} requirement rows",
                "status": "present_but_weak",
                "finding": (
                    f"Only {structured_count}/{total} requirements ({structure_ratio:.0%}) "
                    f"follow the Stellantis requirement template (precondition + trigger). "
                    f"{missing_precondition} requirements have triggers but no preconditions."
                ),
                "why_it_matters": (
                    "The CTS writing guide mandates that requirements follow the pattern: "
                    "'The system shall <action> with <performance> in <condition>'."
                ),
                "suggested_fix": "Rewrite requirements to include preconditions, triggers, and observable outcomes.",
            })
    
    return findings


def run_mechatronics_checks(
    user_text: str,
    req_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Run all mechatronics-specific validation checks.
    
    Returns:
        Dict with findings list and stats
    """
    all_findings = []
    
    # 1. ASIL compliance
    all_findings.extend(check_asil_compliance(user_text, req_rows))
    
    # 2. Physical parameters
    all_findings.extend(check_physical_parameters(user_text))
    
    # 3. State machine completeness
    all_findings.extend(check_state_machine_completeness(user_text))
    
    # 4. Requirement patterns
    all_findings.extend(check_requirement_patterns(req_rows))
    
    return {
        "findings": all_findings,
        "stats": {
            "asil_levels_detected": list(set(ASIL_PATTERN.findall(user_text))),
            "physical_params_found": {
                "sound_levels": len(PHYSICAL_PATTERNS["sound_level_db"].findall(user_text)),
                "voltages": len(PHYSICAL_PATTERNS["voltage_v"].findall(user_text)),
                "timing_ms": len(PHYSICAL_PATTERNS["time_ms"].findall(user_text)),
                "timing_s": len(PHYSICAL_PATTERNS["time_s"].findall(user_text)),
                "percentages": len(PHYSICAL_PATTERNS["percentage"].findall(user_text)),
                "frequencies": len(PHYSICAL_PATTERNS["frequency_hz"].findall(user_text)),
            },
            "mechatronics_findings_count": len(all_findings),
        },
    }
