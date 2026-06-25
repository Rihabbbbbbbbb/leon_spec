"""Quick sanity check — verify Azure Function imports work."""
import sys, traceback
sys.path.insert(0, ".")

with open("_azure_sanity.txt", "w", encoding="utf-8") as out:
    def p(*a):
        out.write(" ".join(str(x) for x in a) + "\n")
        out.flush()

    tests = [
        ("azure_handler imports", "from azure_handler import handle_ask, handle_validate, handle_upload, handle_list_files"),
        ("app.qa.retrieval", "from app.qa.retrieval import build_index, retrieve, extract_text_from_file"),
        ("app.qa.prompt", "from app.qa.prompt import is_standards_question, STANDARDS_REFUSAL_MESSAGE, NOT_FOUND_MESSAGE, SYSTEM_PROMPT, build_user_prompt, extract_confidence"),
        ("app.qa.evidence_comparator", "from app.qa.evidence_comparator import validate_with_evidence"),
        ("app.qa.section_guidance", "from app.qa.section_guidance import is_section_guidance_question, get_section_guidance"),
        ("app.qa.rule_extractor", "from app.qa.rule_extractor import extract_all_rules"),
        ("app.qa.route (utils)", "from app.qa.route import _is_validation_question, _pick_validation_file, _is_overview_question, _detect_referenced_file, _retrieve_file_overview, _try_acronym_retrieval"),
    ]

    passed = 0
    failed = 0
    for name, import_stmt in tests:
        try:
            exec(import_stmt)
            p(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            p(f"  FAIL: {name} — {e}")
            failed += 1

    p(f"\n  Imports: {passed}/{passed + failed} passed")

    if failed == 0:
        p("\n  All imports OK — Azure Function is ready to deploy!")
    else:
        p(f"\n  {failed} import(s) failed — check dependencies.")
