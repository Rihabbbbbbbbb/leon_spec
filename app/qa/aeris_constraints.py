"""
Deterministic extraction and comparison of quantitative engineering constraints.

This is the layer that makes AERIS give *correct* answers for cases like
≤100 mA vs Typ 119.9 / Max 192.7 mA. An LLM is not used here: numbers,
units, operators and conditions are parsed and compared with explicit rules.

Supported families: current, voltage, temperature, percent, ratio, angle,
power, frequency, time, length, dimensionless load/percentage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ── Data ───────────────────────────────────────────────────────────

@dataclass
class Constraint:
    """A measurable target extracted from a requirement or comment."""
    raw: str
    value: float
    operator: str            # le, lt, ge, gt, eq
    unit_family: str
    unit: str
    condition: str = ""
    quantity: str = ""
    source: str = "requirement"  # requirement | comment


@dataclass
class Measurement:
    """A measured / declared value extracted from evidence (TDR/PPT/PDF)."""
    raw: str
    value: float
    unit_family: str
    unit: str
    condition: str = ""
    qualifier: str = ""      # typ | max | min | ""
    location: str = ""       # slide/page label
    quantity: str = ""       # attenuation | cpu_load | contrast | current…


@dataclass
class ConditionVerdict:
    condition: str
    target: str
    measured: str
    status: str              # CONFORME | NON_CONFORME | INCOMPARABLE
    gap: Optional[float] = None
    gap_unit: str = ""


# ── Unit catalogue ─────────────────────────────────────────────────
# Longer tokens first so "°c" wins over "°" and "ma" over "a".

_UNIT_TABLE: List[Tuple[str, str, float]] = [
    # family, token, factor-to-canonical
    ("current", "µa", 0.001),
    ("current", "ua", 0.001),
    ("current", "ma", 1.0),
    ("current", "ampere", 1000.0),
    ("current", "amps", 1000.0),
    ("current", "amp", 1000.0),
    ("voltage", "mv", 0.001),
    ("voltage", "kv", 1000.0),
    ("temperature", "°c", 1.0),
    ("temperature", "degc", 1.0),
    ("temperature", "deg c", 1.0),
    ("temperature", "celsius", 1.0),
    ("percent", "percent", 1.0),
    ("percent", "pct", 1.0),
    ("percent", "%", 1.0),
    ("ratio", ":1", 1.0),
    ("ratio", "ratio", 1.0),
    ("power", "mw", 1.0),
    ("power", "kw", 1_000_000.0),
    ("power", "w", 1000.0),
    ("frequency", "mhz", 1_000_000.0),
    ("frequency", "khz", 1000.0),
    ("frequency", "hz", 1.0),
    ("time", "ms", 1.0),
    ("time", "sec", 1000.0),
    ("time", "min", 60_000.0),
    ("length", "µm", 0.001),
    ("length", "um", 0.001),
    ("length", "mm", 1.0),
    ("length", "cm", 10.0),
    ("angle", "degree", 1.0),
    ("angle", "degrees", 1.0),
    ("angle", "deg", 1.0),
    ("angle", "°", 1.0),
    # Ambiguous single letters — only accepted with a word boundary and
    # a nearby quantity hint (handled in _looks_like_unit).
    ("current", "a", 1000.0),
    ("voltage", "v", 1.0),
    ("temperature", "c", 1.0),
    ("power", "w", 1000.0),
]

_AMBIGUOUS_UNITS = {"a", "v", "c", "w"}

_OPERATOR_MAP = [
    (r"≤|<=|=<", "le"),
    (r"≥|>=|=>", "ge"),
    (r"(?<![<>=])<(?![<=])", "lt"),
    (r"(?<![<>=])>(?![>=])", "gt"),
    (r"\bat\s+most\b|\bno\s+more\s+than\b|\bmaximum\b|\bmax(?:imum)?\s*(?:of|value)?\b", "le"),
    (r"\bat\s+least\b|\bno\s+less\s+than\b|\bminimum\b|\bmin(?:imum)?\s*(?:of|value)?\b", "ge"),
]

_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9])(-?\d+(?:[.,]\d+)?)\s*(:1)?"
    r"(?:\s*([µuμ]?[A-Za-z%°]+(?:\s*[Cc])?))?",
)

_QUALIFIER_RE = re.compile(r"\b(typ(?:ical)?|max(?:imum)?|min(?:imum)?)\b", re.I)

# A number after “target / limit / requirement” is a restated spec, not a result.
_RESTATE_LEAD = re.compile(
    r"\b(?:target|objectif|consigne|requirement|exigence|"
    r"spec(?:ification)?|limit|seuil|design\s+target)\b"
    r"(?:\s*(?:is|of|de|=|:))?\s*(?:≤|≥|<=|>=|<|>)?\s*$",
    re.I,
)

_CONDITION_RE = re.compile(
    r"(?:"
    r"@\s*V\s*=\s*[\d.,]+\s*°?"
    r"|@\s*[\d.,]+\s*°C"
    r"|at\s+V\s*=\s*[\d.,]+\s*°?"
    r"|at\s+[\d.,]+\s*°C"
    r"|V\s*=\s*[\d.,]+\s*°"
    r"|reduced(?:\s+consumption)?\s+mode"
    r"|full(?:\s+consumption)?\s+mode"
    r"|normal(?:\s+consumption)?\s+mode"
    r")",
    re.I,
)

_QUANTITY_HINTS = [
    (r"\bcurrent\b|\bconsommation\b|\bconsumption\b|\bamp", "current"),
    (r"\bvoltage\b|\btension\b", "voltage"),
    (r"\btemp(?:erature)?\b|\bthermal\b", "temperature"),
    (r"\battenuat", "percent"),
    (r"\bcontrast\b", "ratio"),
    (r"\bload\b|\bcpu\b|\bcpu\s+load\b", "percent"),
    (r"\bpower\b|\bwatt", "power"),
    (r"\bstartup\b|\bboot\b|\btime\b", "time"),
]


def _norm_num(text: str) -> float:
    return float(text.replace(",", "."))


def _norm_unit_token(token: str) -> str:
    t = (token or "").strip().lower()
    t = t.replace("μ", "µ").replace("deg.", "deg")
    t = re.sub(r"\s+", "", t)
    return t


def _looks_like_unit(token: str, nearby: str) -> bool:
    tok = _norm_unit_token(token)
    if not tok:
        return False
    if tok not in _AMBIGUOUS_UNITS:
        return True
    near = nearby.lower()
    if tok == "a" and re.search(r"current|amp|consum", near):
        return True
    if tok == "v" and re.search(r"volt|tension|v\s*=", near):
        return True
    if tok == "c" and re.search(r"temp|thermal|°", near):
        return True
    if tok == "w" and re.search(r"power|watt", near):
        return True
    return False


def classify_unit(token: str, nearby: str = "") -> Tuple[str, str, float]:
    """
    Return (family, canonical_token, factor_to_canonical) or ("", "", 1).
    Canonical: current→mA, voltage→V, temperature→°C, percent→%,
    ratio→:1, power→mW, frequency→Hz, time→ms, length→mm, angle→°.
    """
    tok = _norm_unit_token(token)
    if tok in {":1", "ratio"}:
        return "ratio", ":1", 1.0
    if not tok:
        return "", "", 1.0
    if not _looks_like_unit(tok, nearby) and tok in _AMBIGUOUS_UNITS:
        return "", "", 1.0
    for family, known, factor in _UNIT_TABLE:
        if tok == known:
            return family, known, factor
    return "", tok, 1.0


def to_canonical(value: float, family: str, factor: float) -> float:
    return value * factor


def _is_id_or_condition_number(text: str, start: int, raw_num: str) -> bool:
    """Skip REQ-IDs, years, slide/page indices, and V=32° condition numbers."""
    prefix = text[max(0, start - 12):start].lower()
    if re.search(r"req[-_\s]?$", prefix):
        return True
    if re.search(r"(slide|page|block|section)\s*$", prefix):
        return True
    digits = raw_num.lstrip("-")
    if digits.isdigit() and len(digits) >= 6:
        return True
    if re.match(r"20\d{2}$", raw_num):
        return True
    if re.search(r"v\s*=\s*$", prefix):
        return True
    return False


def _quantity_of(text: str) -> str:
    """Fine-grained quantity so % attenuation is never compared to % CPU load."""
    t = (text or "").lower()
    if re.search(r"attenuat|lcf|luminance", t):
        return "attenuation"
    if re.search(r"contrast", t):
        return "contrast"
    if re.search(r"\bcpu\b|cpu\s+load|\bload\b", t):
        return "cpu_load"
    if re.search(r"\bidle\b", t):
        return "idle_current"
    if re.search(r"\bstandby\b", t):
        return "standby_current"
    if re.search(r"reduced", t) and re.search(r"current|consum", t):
        return "reduced_current"
    if re.search(r"current|consommation|consumption|amp", t):
        return "current"
    if re.search(r"\bstorage\b", t) and re.search(r"temp", t):
        return "storage_temperature"
    if re.search(r"\bdisplay\b", t) and re.search(r"temp", t):
        return "display_temperature"
    if re.search(r"temp(?:erature)?|thermal", t):
        return "temperature"
    if re.search(r"voltage|tension", t):
        return "voltage"
    if re.search(r"startup|boot|\btime\b", t):
        return "time"
    return ""


_GENERIC_QTY = {
    "", "temperature", "current", "percent", "time", "voltage", "power",
    "ratio", "length", "frequency", "angle",
}


def _prefer_quantity(window: str, text: str) -> str:
    """Prefer a specific tag (idle_current, storage_temperature) over the family."""
    a, b = _quantity_of(window), _quantity_of(text)
    if b and b not in _GENERIC_QTY and (not a or a in _GENERIC_QTY):
        return b
    return a or b


def _guess_family_from_text(text: str) -> str:
    for pat, family in _QUANTITY_HINTS:
        if re.search(pat, text, re.I):
            return family
    return ""


def _extract_condition(text: str, prefer_after: str = "") -> str:
    """Return the operating-point nearest the value, not the first in the file."""
    search = prefer_after if prefer_after and _CONDITION_RE.search(prefer_after) else text
    matches = _CONDITION_RE.findall(search)
    if not matches:
        matches = _CONDITION_RE.findall(text)
    if not matches:
        return ""
    cleaned = [re.sub(r"\s+", " ", m).strip() for m in matches]
    return cleaned[0]


def _detect_operator(text: str, default: str = "le") -> str:
    for pat, op in _OPERATOR_MAP:
        if re.search(pat, text, re.I):
            return op
    if re.search(r"\bshall\s+not\s+exceed\b|\bmust\s+not\s+exceed\b", text, re.I):
        return "le"
    if re.search(r"\bshall\s+(?:be\s+)?(?:greater|higher|above)\b", text, re.I):
        return "ge"
    return default


def extract_constraints(text: str, source: str = "requirement") -> List[Constraint]:
    """Extract measurable constraints from a requirement or comment."""
    if not text or not text.strip():
        return []

    found: List[Constraint] = []
    # Ratio first: 400:1
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*:\s*1\b", text):
        value = _norm_num(m.group(1))
        window = text[max(0, m.start() - 40): m.end() + 40]
        found.append(Constraint(
            raw=m.group(0),
            value=value,
            operator=_detect_operator(window, default="ge"),
            unit_family="ratio",
            unit=":1",
            condition=_extract_condition(window) or _extract_condition(text),
            quantity=_quantity_of(text) or "contrast",
            source=source,
        ))

    for m in _NUM_RE.finditer(text):
        raw_num, ratio_mark, unit_tok = m.group(1), m.group(2), m.group(3) or ""
        if ratio_mark:
            continue  # already handled
        if _is_id_or_condition_number(text, m.start(), raw_num):
            continue
        prefix = text[max(0, m.start() - 24): m.start()]
        after = text[m.end(): m.end() + 36]
        window = prefix + m.group(0) + after
        family, unit, factor = classify_unit(unit_tok, window)
        if not family:
            # Bare number only if a unit-like hint sits right next to it.
            hinted = _guess_family_from_text(window)
            if hinted == "percent" and re.search(r"%|load|cpu", window, re.I):
                family, unit, factor = "percent", "%", 1.0
            elif hinted == "ratio" and re.search(r"contrast", window, re.I):
                family, unit, factor = "ratio", ":1", 1.0
            else:
                continue
        value = to_canonical(_norm_num(raw_num), family, factor)
        found.append(Constraint(
            raw=m.group(0).strip(),
            value=value,
            operator=_detect_operator(window, default="le" if family in ("current", "temperature", "power") else "ge"),
            unit_family=family,
            unit=unit or family,
            condition=_extract_condition(after) or _extract_condition(window) or _extract_condition(text),
            quantity=_prefer_quantity(window, text) or family,
            source=source,
        ))

    return _dedupe_constraints(found)


def extract_measurements(text: str, location: str = "") -> List[Measurement]:
    """Extract measured values from a TDR/PPT/PDF passage."""
    if not text or not text.strip():
        return []

    found: List[Measurement] = []

    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*:\s*1\b", text):
        prefix = text[max(0, m.start() - 24): m.start()]
        after = text[m.end(): m.end() + 36]
        window = prefix + m.group(0) + after
        found.append(Measurement(
            raw=m.group(0),
            value=_norm_num(m.group(1)),
            unit_family="ratio",
            unit=":1",
            condition=_extract_condition(after) or _extract_condition(window),
            qualifier=_qualifier(window, prefix=prefix),
            location=location,
            quantity=_quantity_of(window) or _quantity_of(text) or "contrast",
        ))

    for m in _NUM_RE.finditer(text):
        raw_num, ratio_mark, unit_tok = m.group(1), m.group(2), m.group(3) or ""
        if ratio_mark:
            continue
        if _is_id_or_condition_number(text, m.start(), raw_num):
            continue
        prefix = text[max(0, m.start() - 24): m.start()]
        after = text[m.end(): m.end() + 36]
        window = prefix + m.group(0) + after
        # Skip quoted spec limits (“target 800ms”, “requirement ≤100mA”).
        if _RESTATE_LEAD.search(prefix):
            continue
        family, unit, factor = classify_unit(unit_tok, window)
        if not family:
            hinted = _guess_family_from_text(window)
            # Bare numbers only when the hint is in the tight window — never
            # borrow "%" from a different requirement later in the document.
            # Contrast tables list "500 @25°C" on later lines without repeating
            # the word "contrast", so a document-level contrast hint + @°C is OK.
            if hinted == "percent" and re.search(r"%|cpu|load", window, re.I):
                family, unit, factor = "percent", "%", 1.0
            elif (hinted == "ratio" and re.search(r"contrast", window, re.I)) or (
                _quantity_of(text) == "contrast" and re.search(r"@\s*[\d.,]+\s*°C", after + window, re.I)
            ):
                family, unit, factor = "ratio", ":1", 1.0
            elif hinted == "temperature" and re.search(r"temp|°c|thermal", window, re.I):
                family, unit, factor = "temperature", "°c", 1.0
            else:
                continue
        qty = _prefer_quantity(window, text)
        if family == "percent" and not qty:
            if re.search(r"attenuat|lcf", window + " " + after, re.I):
                qty = "attenuation"
            elif re.search(r"cpu|load", window, re.I):
                qty = "cpu_load"
        found.append(Measurement(
            raw=m.group(0).strip(),
            value=to_canonical(_norm_num(raw_num), family, factor),
            unit_family=family,
            unit=unit or family,
            condition=_extract_condition(after) or _extract_condition(window),
            qualifier=_qualifier(window, prefix=prefix),
            location=location,
            quantity=qty,
        ))

    return _dedupe_measurements(found)


_NEAR_QUALIFIER_RE = re.compile(
    r"(typ(?:ical)?|max(?:imum)?|min(?:imum)?)\s*$", re.I
)


def _qualifier(window: str, prefix: str = "") -> str:
    # Prefer the qualifier immediately before the number ("Max 192.7mA")
    # so a nearby "Typ 119.9" does not steal the label of the next value.
    src = prefix if prefix else window
    m = _NEAR_QUALIFIER_RE.search(src[-24:]) if src else None
    if not m:
        m = _QUALIFIER_RE.search(window[-20:] if window else "")
    if not m:
        return ""
    w = m.group(1).lower()
    if w.startswith("typ"):
        return "typ"
    if w.startswith("max"):
        return "max"
    if w.startswith("min"):
        return "min"
    return ""


def _dedupe_constraints(items: List[Constraint]) -> List[Constraint]:
    seen = set()
    out = []
    for c in items:
        key = (round(c.value, 6), c.operator, c.unit_family, c.condition.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _dedupe_measurements(items: List[Measurement]) -> List[Measurement]:
    seen = set()
    out = []
    for m in items:
        key = (round(m.value, 6), m.unit_family, m.condition.lower(), m.qualifier, m.quantity)
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def conditions_compatible(a: str, b: str) -> bool:
    """True if two condition strings refer to the same operating point, or one is empty."""
    if not a or not b:
        return True
    na, nb = _norm_condition(a), _norm_condition(b)
    if na == nb:
        return True
    # Shared distinctive tokens (32°, 25c, reduced…)
    ta, tb = set(na.split()), set(nb.split())
    distinctive = {t for t in ta & tb if re.search(r"\d|reduc|full|normal|mode", t)}
    return bool(distinctive)


def _norm_condition(text: str) -> str:
    t = text.lower()
    t = t.replace("°c", "c").replace("degc", "c").replace("degrees", "")
    t = t.replace("degree", "").replace("°", "")
    t = re.sub(r"[^a-z0-9.\s=]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _compare_one(op: str, measured: float, target: float) -> bool:
    if op == "le":
        return measured <= target + 1e-9
    if op == "lt":
        return measured < target - 1e-12
    if op == "ge":
        return measured >= target - 1e-9
    if op == "gt":
        return measured > target + 1e-12
    if op == "eq":
        return abs(measured - target) <= max(0.01 * abs(target), 1e-6)
    return False


def _pick_worst(op: str, candidates: List[Measurement]) -> Measurement:
    """For an upper-bound, the worst value is the largest; for a lower-bound, the smallest."""
    if op in ("le", "lt"):
        # Prefer an explicit max qualifier when present.
        maxed = [m for m in candidates if m.qualifier == "max"]
        pool = maxed or candidates
        return max(pool, key=lambda m: m.value)
    if op in ("ge", "gt"):
        mined = [m for m in candidates if m.qualifier == "min"]
        pool = mined or candidates
        return min(pool, key=lambda m: m.value)
    return candidates[0]


def compare_constraint(
    constraint: Constraint,
    measurements: List[Measurement],
) -> List[ConditionVerdict]:
    """
    Compare one requirement constraint against all compatible measurements.

    Multiple operating points (25 °C / 70 °C / 85 °C) each get their own verdict
    so the caller can emit PARTIELLEMENT_CONFORME.
    """
    same_family = [m for m in measurements if m.unit_family == constraint.unit_family]
    if constraint.quantity:
        same_qty = [m for m in same_family if not m.quantity or m.quantity == constraint.quantity]
        if same_qty:
            same_family = same_qty
    if not same_family:
        return []

    # Group measurements by normalized condition.
    groups: Dict[str, List[Measurement]] = {}
    for m in same_family:
        if not conditions_compatible(constraint.condition, m.condition):
            continue
        key = _norm_condition(m.condition) or _norm_condition(constraint.condition) or ""
        groups.setdefault(key, []).append(m)

    if not groups:
        return []

    verdicts: List[ConditionVerdict] = []
    for cond_key, group in groups.items():
        # Typ/Max are one operating point (use the worst). Two bare results
        # that disagree are two tests — emit both so PARTIELLEMENT is possible.
        members = group if _bare_results_conflict(group) else [_pick_worst(constraint.operator, group)]
        for chosen in members:
            ok = _compare_one(constraint.operator, chosen.value, constraint.value)
            gap = chosen.value - constraint.value
            op_sym = {"le": "<=", "lt": "<", "ge": ">=", "gt": ">", "eq": "="}.get(constraint.operator, constraint.operator)
            verdicts.append(ConditionVerdict(
                condition=chosen.condition or constraint.condition or cond_key,
                target=f"{op_sym}{constraint.value:g} {constraint.unit}",
                measured=f"{chosen.qualifier + ' ' if chosen.qualifier else ''}{chosen.value:g} {chosen.unit}".strip(),
                status="CONFORME" if ok else "NON_CONFORME",
                gap=gap,
                gap_unit=constraint.unit,
            ))
    return verdicts


def _bare_results_conflict(group: List[Measurement]) -> bool:
    bare = [m for m in group if not m.qualifier]
    if len(bare) < 2:
        return False
    vals = [m.value for m in bare]
    span = max(vals) - min(vals)
    scale = max(abs(v) for v in vals) or 1.0
    return span > 1e-9 and (span / scale) > 0.08


def summarize_verdicts(verdicts: List[ConditionVerdict]) -> str:
    """Roll per-condition results into a single status."""
    if not verdicts:
        return "PREUVE_INSUFFISANTE"
    statuses = {v.status for v in verdicts}
    if statuses == {"CONFORME"}:
        return "CONFORME"
    if statuses == {"NON_CONFORME"}:
        return "NON_CONFORME"
    if "NON_CONFORME" in statuses and "CONFORME" in statuses:
        return "PARTIELLEMENT_CONFORME"
    return "PREUVE_INSUFFISANTE"


def operator_symbol(op: str) -> str:
    return {"le": "<=", "lt": "<", "ge": ">=", "gt": ">", "eq": "="}.get(op, op)


# Affichage lisible : "<=50 ma" → "≤50 mA", "380 :1" → "380:1".
_PRETTY_UNIT = {
    "ma": "mA", "µa": "µA", "ua": "µA", "amp": "A", "ampere": "A", "amps": "A",
    "mv": "mV", "kv": "kV", "°c": "°C", "degc": "°C", "celsius": "°C",
    "mw": "mW", "kw": "kW", "mhz": "MHz", "khz": "kHz", "hz": "Hz",
    "ms": "ms", "sec": "s", "min": "min", "µm": "µm", "um": "µm",
    "mm": "mm", "cm": "cm", "percent": "%", "pct": "%", "ratio": ":1",
    "deg": "°", "degree": "°", "degrees": "°",
}


def pretty_unit(unit: str) -> str:
    tok = (unit or "").strip()
    return _PRETTY_UNIT.get(tok.lower(), tok)


def pretty_value(text: str) -> str:
    """Rendre un affichage machine lisible : opérateurs et unités."""
    if not text:
        return ""
    out = str(text)
    out = out.replace("<=", "≤").replace(">=", "≥")
    # Unités en fin de nombre : "50 ma" → "50 mA"
    def _unit_sub(m):
        return m.group(1) + pretty_unit(m.group(2))
    out = re.sub(
        r"(\d\s*)([A-Za-zµ°]+(?:\s*[Cc])?)",
        lambda m: _unit_sub(m) if pretty_unit(m.group(2)) != m.group(2) else m.group(0),
        out,
    )
    out = re.sub(r"\s*:\s*1\b", ":1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out
