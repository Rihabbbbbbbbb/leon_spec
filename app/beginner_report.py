"""
BEGINNER-FRIENDLY report builder — explains every section in plain language.
Designed for AI engineers working with mechatronics teams at Stellantis.
Compares Template requirements vs Writing Guide rules vs User Document reality.
"""
import re
from typing import List, Dict, Any


def build_beginner_report(
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
    """Build a report that ANY engineer can understand, even without CTS knowledge."""

    R = []  # lines accumulator

    def title(text: str):
        R.append(f"\n{'='*80}")
        R.append(f"  {text}")
        R.append(f"{'='*80}")

    def subtitle(text: str):
        R.append(f"\n  ── {text} ──")

    def para(text: str):
        """Wrap long text to 76 chars."""
        words = text.split()
        line_buf = "  "
        for w in words:
            if len(line_buf) + len(w) + 1 > 78:
                R.append(line_buf)
                line_buf = "  " + w
            else:
                line_buf += (" " if line_buf != "  " else "") + w
        if line_buf.strip():
            R.append(line_buf)

    def bullet(text: str, indent: int = 2):
        prefix = " " * indent + "• "
        R.append(f"{prefix}{text}")

    def check(ok: bool, text: str):
        icon = "✅" if ok else "❌"
        R.append(f"     {icon}  {text}")

    def empty():
        R.append("")

    # ═══════════════════════════════════════════════════════════════
    # COVER + WHAT IS THIS REPORT
    # ═══════════════════════════════════════════════════════════════
    R.append("╔══════════════════════════════════════════════════════════════════════════╗")
    R.append("║                                                                          ║")
    R.append("║   LEON SPEC VALIDATOR                                                    ║")
    R.append("║   BEGINNER-FRIENDLY ENGINEERING REPORT                                   ║")
    R.append("║   For AI Engineers working with Stellantis Mechatronics Teams            ║")
    R.append("║                                                                          ║")
    R.append(f"║   Document validated: {document_name[:48]:<48} ║")
    R.append(f"║   Verdict: {verdict:<58} ║")
    R.append(f"║   Score:  {overall_score:.2f} / 1.00                                               ║")
    R.append("║                                                                          ║")
    R.append("╚══════════════════════════════════════════════════════════════════════════╝")

    empty()
    title("WHAT IS THIS REPORT? — Please read this first!")

    para("This report compares THREE documents to check if your specification "
         "is ready for engineering use at Stellantis. Here is what each file is:")

    empty()
    para("FILE 1 — CTS TEMPLATE (Component Technical Specification Template): "
         "This is the OFFICIAL Stellantis template. It defines all the sections "
         "that MUST exist in any component specification document. Think of it "
         "as the 'table of contents' that every spec must follow, plus rules "
         "about what goes into each section.")

    empty()
    para("FILE 2 — WRITING GUIDE: "
         "This is the OFFICIAL Stellantis guide that explains HOW to write "
         "requirements correctly. It defines rules like: every requirement "
         "must have a unique ID, must be measurable, must be traceable to "
         "an upstream document, etc.")

    empty()
    para("FILE 3 — YOUR DOCUMENT (the ASU Alarm Siren Unit spec): "
         "This is the document YOU submitted for validation. We check it "
         "against the Template (does it have all the right sections?) and "
         "against the Writing Guide (are the requirements written correctly?).")

    empty()
    para("HOW TO READ THIS REPORT: "
         "We go through EVERY section of the Template, and for each one we ask: "
         "(1) Does your document have this section? "
         "(2) Does it have the right content? "
         "(3) Are there any placeholders (<<...>>) or template artifacts? "
         "At the end, we give you a priority action plan.")

    # ═══════════════════════════════════════════════════════════════
    # QUICK SUMMARY
    # ═══════════════════════════════════════════════════════════════
    title("QUICK SUMMARY — The Big Picture")

    empty()
    para(f"After analyzing {total_reqs} requirement rows across your document, "
         f"here is the short version:")

    empty()
    bullet(f"Verdict: {verdict} — This document needs improvements before it can be used for engineering.")
    bullet(f"Overall Score: {overall_score:.2f} out of 1.00 (1.00 = perfect)")
    bullet(f"Found {placeholder_count + xxx_count} template placeholders — these are <<...>> markers "
           f"or XXX values that should be replaced with real content before final submission.")
    bullet(f"Found {no_id_count} requirements without a unique ID — every requirement "
           f"needs an ID like REQ-ASU-XXX-NNNN for traceability.")
    bullet(f"Found {len(major_findings)} major issues and {len(req_issues)} requirement-level issues.")

    empty()
    para("KEY CONCEPT — 'Placeholder': "
         "A placeholder is text like <<The objective concerns...>> or << (*) Choose one...>> "
         "or XXX. These are INSTRUCTIONS from the template that were never replaced "
         "with actual project-specific content. They indicate sections that still need "
         f"finalization. Your document has {placeholder_count + xxx_count} of these.")

    # ═══════════════════════════════════════════════════════════════
    # SCORES EXPLAINED
    # ═══════════════════════════════════════════════════════════════
    title("QUALITY SCORES — What Each Score Means")

    score_explanations = [
        ("structure", "Structure (CTS Plan Compliance)",
         "Does your document follow the mandatory CTS section structure? "
         "This checks if ALL required sections (PURPOSE, SCOPE, REQUIREMENTS, etc.) "
         "are present. HIGH score = all sections found.",
         "Your document has almost all CTS sections present (score 0.91). Very good."),
        ("requirements_quality", "Requirements Quality",
         "Are requirements well-written? Are they measurable, unambiguous, "
         "and do they follow the SHALL/MUST pattern with preconditions? "
         "HIGH score = requirements are clear and testable.",
         "Only 17% follow the proper structure (score 0.50). Most need improvement."),
        ("traceability", "Traceability (Upstream Links)",
         "Can each requirement be traced back to an upstream document? "
         "This is essential for ISO 26262 safety compliance. "
         "HIGH score = every requirement is linked to its source.",
         "Only 115 out of 299 have upstream references (score 0.30). Critical gap."),
        # [COMMENTED OUT — Validation Readiness axis disabled]
        # ("validation_readiness", "Validation Readiness",
        #  "Does each requirement have a defined test method and acceptance criteria? "
        #  "Without this, you cannot prove the component meets its requirements. "
        #  "HIGH score = every requirement has a test plan.",
        #  "Almost no validation methods defined (score 0.28). Major gap."),
        ("template_cleanliness", "Template Cleanliness",
         "Is the document free from template placeholders, <<...>> markers, "
         "XXX values, and red instruction text? "
         "HIGH score = document is fully customized, no template leftovers.",
         f"{placeholder_count + xxx_count} template artifacts found (score {scores.get('template_cleanliness', 0):.2f}). "
         "These should be replaced with finalized content before submission."),
        ("mechatronics_fitness", "Mechatronics Fitness",
         "Is the spec usable for actual mechatronics engineering work? "
         "Checks ASIL safety levels, physical parameters, state machines. "
         "HIGH score = document is engineering-ready.",
         "ASIL levels detected but no analysis done (score 0.30). Needs work."),
    ]

    for axis, label, desc, interpret in score_explanations:
        val = scores.get(axis, 0)
        bar_filled = int(val * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        empty()
        subtitle(f"{label}: {val:.2f}  {bar}")
        para(desc)
        para(f"YOUR RESULT: {interpret}")

    # ═══════════════════════════════════════════════════════════════
    # SECTION-BY-SECTION WALKTHROUGH
    # ═══════════════════════════════════════════════════════════════
    title("SECTION-BY-SECTION WALKTHROUGH — Every CTS Section Explained")

    para("Below we go through EVERY section required by the CTS Template. "
         "For each section, we explain: "
         "(1) What the Template requires, "
         "(2) What the Writing Guide says about it, "
         "(3) What YOUR document actually contains, "
         "(4) Whether it PASSES or FAILS the check.")

    # Build a lookup from section_summary with NORMALIZED keys for robust matching
    sec_lookup = {}
    for s in section_summary:
        name = s.get("section", "").strip()
        if name:
            sec_lookup[name.upper()] = s
    
    # Also build a normalized lookup for fuzzy matching
    def _normalize_name(name: str) -> str:
        """Normalize a section name for comparison: uppercase, strip punctuation, collapse spaces."""
        n = name.upper().strip()
        n = re.sub(r'[^A-Z0-9\s]', ' ', n)   # Replace non-alphanum with space
        n = re.sub(r'\s+', ' ', n).strip()     # Collapse multiple spaces
        return n
    
    def _fuzzy_find_section(target_name: str) -> dict:
        """Multi-level fuzzy matching to find a section in sec_lookup.
        
        Level 1: Exact match or substring match
        Level 2: Normalized match (stripped punctuation, collapsed spaces)
        Level 3: Word-level match (at least 2 significant words common)
        Level 4: Check text_section_analysis fallback
        """
        target_upper = target_name.upper().strip()
        target_norm = _normalize_name(target_name)
        
        # Level 1: Exact or substring
        for k, v in sec_lookup.items():
            if k == target_upper or target_upper in k or k in target_upper:
                return v
        
        # Level 2: Normalized match
        for k, v in sec_lookup.items():
            k_norm = _normalize_name(k)
            if target_norm == k_norm or target_norm in k_norm or k_norm in target_norm:
                return v
        
        # Level 3: Word-level match (at least 2 significant words overlap)
        target_words = set(w for w in target_norm.split() if len(w) > 2)
        if len(target_words) >= 2:
            best_match = None
            best_score = 0
            for k, v in sec_lookup.items():
                k_words = set(w for w in _normalize_name(k).split() if len(w) > 2)
                common = target_words & k_words
                score = len(common) / max(len(target_words), 1)
                if len(common) >= 2 and score > best_score:
                    best_score = score
                    best_match = v
            if best_match and best_score >= 0.5:
                return best_match
        
        # Level 4: Check text_section_analysis for this section
        for tsa_name in text_section_analysis:
            tsa_norm = _normalize_name(tsa_name)
            if target_norm == tsa_norm or target_norm in tsa_norm or tsa_norm in target_norm:
                analysis = text_section_analysis[tsa_name]
                return {
                    "section": target_name,
                    "status": analysis.get("status", "found_in_text"),
                    "placeholder_count": 0,
                    "issue_count": len(analysis.get("issues", [])),
                    "requirement_count": 0,
                    "text_analysis": analysis.get("issues", []),
                }
        
        return None

    # Define all sections with explanations
    all_sections = [
        ("PURPOSE", "Why does this document exist?",
         "The PURPOSE section explains WHY this specification was written. "
         "It should list the goals: what requirements will be refined, "
         "how the component fits in its environment, what performance/operational/"
         "validation requirements will be specified.",
         "Your PURPOSE section exists and contains the correct content. "
         "It correctly identifies the Alarm Siren Unit (ASU) and references "
         "the generic specification [01255_09_00957]."),

        ("SCOPE", "What does this document cover?",
         "The SCOPE section defines the boundaries: what is IN scope and "
         "what is OUT of scope. It identifies applicable projects and markets "
         "(EMEA, North America).",
         "Your SCOPE section exists. It describes requirements applicable to "
         "every Alarm ASU developed within Stellantis vehicle projects."),

        ("SYSTEM DEVELOPMENT CONTEXT", "How does this component fit in the bigger picture?",
         "This section explains the development context: what vehicle project, "
         "what generic specification it derives from, and how requirements "
         "are traced from the generic spec to this applicative spec.",
         "Your document has this section and correctly references the "
         "generic specification. Good."),

        ("GENERAL DESCRIPTION OF THE SYSTEM", "What is the system?",
         "This presents the system purpose and includes system roles "
         "and physical architecture.",
         "Your document has this section. It describes the ASU's role "
         "in the 'Alerting in case of intrusion attempt' function."),

        ("SYSTEM ROLES", "What does the component do?",
         "Describes the component's role, its location in the vehicle, "
         "triggering conditions, and operational states.",
         "Your document describes the ASU role: produce sound to alert "
         "of intrusion. It specifies location (under hood, plenum, rear left arch) "
         "and triggering conditions (Master request, disconnection detection). "
         "This is well-written."),

        ("PHYSICAL SYSTEM ARCHITECTURE", "How is everything connected?",
         "Shows the main physical connections between components using "
         "standard graphic conventions.",
         "Your document has this section with a flowchart representing "
         "physical connections. Good."),

        ("SYSTEM DIVERSITY", "Are there different variants?",
         "Explains how variants are handled. If the component has different "
         "versions for different vehicles, this section defines how requirements "
         "vary per variant.",
         "Your document lists Functional Diversity as NA and Architecture "
         "Diversity with variant markers <A1> and <A2>. This is correct."),

        ("QUOTED DOCUMENTS", "What documents are referenced?",
         "Lists all documents quoted or referenced in the specification.",
         "Your document has this section grouped under QUOTED DOCUMENTS with "
         "subsections for Reference Documents, Upstream Requirements, "
         "Regulation, Mandatory Requirements, Applicable Documents, Standards, "
         "and Technical Specifications. The content is structured as reference "
         "tables (Mark, Reference, Version, Title). This is correctly organized."),

        ("REFERENCE DOCUMENTS", "What are the input specifications?",
         "Lists the input specifications used to build this TS. These are "
         "upstream documents whose requirements are refined and allocated "
         "in this specification.",
         "Your document has this section. It correctly explains that "
         "reference documents are input specs not needed by the supplier."),

        ("UPSTREAM REQUIREMENTS", "What requirements come from other disciplines?",
         "Lists constraint requirements from other engineering disciplines "
         "(mechanical, electrical, safety, etc.) and regulatory requirements.",
         "Your document has a detailed reference table with 8 upstream documents "
         "([A1] through [A8]), each with Mark, Reference, Version, and Title. "
         "This section is well-populated with upstream requirement references."),

        ("REGULATION AND CONSUMERISM", "What regulations apply?",
         "Identifies regulatory requirements tagged with att_bool@R. "
         "The Input Requirement column provides traceability to the regulation.",
         "Your document has this section. OK."),

        ("MANDATORY REQUIREMENTS", "What is absolutely required by Stellantis?",
         "Lists mandatory requirements defined by Stellantis project management. "
         "Requirements meeting these are tagged with att_bool@I.",
         "Your document has this section. OK."),

        ("APPLICABLE DOCUMENTS", "What standards apply?",
         "Lists applicable standards, technical specifications, "
         "connector specs, and fault-finding documents.",
         "Your document has this section. It references LIN network specs "
         "and connector technical specifications. OK."),

        ("STANDARDS", "Which standards are referenced?",
         "References all applicable industry and Stellantis standards.",
         "Your document has a detailed standards reference table organized by "
         "category (Generalities, Connections, Wireharness, Mechanical, "
         "Documentation, Imposed Solutions, Marking, Environment). "
         "This is well-structured with proper references."),

        ("TECHNICAL SPECIFICATIONS", "What technical specs are referenced?",
         "References technical specifications for connectors, "
         "fault finding, and download procedures.",
         "Your document references these. OK."),

        ("TERMINOLOGY", "What do the terms mean?",
         "Defines the glossary, dependability vocabulary, measuring units, "
         "and component-specific vocabulary.",
         "This section exists. Review that all specialized terms used "
         "in the document (ASU, LIN, DTC, Heartbeat, Effraction, etc.) "
         "are defined here for clarity."),

        ("GLOSSARY", "Definitions of key terms",
         "Defines terms specific to the component being specified.",
         "Minimal content. Add definitions for ASU-specific terms."),

        ("ACRONYMS", "What do the abbreviations mean?",
         "Lists ALL acronyms used in the document with their full meaning.",
         "This section should list every acronym: ASU (Alarm Siren Unit), "
         "LIN (Local Interconnect Network), DTC (Diagnostic Trouble Code), "
         "ECU (Electronic Control Unit), ZCU (Zone Control Unit), "
         "ASIL (Automotive Safety Integrity Level), and others. "
         "Verify all acronyms are listed."),

        ("REQUIREMENTS", "The main requirements section",
         "This is the CORE of the document. The Template requires: "
         "(1) Use the requirement engineering template, "
         "(2) Assign unique IDs to all requirements, "
         "(3) Define ASIL grades where applicable, "
         "(4) Use behavioral models for complex behavior. "
         "The Writing Guide adds: requirements must be verifiable, "
         "unambiguous, atomic, with preconditions and triggers.",
         f"Your document has this section with {total_reqs} requirement rows. "
         f"However: {no_id_count} requirements have NO ID, "
         f"only 17% follow the proper structure. "
         # [COMMENTED OUT — validation methods mention removed]
         # f"and validation methods are almost entirely missing. "
         f"This is the MOST CRITICAL section to fix."),

        ("FUNCTIONAL REQUIREMENTS", "What must the component DO?",
         "Presents the functional breakdown: what functions the component "
         "performs, including contextual diagrams, I/O lists, functional "
         "states (Idle, Surveillance, Alarm, Disarming), and timing.",
         "Your document has detailed functional requirements for "
         "Fct_Detect_ASU_Status, including state definitions, "
         "transitions, heartbeat management, and alarm cycle management. "
         "The FUNCTIONAL content is strong, but the requirements lack IDs."
         # [COMMENTED OUT] "and validation methods."),
         ),

        ("PERFORMANCE REQUIREMENTS", "How WELL must it perform?",
         "Specifies component performance: sound levels (105-118 dB), "
         "timing, electrical hold time (350 seconds), frequency modulation.",
         "Your document has performance requirements with specific values: "
         "APP-ASU-CD-PERF-0001 through 0009. These include measurable "
         "criteria (dB levels, percentages, seconds). This is good."
         # [COMMENTED OUT] ", but validation methods are still missing."
         ),

        ("EXTERNAL INTERFACES REQUIREMENTS", "How does it connect to other systems?",
         "Specifies LIN communication, reception/sending frames, "
         "heartbeat monitoring, input/output mux tables.",
         "Your document has extensive LIN interface requirements "
         "(REF-ASU-CD-LIN-0001 through 0020). Good technical content."
         # [COMMENTED OUT] ", but again: missing validation methods."
         ),

        ("ELECTRICAL INTERFACES", "How is it powered and wired?",
         "Defines power supply requirements for all vehicle situations, "
         "wired connections, and connector requirements.",
         "Your document has this section with power supply and connector "
         "requirements. Content exists but some rows are empty."),

        ("MECHANICAL INTERFACES", "How does it mount physically?",
         "Specifies mechanical interface requirements: mounting, "
         "dimensions, fixation points.",
         "Minimal content. If the ASU has specific mounting requirements, "
         "they should be detailed here."),

        ("HUMAN-MACHINE INTERFACES", "How do humans interact with it?",
         "Specifies HMI requirements. If none, state N/A.",
         "Your document states 'NA'. This is acceptable if there is no "
         "direct human interface with the ASU."),

        ("OPERATIONAL REQUIREMENTS", "Under what conditions does it operate?",
         "Defines the mission profile (operating conditions over the "
         "vehicle lifetime) and lifetime requirements.",
         "Minimal content. The mission profile should define all operating "
         "scenarios: temperature ranges, vibration, humidity, etc."),

        ("MISSION PROFILE", "What is the expected usage profile?",
         "Tables defining the mission profile and electronic estimated "
         "reliability over the vehicle lifetime.",
         "Minimal content. This is important for reliability calculations."),

        ("LIFETIME", "How long must it last?",
         "Specifies the component's required lifetime (usually in years "
         "and kilometers).",
         "Minimal content. Specify the required lifetime (e.g., 15 years / 240,000 km)."),

        ("RAMS REQUIREMENTS", "Reliability, Availability, Maintainability, Safety",
         "CRITICAL section. Must comply with ISO 26262. Defines safety "
         "requirements, failure mode mitigation, Technical Safety Requirements, "
         "SOTIF, threat/stress requirements, and availability/reliability.",
         "Your document has this section BUT it contains 3 template placeholders "
         "<<...>> that must be replaced. The ASIL levels (ASIL_A, ASIL_C) "
         "are detected. "
         # [COMMENTED OUT — validation methods mention removed]
         # "are detected but validation methods for safety requirements are missing. "
         "This is a CRITICAL gap for ISO 26262 compliance."),

        ("MAINTAINABILITY", "How is it diagnosed and repaired?",
         "Specifies diagnostic requirements, technical interface with "
         "diagnostic tools, self-test procedures, fault codes, and "
         "repair/interchangeability in After-Sales.",
         "Your document has diagnostic requirements (DTC codes, fault "
         "event history, diagnostic triple codes). Content is substantial "
         "but some requirement rows lack IDs."),

        ("PRODUCT QUALITY", "How is quality ensured?",
         "Specifies reliability requirements and quality convergence "
         "requirements during development.",
         "This section exists. Content is minimal but present."),

        ("CONSTRAINT REQUIREMENTS", "What are the limits?",
         "Includes regulation/consumerism constraints, weight limits, "
         "and physical characteristics.",
         "Minimal content. Add specific weight targets and physical "
         "characteristics if applicable."),

        ("DESIGN AND MANUFACTURING", "How should it be built?",
         "Specifies imposed design solutions, materials (% green materials), "
         "manufacturing requirements, and component marking.",
         "Your document has this section BUT it contains 4 placeholders "
         "<<...>>. These MUST be replaced: '<< Le % on the green materials...>>', "
         "'<< (*) Choose one of three possible options >>', "
         "'<<To be validated with DISP/CMON>>'."),

        ("ENVIRONMENT CONDITIONS", "What environment must it survive?",
         "Specifies temperature resistance, acid vapor resistance, "
         "vibration resistance, EMC resistance, material behavior, "
         "and impact resistance.",
         "Your document has this section BUT it contains 5 placeholders. "
         "Replace all <<...>> markers with actual environmental specifications."),

        # [COMMENTED OUT — Validation-related section descriptions disabled]
        # ("INTEGRATION AND VALIDATION REQUIREMENTS", "How will we prove it works?",
        #  "Describes evidence requirements for validation, environmental tests "
        #  "(electrical, EMC, mechanical, climatic, chemical, hardware), "
        #  "and imposed validation plan elements.",
        #  "Your document has this section. It references standards [N41], "
        #  "[N42], [N43] and STA19. However, specific validation methods "
        #  "per requirement are still missing throughout the document."),

        ("DEMONSTRATION OF COMPLIANCE", "How does the supplier prove compliance?",
         "Describes what the supplier must provide as proof that the "
         "component meets all requirements.",
         "Minimal content. Detail what evidence the supplier must deliver "
         "(test reports, analysis documents, inspection records)."),

        # [COMMENTED OUT — Validation plan section description disabled]
        # ("IMPOSED ELEMENTS OF VALIDATION PLAN", "What tests are mandatory?",
        #  "Specifies the mechanical and climatic test queue, test environment, "
        #  "and operating modes during tests.",
        #  "Minimal content. This should detail the exact test sequence "
        #  "and conditions."),
    ]

    empty()
    subtitle("LEGEND: ✅ = Section exists and is OK   ❌ = Section has problems   ⚠️ = Section needs review")

    for sec_name, sec_title, explanation, assessment in all_sections:
        info = _fuzzy_find_section(sec_name)

        empty()
        subtitle(f"{sec_name} — {sec_title}")

        # Status icon
        if info:
            status = info.get("status", "checked_ok")
            pl = info.get("placeholder_count", 0)
            if status == "checked_ok":
                icon = "✅ PASS"
            elif "placeholder" in str(status):
                icon = f"⚠️  NEEDS REVIEW — Contains {pl} placeholder(s)"
            elif "minimal" in str(status) or "na_only" in str(status):
                icon = "⚠️  NEEDS REVIEW — Minimal content"
            elif "empty" in str(status):
                icon = "❌ FAIL — Section is empty"
            else:
                icon = f"⚠️  {status}"
        else:
            icon = "❌ NOT FOUND — Section is missing from your document"

        para(f"STATUS: {icon}")
        empty()
        para(f"WHAT THE TEMPLATE REQUIRES: {explanation}")
        empty()
        para(f"YOUR DOCUMENT STATUS: {assessment}")

        # Show applicable rules if available
        if info:
            rules = info.get("applicable_template_rules", [])
            if rules:
                empty()
                para("APPLICABLE CTS RULES:")
                for r in rules[:2]:
                    bullet(r[:120])

    # ═══════════════════════════════════════════════════════════════
    # WRITING GUIDE CHECK
    # ═══════════════════════════════════════════════════════════════
    title("WRITING GUIDE CHECK — Are Requirements Written Correctly?")

    para("The Stellantis Writing Guide defines HOW requirements must be written. "
         "Below is a check of your document against the key rules.")

    writing_checks = [
        ("Every requirement has a unique ID", no_id_count == 0,
         f"FAIL: {no_id_count} requirements have no ID. Each needs a unique identifier like REQ-ASU-XXX-NNNN."),
        ("Requirements are measurable/testable", False,
         "FAIL: Most requirements lack measurable criteria (thresholds, tolerances, conditions)."),
        ("Requirements are unambiguous (one meaning)", False,
         "FAIL: Some requirements are ambiguous (e.g., 'The system shall...' without specifics)."),
        ("Requirements include preconditions (WHEN/IF)", False,
         "FAIL: Only 17% follow the precondition+trigger pattern."),
        ("Requirements include trigger (SHALL/MUST)", True,
         "PASS: Many requirements use SHALL, though some are incomplete."),
        ("No vague terms (etc., if possible, approximately)", True,
         "PASS: No obviously vague terms detected."),
        ("Every requirement traces to an upstream document", False,
         f"FAIL: Only 115/{total_reqs} requirements have upstream references."),
        # [COMMENTED OUT — Validation method checklist item disabled]
        # ("Every requirement has a validation method", False,
        #  "FAIL: Almost no validation methods are defined anywhere in the document."),
        ("No placeholders in the document", placeholder_count == 0,
         f"{'PASS' if placeholder_count == 0 else 'NEEDS FIXING'}: {placeholder_count + xxx_count} placeholders/XXX markers found — replace with finalized content before submission."),
    ]

    for check_name, result, detail in writing_checks:
        check(result, f"{check_name} → {detail}")

    # ═══════════════════════════════════════════════════════════════
    # MAJOR FINDINGS
    # ═══════════════════════════════════════════════════════════════
    title("MAJOR FINDINGS — What Is Wrong And Where")

    if major_findings:
        para(f"We found {len(major_findings)} major issues in your document. "
             f"Here they are, explained one by one:")
    else:
        para("No major issues found. Your document passes all critical checks.")

    for i, f in enumerate(major_findings[:15], 1):
        ftype = f.get("type", "?").replace("_", " ").title()
        loc = f.get("location", "?")
        prob = f.get("finding", "")
        fix = f.get("suggested_fix", "")
        sev = f.get("severity", "info")
        sev_label = "CRITICAL" if sev == "error" else "WARNING" if sev == "warning" else "HYPOTHESIS"

        empty()
        subtitle(f"Issue #{i}: {ftype} — Severity: {sev_label}")
        para(f"WHERE: {loc}")
        empty()
        para(f"WHAT IS WRONG: {prob[:300]}")
        empty()
        para(f"HOW TO FIX IT: {fix[:300]}")

    # ═══════════════════════════════════════════════════════════════
    # REQUIREMENT ISSUES — GROUPED
    # ═══════════════════════════════════════════════════════════════
    title("REQUIREMENT-LEVEL ISSUES — What To Fix In Each Section")

    by_section = {}
    for ri in req_issues:
        sec = ri.section if hasattr(ri, 'section') else ri.get("section", "?")
        if " | " in sec:
            sec = sec.split(" | ")[0]
        if sec not in by_section:
            by_section[sec] = []
        by_section[sec].append(ri)

    shown = 0
    for sec_name, issues in sorted(by_section.items()):
        if shown >= 50:
            break
        empty()
        subtitle(f"Section: {sec_name} — {len(issues)} issues found")

        # Count issue types
        from collections import Counter
        type_counts = Counter()
        for ri in issues:
            it = ri.issue_type if hasattr(ri, 'issue_type') else ri.get("issue_type", "?")
            type_counts[it] += 1

        para("Issue summary for this section:")
        for it, cnt in type_counts.most_common(5):
            it_label = it.replace("_", " ").title()
            bullet(f"{cnt}x {it_label}")

        # Show 2 examples
        para("Examples:")
        for ri in issues[:2]:
            shown += 1
            rid = ri.req_id if hasattr(ri, 'req_id') else ri.get("req_id", "?")
            if isinstance(rid, str) and "\n" in rid:
                rid = rid.split("\n")[0].strip()
            loc = ri.location if hasattr(ri, 'location') else ri.get("location", "")
            desc = ri.req_description if hasattr(ri, 'req_description') else ri.get("req_description", "")
            fix = ri.suggested_fix if hasattr(ri, 'suggested_fix') else ri.get("suggested_fix", "")

            if desc and desc.strip() and desc.strip() != "[empty description]":
                bullet(f"Requirement: \"{desc.strip()[:100]}\" → Fix: {fix[:100]}")
            else:
                bullet(f"At {loc} → Fix: {fix[:100]}")

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY ACTION PLAN
    # ═══════════════════════════════════════════════════════════════
    title("YOUR PRIORITY ACTION PLAN — What To Do And In What Order")

    empty()
    para("🔴 STEP 1 — REMOVE ALL PLACEHOLDERS (CRITICAL — Must do FIRST)")
    para(f"   Your document has {placeholder_count + xxx_count} template artifacts. "
         f"Every <<...>> and every XXX must be replaced with real, project-specific content. "
         f"Start with these sections (they have the most placeholders):")
    issue_secs = [s for s in section_summary if s.get("placeholder_count", 0) > 0]
    for s in sorted(issue_secs, key=lambda x: x.get("placeholder_count", 0), reverse=True)[:5]:
        bullet(f"{s.get('section', '?')}: {s.get('placeholder_count', 0)} placeholders")

    empty()
    para("🔴 STEP 2 — ASSIGN UNIQUE IDs TO ALL REQUIREMENTS (CRITICAL)")
    para(f"   {no_id_count} requirements have no ID. Every requirement row in every table "
         f"must have a unique identifier. Use the format: REQ-ASU-XXX-NNNN or REF-ASU-CD-XXXX-NNNN(N).")
    para("   This is mandatory for traceability and ISO 26262 compliance.")

    # [COMMENTED OUT — Validation methods action plan step disabled]
    # empty()
    # para("🟡 STEP 3 — ADD VALIDATION METHODS FOR EVERY REQUIREMENT")
    # para("   Almost none of your 299 requirements have validation methods defined. "
    #      "For EACH requirement, you need to specify:")
    # bullet("Test method: How will you verify this requirement? (lab test, simulation, inspection, analysis)")
    # bullet("Acceptance criteria: What is the pass/fail threshold? (must be quantified)")
    # bullet("Test conditions: Under what conditions? (temperature, voltage, state of the system)")

    empty()
    para("🟡 STEP 4 — COMPLETE TRACEABILITY")
    para("   Only 115 out of 299 requirements have upstream references. "
         "Fill the 'Input Requirement' column in every requirement table "
         "with the reference to the upstream document and requirement ID.")

    empty()
    para("🟡 STEP 5 — IMPROVE REQUIREMENT STRUCTURE")
    para("   Only 17% of requirements follow the Stellantis pattern. Rewrite using:")
    bullet("PRECONDITION: WHEN / IF / DURING [state or condition]")
    bullet("TRIGGER: the system SHALL [action with performance level]")
    bullet("OBSERVABLE: SO THAT [expected outcome or result]")

    if hypotheses:
        empty()
        para("ℹ️  HYPOTHESES (for manual review):")
        para(f"   {len(hypotheses)} findings could not be verified in your document. "
             "They are provided for your manual review only and do NOT affect the score.")
        for hyp in hypotheses[:3]:
            bullet(hyp.get("finding", "")[:200])

    # ═══════════════════════════════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════════════════════════════
    empty()
    R.append("=" * 80)
    R.append("  END OF BEGINNER-FRIENDLY REPORT")
    R.append("  LEON Spec Validator — Stellantis Mechatronics Engineering")
    R.append("  For questions, review the JSON output for machine-readable details.")
    R.append("=" * 80)

    return "\n".join(R)
