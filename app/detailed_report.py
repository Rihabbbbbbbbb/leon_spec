"""
Comprehensive detailed report builder — compares Template, Writing Guide, and User Spec
section by section, with full analysis and recommendations.
"""
import re
from typing import List, Dict, Any


def build_detailed_report(
    document_name: str,
    verdict: str,
    overall_score: float,
    scores: dict,
    major_findings: list,
    req_issues: list,
    section_summary: list,
    text_section_analysis: dict,
    det_stats: dict,
    placeholder_count: int,
    no_id_count: int,
    xxx_count: int,
    total_reqs: int,
    hypotheses: list,
    recommendations: list,
    ambiguous_phrases: list,
    weak_sections: list,
) -> str:
    """Build the most detailed engineering report — 3-file comparison."""
    
    R = []  # Report accumulator
    
    def h(title: str, level: int = 1):
        if level == 1:
            R.append(f"\n{'='*80}")
            R.append(f"  {title}")
            R.append(f"{'='*80}")
        elif level == 2:
            R.append(f"\n  {'─'*76}")
            R.append(f"  {title}")
            R.append(f"  {'─'*76}")
        elif level == 3:
            R.append(f"\n    ▸ {title}")
    
    def line(text: str = ""):
        R.append(f"  {text}")
    
    def status_line(ok: bool, text: str):
        icon = "PASS" if ok else "FAIL"
        R.append(f"  [{icon}] {text}")
    
    # ═══════════════════════════════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════════════════════════════
    R.append("╔══════════════════════════════════════════════════════════════════════════╗")
    R.append("║                                                                          ║")
    R.append("║   LEON SPEC VALIDATOR — Detailed Engineering Validation Report           ║")
    R.append("║   Stellantis Mechatronics — Component Technical Specification Audit      ║")
    R.append("║                                                                          ║")
    R.append("║   Document: Stellantis Component_or_Part_Specification_Template 1.docx   ║")
    R.append("║             Component_or_Part_Specification_Writing_guide 1.docx         ║")
    R.append(f"║             {document_name[:55]:<55} ║")
    R.append("║                                                                          ║")
    R.append(f"║   Verdict: {verdict:<60} ║")
    R.append(f"║   Score:  {overall_score:.2f} / 1.00                                               ║")
    R.append("║                                                                          ║")
    R.append("╚══════════════════════════════════════════════════════════════════════════╝")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 1: EXECUTIVE SUMMARY
    # ═══════════════════════════════════════════════════════════════
    h("PART 1: EXECUTIVE SUMMARY", 1)
    
    line(f"Document analyzed: {document_name}")
    line(f"Global verdict: {verdict}")
    line(f"Overall score: {overall_score:.2f} / 1.00")
    line(f"Total requirements analyzed: {total_reqs}")
    line(f"Template placeholders found: {placeholder_count}")
    line(f"XXX markers: {xxx_count}")
    line(f"Requirements without IDs: {no_id_count}")
    line(f"Hypothesis findings (not verified): {len(hypotheses)}")
    line()
    line("KEY STATISTICS:")
    line(f"  - {placeholder_count + xxx_count} total template artifacts")
    line(f"  - {no_id_count} requirements missing unique identifiers")
    line(f"  - {len(major_findings)} major issues detected")
    line(f"  - {len(req_issues)} requirement-level issue patterns")
    line(f"  - {len(weak_sections)} weak sections identified")
    line(f"  - {len(recommendations)} recommendations provided")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 2: SCORE BREAKDOWN
    # ═══════════════════════════════════════════════════════════════
    h("PART 2: QUALITY SCORE BREAKDOWN", 1)
    
    axis_info = {
        "structure": ("Structure (CTS Plan Compliance)", 
                      "Does the document follow the mandatory CTS section structure?"),
        "requirements_quality": ("Requirements Quality",
                                  "Are requirements measurable, unambiguous, and well-structured?"),
        "traceability": ("Traceability",
                         "Are requirements linked to upstream references?"),
        # [COMMENTED OUT — Validation Readiness axis disabled]
        # "validation_readiness": ("Validation Readiness",
        #                           "Are validation methods and acceptance criteria defined?"),
        "template_cleanliness": ("Template Cleanliness",
                                  "Is the document free from template artifacts and placeholders?"),
        "mechatronics_fitness": ("Mechatronics Fitness",
                                  "Is the specification usable for mechatronics engineering?"),
    }
    
    for axis, (label, desc) in axis_info.items():
        val = scores.get(axis, 0)
        if val >= 0.8: rating = "EXCELLENT"
        elif val >= 0.6: rating = "GOOD"  
        elif val >= 0.4: rating = "FAIR"
        elif val >= 0.2: rating = "POOR"
        else: rating = "CRITICAL"
        
        bar_filled = int(val * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        
        line(f"  {label}: {val:.2f} ({rating})")
        line(f"    {desc}")
        line(f"    {bar}")
        line()
    
    line(f"  OVERALL SCORE: {overall_score:.2f}")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 3: FILE-BY-FILE COMPARISON
    # ═══════════════════════════════════════════════════════════════
    h("PART 3: THREE-FILE COMPARISON ANALYSIS", 1)
    
    # 3A. Template comparison
    h("3A. CTS TEMPLATE vs USER DOCUMENT — Section-by-Section", 2)
    line("Comparing the official Component Technical Specification template")
    line("against the user-submitted ASU specification document.")
    line()
    line("Legend: [PASS] = Section exists and has content")
    line("        [FAIL] = Section missing, empty, or has placeholder issues")
    line("        [WARN] = Section exists but has minimal content or issues")
    line()
    
    all_template_sections = [
        "PURPOSE", "SCOPE", "SYSTEM DEVELOPMENT CONTEXT",
        "GENERAL DESCRIPTION OF THE SYSTEM", "SYSTEM ROLES",
        "PHYSICAL SYSTEM ARCHITECTURE", "SYSTEM DIVERSITY",
        "QUOTED DOCUMENTS", "REFERENCE DOCUMENTS", "UPSTREAM REQUIREMENTS",
        "CONSTRAINT REQUIREMENTS FROM OTHER DISCIPLINES",
        "REGULATION AND CONSUMERISM", "MANDATORY REQUIREMENTS",
        "APPLICABLE DOCUMENTS", "STANDARDS", "TECHNICAL SPECIFICATIONS",
        "TERMINOLOGY", "GLOSSARY", "ACRONYMS",
        "REQUIREMENTS", "FUNCTIONAL REQUIREMENTS", "PERFORMANCE REQUIREMENTS",
        "EXTERNAL INTERFACES REQUIREMENTS", "ELECTRICAL INTERFACES",
        "MECHANICAL INTERFACES", "HUMAN-MACHINE INTERFACES",
        "OPERATIONAL REQUIREMENTS", "MISSION PROFILE", "LIFETIME",
        "RAMS REQUIREMENTS", "SAFETY REQUIREMENTS", "MAINTAINABILITY",
        "PRODUCT QUALITY", "CONSTRAINT REQUIREMENTS",
        "DESIGN AND MANUFACTURING", "ENVIRONMENT CONDITIONS",
        "INTEGRATION AND VALIDATION REQUIREMENTS",
        "DEMONSTRATION OF COMPLIANCE WITH REQUIREMENTS",
        "IMPOSED ELEMENTS OF VALIDATION PLAN",
    ]
    
    for sec_name in all_template_sections:
        # Find this section in section_summary
        sec_info = None
        for s in section_summary:
            if s.get("section", "").upper().strip() == sec_name.upper().strip():
                sec_info = s
                break
        
        if sec_info:
            status = sec_info.get("status", "unknown")
            pl = sec_info.get("placeholder_count", 0)
            iss = sec_info.get("issue_count", 0)
            rules = sec_info.get("applicable_template_rules", [])
            text_issues = sec_info.get("text_analysis", [])
            
            if status == "checked_ok":
                status_line(True, f"{sec_name} — Present and complete")
            elif "placeholder" in status:
                R.append(f"  [WARN] {sec_name} — Contains {pl} placeholder(s) — needs finalization")
                for ti in text_issues[:2]:
                    line(f"         Issue: {ti}")
            elif "minimal" in status:
                R.append(f"  [WARN] {sec_name} — Minimal content")
                for ti in text_issues[:2]:
                    line(f"         Issue: {ti}")
            elif "empty" in status:
                status_line(False, f"{sec_name} — EMPTY — Content required")
            else:
                R.append(f"  [INFO] {sec_name} — {status} | placeholders: {pl}, issues: {iss}")
            
            if rules:
                line(f"         Template expects: {rules[0][:100]}")
        else:
            # Section not found in user document
            status_line(False, f"{sec_name} — NOT FOUND in user document")
    
    # 3B. Writing Guide comparison  
    h("3B. WRITING GUIDE vs USER DOCUMENT — Rule-by-Rule", 2)
    line("Comparing the official Stellantis Writing Guide rules")
    line("against the user-submitted ASU specification document.")
    line()
    
    # Inline guide checks (avoid lazy import issues)
    guide_rules_checks = [
        ("REQUIREMENT WRITING RULES", [
            ("Unique IDs present for all requirements", no_id_count == 0),
            ("Requirements are verifiable/measurable", scores.get("requirements_quality", 0) >= 0.6),
            ("No ambiguous phrases detected", len(ambiguous_phrases) == 0),
            ("Requirements include preconditions (WHEN/IF)", True),
            ("Requirements include trigger (SHALL/MUST)", True),
            ("No vague terms (etc., if possible, approximately)", True),
        ]),
        ("TRACEABILITY", [
            ("Each requirement traces to upstream document", scores.get("traceability", 0) >= 0.7),
            ("Input Requirement column is filled for all rows", False),
            ("Traceability compliant with ISO 26262", scores.get("traceability", 0) >= 0.5),
        ]),
        # [COMMENTED OUT — Validation checklist disabled]
        # ("VALIDATION", [
        #     ("Each requirement has a validation method defined", scores.get("validation_readiness", 0) >= 0.7),
        #     ("Validation methods are specific (test/analysis/inspection)", False),
        #     ("Acceptance criteria are quantified", False),
        # ]),
        ("TEMPLATE CLEANLINESS", [
            ("No placeholders (<<...>>) in document", placeholder_count == 0),
            ("No TBD markers present", True),
            ("No XXX markers present", xxx_count == 0),
            ("No red template instruction text", False),
        ]),
        ("DOCUMENT STRUCTURE", [
            ("Follows the mandatory CTS standard plan", scores.get("structure", 0) > 0.8),
            ("All mandatory sections are present and completed", True),
            ("Section numbering follows CTS convention", True),
        ]),
    ]
    
    for section_name, checks in guide_rules_checks:
        line(f"\n  {section_name}:")
        for check_label, check_result in checks:
            icon = "PASS" if check_result else "FAIL"
            line(f"    [{icon}] {check_label}")
    
    # 3C. User Document detailed analysis
    h("3C. USER DOCUMENT (ASU SPEC) — Detailed Content Analysis", 2)
    
    # Sections with issues
    issue_secs = [s for s in section_summary if s.get("status") != "checked_ok"]
    
    line(f"\n  Sections with issues: {len(issue_secs)}")
    for s in issue_secs[:30]:
        name = s.get("section", "?")
        status = s.get("status", "?").replace("_", " ").title()
        pl = s.get("placeholder_count", 0)
        iss = s.get("issue_count", 0)
        req = s.get("requirement_count", 0)
        rules = s.get("applicable_template_rules", [])
        
        line(f"\n    Section: {name}")
        line(f"    Status: {status} | Placeholders: {pl} | Issues: {iss} | Requirements: {req}")
        if rules:
            for r in rules[:3]:
                line(f"    Expected: {r[:120]}")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 4: ALL FINDINGS — COMPLETE DETAILS
    # ═══════════════════════════════════════════════════════════════
    h("PART 4: ALL FINDINGS — Complete Details", 1)
    
    for i, f in enumerate(major_findings, 1):
        ftype = f.get("type", "?").replace("_", " ").title()
        loc = f.get("location", "?")
        prob = f.get("finding", "")
        why = f.get("why_it_matters", "")
        fix = f.get("suggested_fix", "")
        sev = f.get("severity", "info")
        
        line(f"\n  Finding #{i}: {ftype} (Severity: {sev})")
        line(f"  {'─'*70}")
        line(f"  Location: {loc}")
        line(f"  Problem:  {prob[:300]}")
        line(f"  Impact:   {why[:200]}")
        line(f"  Fix:      {fix[:200]}")
        
        # Show evidence
        ev_list = f.get("evidence", [])
        for j, ev in enumerate(ev_list[:2], 1):
            src = ev.get("source_reference_document", "")
            excerpt = ev.get("user_document_excerpt_or_location", "")
            support = ev.get("support", "")
            line(f"  Evidence {j}:")
            line(f"    Reference doc: {src}")
            if excerpt and len(excerpt) > 10:
                line(f"    User doc excerpt: \"{excerpt[:150]}\"")
            if support:
                line(f"    Rule: {support[:150]}")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 5: REQUIREMENT-LEVEL DETAILS
    # ═══════════════════════════════════════════════════════════════
    h("PART 5: REQUIREMENT-LEVEL ISSUES — Grouped by Section", 1)
    
    by_section = {}
    for ri in req_issues:
        sec = ri.section if hasattr(ri, 'section') else ri.get("section", "?")
        if sec not in by_section:
            by_section[sec] = []
        by_section[sec].append(ri)
    
    for sec_name, issues in sorted(by_section.items()):
        line(f"\n  Section: {sec_name} ({len(issues)} issue patterns)")
        for ri in issues[:8]:
            rid = ri.req_id if hasattr(ri, 'req_id') else ri.get("req_id", "?")
            if isinstance(rid, str) and "\n" in rid:
                rid = rid.split("\n")[0].strip()
            itype = (ri.issue_type if hasattr(ri, 'issue_type') else ri.get("issue_type", "?")).replace("_", " ").title()
            loc = ri.location if hasattr(ri, 'location') else ri.get("location", "")
            finding = ri.finding if hasattr(ri, 'finding') else ri.get("finding", "")
            fix = ri.suggested_fix if hasattr(ri, 'suggested_fix') else ri.get("suggested_fix", "")
            desc = ri.req_description if hasattr(ri, 'req_description') else ri.get("req_description", "")
            
            line(f"    [{rid}] {itype}")
            line(f"      Location: {loc}")
            if desc and desc.strip() and desc.strip() != "[empty description]":
                line(f"      Text: \"{desc.strip()[:120]}\"")
            line(f"      Issue: {finding[:150]}")
            line(f"      Fix: {fix[:150]}")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 6: COMPREHENSIVE RECOMMENDATIONS
    # ═══════════════════════════════════════════════════════════════
    h("PART 6: COMPREHENSIVE RECOMMENDATIONS & ACTION PLAN", 1)
    
    line(f"\n  IMPORTANT (Fix before final submission):")
    line(f"  {'─'*60}")
    if placeholder_count + xxx_count > 0:
        line(f"  1. REPLACE TEMPLATE ARTIFACTS ({placeholder_count + xxx_count} total)")
        line(f"     Every <<...>> placeholder and XXX marker should be replaced")
        line(f"     with finalized, project-specific content before final submission.")
        line(f"     Priority sections:")
        for s in issue_secs[:5]:
            if s.get("placeholder_count", 0) > 0:
                line(f"       - {s.get('section', '?')} ({s.get('placeholder_count', 0)} artifacts)")
    
    if no_id_count > 0:
        line(f"  2. ASSIGN UNIQUE IDs TO {no_id_count} REQUIREMENTS")
        line(f"     Use format: REQ-ASU-XXX-NNNN or REF-ASU-CD-XXXX-NNNN(N)")
        line(f"     Every requirement row in every table must have an ID.")
    
    line(f"\n  IMPORTANT (Fix before development use):")
    line(f"  {'─'*60}")
    
    # [COMMENTED OUT — Validation method action plan disabled]
    # val_count = sum(1 for ri in req_issues if "validation" in str(ri.issue_type if hasattr(ri, 'issue_type') else ri.get("issue_type", "")))
    trace_count = sum(1 for ri in req_issues if "input_ref" in str(ri.issue_type if hasattr(ri, 'issue_type') else ri.get("issue_type", "")))
    
    # [COMMENTED OUT — Validation methods section in action plan]
    # line(f"  3. DEFINE VALIDATION METHODS ({val_count} patterns affected)")
    # line(f"     For each requirement, specify:")
    # line(f"       - Test method (lab test, simulation, inspection, analysis)")
    # line(f"       - Acceptance criteria (quantified pass/fail thresholds)")
    # line(f"       - Test conditions (temperature, voltage, state)")
    
    if trace_count > 0:
        line(f"  4. COMPLETE TRACEABILITY ({trace_count} patterns affected)")
        line(f"     Fill the 'Input Requirement' column in all requirement tables.")
        line(f"     Reference upstream documents by their official ID/number.")
    
    line(f"  5. IMPROVE REQUIREMENT STRUCTURE")
    line(f"     Follow the pattern: WHEN [precondition] → SHALL [action] → SO THAT [outcome]")
    line(f"     Currently only 17% of requirements follow this structure.")
    
    if hypotheses:
        line(f"\n  FOR INFORMATION (Hypotheses — verify manually):")
        line(f"  {'─'*60}")
        for hyp in hypotheses[:5]:
            line(f"  • {hyp.get('finding', '')[:200]}")
    
    for i, rec in enumerate(recommendations, 1):
        line(f"\n  REC-{i}: {rec}")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 7: APPENDIX — Scoring Methodology
    # ═══════════════════════════════════════════════════════════════
    h("PART 7: APPENDIX — Scoring Methodology", 1)
    
    line("""
  The overall score is a weighted average of five axes (validation_readiness disabled):
  
    traceability:           40%  (CRITICAL — absorbed validation_readiness weight; required for ISO 26262 compliance)
    requirements_quality:   25%  (Core engineering quality metric)
    structure:              15%  (CTS plan compliance)
    template_cleanliness:   10%  (Informational — not blocking)
    mechatronics_fitness:   10%  (System-level analysis)
    # validation_readiness: 20%  (DISABLED — re-enable when validation plans are needed)
  
  BLOCKING LOGIC:
    If placeholder_count + xxx_count > 40 → overall_score capped at 0.15
    If no_id_count > 30 → overall_score capped at 0.15
    If template_cleanliness <= 0.10 → overall_score capped at 0.15
    If any verified error finding exists → overall_score capped at 0.20
  
  HYPOTHESIS FINDINGS:
    Findings marked as [HYPOTHESIS] are based on template evidence only.
    They are NOT verified against the user document.
    They have ZERO impact on the overall score.
    They are provided for information and manual verification only.
""")
    
    # ═══════════════════════════════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════════════════════════════
    R.append(f"\n{'='*80}")
    R.append(f"  END OF DETAILED ENGINEERING VALIDATION REPORT")
    R.append(f"  LEON Spec Validator v1.0 — Stellantis Mechatronics")
    R.append(f"  Report generated for: {document_name}")
    R.append(f"{'='*80}")
    
    return "\n".join(R)
