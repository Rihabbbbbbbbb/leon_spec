"""
Regression tests for classify_conformity() (app/qa/conformity_analyzer.py).

A 2026 audit of the Conformity Matrix Analyzer independently predicted and
verified ~75 realistic OK/NOK/NA/EMPTY cell values (French + English) and
confirmed 7 real bugs: negated-OK phrases ("Not OK") and domain-prefixed NOK
("EE: NOK") silently fell through to EMPTY instead of NOK; trailing
punctuation ("Non conforme.", "N.A.") broke exact-match NOK/NA detection;
French "Sans objet" (a standard N/A phrasing) wasn't recognized; "N/A"
followed by free text fell to EMPTY instead of NA; the documented
`is_assessment` parameter was dead code; and negated conformity phrased
differently from the hardcoded guard list ("not compliant") was misread as
OK. None of this affected the real Gentex fixture (it only uses clean
canonical forms), but all are real risks for a differently-worded
submission.
"""
from app.qa.conformity_analyzer import classify_conformity


class TestNegatedNokDetection:
    def test_not_ok_is_nok_not_empty(self):
        assert classify_conformity("Not OK") == "NOK"

    def test_not_ok_with_trailing_text_is_nok(self):
        assert classify_conformity("Not OK yet, need rework") == "NOK"

    def test_not_conform_with_trailing_ok_mention_is_nok(self):
        assert classify_conformity("Not conform, will be ok in v2") == "NOK"

    def test_not_compliant_with_stray_ok_word_is_nok_not_ok(self):
        """A comment describing a CURRENT non-conformity that also happens
        to contain the bare word "ok" elsewhere must not be misread as OK."""
        assert classify_conformity(
            "this is currently not compliant, but should be ok in the next version"
        ) == "NOK"

    def test_does_not_meet_is_nok(self):
        assert classify_conformity("Does not meet the requirement") == "NOK"


class TestDomainPrefixedNok:
    def test_ee_nok_is_nok_not_empty(self):
        assert classify_conformity("EE: NOK") == "NOK"

    def test_sw_nok_is_nok_not_empty(self):
        assert classify_conformity("SW: NOK") == "NOK"

    def test_mixed_domain_with_one_nok_is_nok(self):
        """If ANY domain reports non-conformity, the overall cell is NOK —
        consistent with the "NOK takes priority over OK" rule already used
        for the Gentex-style separate OK/NOK columns elsewhere in this file."""
        assert classify_conformity("EE: ok / SW: nok") == "NOK"


class TestPunctuationRobustness:
    def test_non_conforme_with_trailing_period_is_nok(self):
        assert classify_conformity("Non conforme.") == "NOK"

    def test_non_conform_with_trailing_period_is_nok(self):
        assert classify_conformity("Non Conform.") == "NOK"

    def test_hyphenated_non_conforme_is_nok(self):
        assert classify_conformity("Non-conforme") == "NOK"

    def test_n_dot_a_dot_is_na(self):
        assert classify_conformity("N.A.") == "NA"

    def test_n_slash_a_with_trailing_period_is_na(self):
        assert classify_conformity("N/A.") == "NA"

    def test_ok_with_trailing_punctuation_still_ok(self):
        assert classify_conformity("Ok.") == "OK"
        assert classify_conformity("OK!!") == "OK"


class TestNaRobustness:
    def test_sans_objet_is_na(self):
        assert classify_conformity("Sans objet") == "NA"

    def test_n_slash_a_with_trailing_free_text_is_na(self):
        assert classify_conformity("N/A - not required for this variant") == "NA"


class TestIsAssessmentParameter:
    """The documented behavior ("uncertain/pending language = NOK when
    is_assessment, EMPTY otherwise") was previously dead code — the
    parameter was accepted but never read anywhere in the function body."""

    def test_tbd_is_nok_when_is_assessment_true(self):
        assert classify_conformity("TBD", is_assessment=True) == "NOK"

    def test_tbd_is_empty_when_is_assessment_false(self):
        assert classify_conformity("TBD", is_assessment=False) == "EMPTY"

    def test_pending_is_nok_when_is_assessment_true(self):
        assert classify_conformity("pending", is_assessment=True) == "NOK"


class TestBaselineStillCorrect:
    """Every case the audit confirmed was ALREADY correct — must stay so."""

    def test_canonical_ok_values(self):
        for v in ("OK", "ok", " OK ", "Conforme", "okay", "Oui", "oui", "/"):
            assert classify_conformity(v) == "OK", v

    def test_canonical_nok_values(self):
        for v in ("NOK", "Non conforme", "KO", "Non", "nc", "no"):
            assert classify_conformity(v) == "NOK", v

    def test_canonical_na_values(self):
        for v in ("N/A", "NA", "Non applicable", "Not Applicable"):
            assert classify_conformity(v) == "NA", v

    def test_domain_codes_alone_are_empty(self):
        for v in ("EE", "EE:", "EE/VE/ME", "A", ""):
            assert classify_conformity(v) == "EMPTY", v

    def test_word_boundary_prevents_substring_false_positives(self):
        """"ok" embedded inside another word must never trigger OK."""
        for v in ("book", "broken", "outlook"):
            assert classify_conformity(v) == "EMPTY", v

    def test_empty_and_whitespace(self):
        assert classify_conformity("") == "EMPTY"
        assert classify_conformity("   ") == "EMPTY"
