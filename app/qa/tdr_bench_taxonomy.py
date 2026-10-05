"""Technical taxonomy for multi-supplier TDR (Technical Design Review) benchmarks.

Domains, fact kinds and canonical parameters are shared by the deterministic
extractors, the LLM schemas, the UI and the exports so that every supplier is
read against the same grid (side-by-side comparison needs a common frame).
Commercial content (price, cost breakdown, payment terms) is out of scope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Domain:
    key: str
    label_en: str
    label_fr: str
    weight: float
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def label(self, lang: str) -> str:
        return self.label_fr if lang == "fr" else self.label_en


DOMAINS: tuple[Domain, ...] = (
    Domain("architecture", "Product & system architecture", "Architecture produit & système", 1.0, (
        r"system architecture", r"block diagram", r"product description", r"architecture",
        r"variants?", r"interfaces?", r"system overview", r"product overview", r"topology",
        r"pin ?out", r"harness", r"pigtail", r"connector",
    )),
    Domain("mechanical", "Mechanical design", "Conception mécanique", 1.0, (
        r"mechanic", r"housing", r"bracket", r"bezel", r"rear cover", r"back ?cover",
        r"thickness", r"weight", r"mass", r"tolerance", r"hinge", r"screw", r"fixation",
        r"material", r"die[- ]?cast", r"magnesium", r"alumin", r"plastic", r"pc/abs",
        r"stack[- ]?up", r"head impact", r"vibration", r"crash", r"cad", r"envelope",
    )),
    Domain("display_optical", "Display & optical performance", "Afficheur & performances optiques", 1.5, (
        r"\btft\b", r"\blcd\b", r"oled", r"panel", r"luminance", r"brightness", r"cd/m",
        r"\bnits?\b", r"contrast", r"colou?r gamut", r"ntsc", r"dci", r"viewing angle",
        r"backlight", r"\bblu\b", r"local dimming", r"mini ?led", r"\bled\b", r"optical",
        r"bonding", r"cover (?:lens|glass)", r"anti[- ]?glare", r"anti[- ]?reflect",
        r"\bag\b", r"\bar\b", r"\baf\b", r"reflectance", r"mura", r"gamma", r"uniformity",
        r"resolution", r"pixel", r"privacy", r"polari[sz]er", r"sunlight", r"cie",
    )),
    Domain("touch_hmi", "Touch & HMI", "Tactile & IHM", 0.8, (
        r"touch", r"capacitive", r"in[- ]?cell", r"on[- ]?cell", r"\bgff\b", r"\bogs\b",
        r"haptic", r"force sens", r"glove", r"water", r"hover", r"proximity", r"gesture",
        r"\bhmi\b", r"button", r"knob", r"ambient light",
    )),
    Domain("hardware", "Hardware & electronics", "Hardware & électronique", 1.2, (
        r"hardware", r"\bpcba?\b", r"schematic", r"\bsoc\b", r"\bmcu\b", r"micro[- ]?controller",
        r"serializer", r"deserializer", r"\bgmsl", r"fpd[- ]?link", r"\bapix", r"\bmipi",
        r"\bedp\b", r"\blvds\b", r"timing controller", r"\btcon\b", r"power supply",
        r"\bpmic\b", r"\bdc[/-]?dc\b", r"\bldo\b", r"voltage", r"current", r"power consumption",
        r"\bemc\b", r"\besd\b", r"\bcan\b", r"\blin\b", r"ethernet", r"component",
        r"\bbom\b", r"memory", r"flash", r"eeprom", r"\bddr", r"semiconductor", r"layer",
    )),
    Domain("thermal", "Thermal management", "Gestion thermique", 0.8, (
        r"thermal", r"temperature", r"heat", r"derating", r"heat ?sink", r"cooling",
        r"junction", r"°c", r"simulation", r"\bcfd\b", r"hot spot", r"solar load",
    )),
    Domain("software", "Software", "Logiciel", 1.2, (
        r"software", r"\bsw\b", r"firmware", r"bootloader", r"autosar", r"\bota\b",
        r"reprogramm", r"flashing", r"\buds\b", r"diagnostic", r"\bdtc\b", r"\bos\b",
        r"rtos", r"linux", r"driver", r"stack", r"update", r"calibration", r"log",
    )),
    Domain("functional_safety", "Functional safety", "Sûreté de fonctionnement", 1.0, (
        r"functional safety", r"\bfusa\b", r"iso ?26262", r"\basil", r"\bqm\b", r"fmeda",
        r"\bfmea\b", r"\bfta\b", r"safety (?:goal|mechanism|concept|case|manager)",
        r"frozen (?:image|frame)", r"black screen", r"\bcrc\b", r"tell[- ]?tale",
        r"safety requirement", r"\bhara\b", r"\bdfmea\b",
    )),
    Domain("cybersecurity", "Cybersecurity", "Cybersécurité", 1.0, (
        r"cyber", r"iso ?(?:/sae ?)?21434", r"\btara\b", r"secure boot", r"\bhsm\b",
        r"encryption", r"authentication", r"\bkey\b", r"certificate", r"vulnerab",
        r"penetration", r"r155", r"r156", r"security",
    )),
    Domain("validation", "Validation & testing", "Validation & essais", 1.0, (
        r"validation", r"verification", r"\bdv\b", r"\bpv\b", r"test plan", r"\bdvp",
        r"reliability", r"endurance", r"climatic", r"humidity", r"thermal shock",
        r"salt", r"dust", r"drop", r"\bhalt\b", r"\balt\b", r"test bench", r"lab",
        r"qualification", r"\baec[- ]?q", r"\bmtbf\b", r"\bfit\b", r"\bppm\b", r"lifetime",
    )),
    Domain("quality_process", "Quality & development process", "Qualité & processus de développement", 0.8, (
        r"quality", r"aspice", r"\bapqp\b", r"\bppap\b", r"iatf", r"16949", r"\bspc\b",
        r"\b8d\b", r"lessons? learned", r"process", r"maturity", r"requirements? management",
        r"traceability", r"audit", r"\bkpi\b", r"\bcmmi\b",
    )),
    Domain("industrialization", "Industrialization & manufacturing", "Industrialisation & production", 1.0, (
        r"industriali", r"manufactur", r"plant", r"production line", r"capacity",
        r"assembly", r"\beol\b", r"end of line", r"\bsmt\b", r"clean ?room", r"automation",
        r"\boee\b", r"cycle time", r"location", r"footprint", r"packag", r"logistic",
        r"supply chain", r"sourcing", r"resilien", r"inventory", r"localization",
    )),
    Domain("planning", "Planning, samples & resources", "Planning, échantillons & ressources", 0.8, (
        r"timing", r"milestone", r"schedule", r"\bsop\b", r"kick[- ]?off", r"sample",
        r"\b[abc][0-3]\b", r"proto", r"gantt", r"timeline", r"team", r"resources?",
        r"organi[sz]ation", r"engineering center", r"\bppap\b", r"launch", r"phase",
    )),
    Domain("options", "Options, alternatives & VAVE", "Options, alternatives & VAVE", 0.6, (
        r"option", r"alternative", r"\bvave\b", r"\bva/ve\b", r"proposal", r"cost reduction",
        r"proposed", r"recommend", r"upgrade", r"downgrade", r"innovation",
    )),
)

DOMAIN_KEYS: tuple[str, ...] = tuple(d.key for d in DOMAINS)
DOMAIN_BY_KEY: dict[str, Domain] = {d.key: d for d in DOMAINS}


FACT_KINDS: dict[str, tuple[str, str]] = {
    "specification": ("Specification / performance value", "Spécification / performance"),
    "design_choice": ("Design choice / component", "Choix de conception / composant"),
    "capability": ("Capability / experience", "Capacité / expérience"),
    "plan": ("Plan / timing / method", "Plan / planning / méthode"),
    "risk": ("Risk / limitation", "Risque / limitation"),
    "deviation": ("Declared deviation", "Écart déclaré"),
    "assumption": ("Assumption / OEM dependency", "Hypothèse / dépendance OEM"),
    "open_point": ("Open point / TBD", "Point ouvert / TBD"),
    "option": ("Option / alternative", "Option / alternative"),
}
FACT_KIND_KEYS: tuple[str, ...] = tuple(FACT_KINDS)


@dataclass(frozen=True)
class Parameter:
    key: str
    label_en: str
    label_fr: str
    unit: str = ""
    better: str = ""  # "higher", "lower" or "" (not orderable)

    def label(self, lang: str) -> str:
        return self.label_fr if lang == "fr" else self.label_en


PARAMETERS: tuple[Parameter, ...] = (
    Parameter("display_size", "Active area diagonal", "Diagonale active", "inch"),
    Parameter("resolution", "Resolution", "Résolution", "px"),
    Parameter("panel_technology", "Panel technology / maker", "Technologie / fabricant dalle"),
    Parameter("luminance", "Luminance", "Luminance", "cd/m²", "higher"),
    Parameter("contrast_ratio", "Contrast ratio", "Taux de contraste", ":1", "higher"),
    Parameter("color_gamut", "Colour gamut", "Gamut couleur", "%", "higher"),
    Parameter("viewing_angle", "Viewing angle", "Angle de vision", "°", "higher"),
    Parameter("response_time", "Response time", "Temps de réponse", "ms", "lower"),
    Parameter("reflectance", "Reflectance", "Réflectance", "%", "lower"),
    Parameter("backlight", "Backlight / local dimming", "Rétroéclairage / local dimming"),
    Parameter("cover_lens", "Cover lens & surface treatment", "Vitre de protection & traitements"),
    Parameter("optical_bonding", "Optical bonding", "Collage optique"),
    Parameter("touch_technology", "Touch technology / controller", "Technologie / contrôleur tactile"),
    Parameter("video_interface", "Video link / interface", "Lien vidéo / interface"),
    Parameter("processor", "SoC / MCU / TCON", "SoC / MCU / TCON"),
    Parameter("power_consumption", "Power consumption", "Consommation", "W", "lower"),
    Parameter("supply_voltage", "Supply voltage range", "Plage de tension", "V"),
    Parameter("operating_temperature", "Operating temperature", "Température de fonctionnement", "°C"),
    Parameter("thickness", "Thickness / depth", "Épaisseur / profondeur", "mm", "lower"),
    Parameter("weight", "Weight", "Masse", "g", "lower"),
    Parameter("housing_material", "Housing material", "Matériau boîtier"),
    Parameter("asil_level", "ASIL level", "Niveau ASIL"),
    Parameter("cybersecurity_standard", "Cybersecurity standard / features", "Norme / fonctions cybersécurité"),
    Parameter("aspice_level", "ASPICE level", "Niveau ASPICE"),
    Parameter("software_stack", "Software stack / OS", "Pile logicielle / OS"),
    Parameter("manufacturing_site", "Manufacturing site", "Site de production"),
    Parameter("sop_date", "SOP / key milestones", "SOP / jalons clés"),
    Parameter("reliability_target", "Reliability / lifetime target", "Objectif fiabilité / durée de vie"),
)
PARAMETER_KEYS: tuple[str, ...] = tuple(p.key for p in PARAMETERS)
PARAMETER_BY_KEY: dict[str, Parameter] = {p.key: p for p in PARAMETERS}

# (expected unit, conflicting unit): a value showing only the conflicting unit was put in the wrong row
# (e.g. a 265.7 x 149.4 mm active area filed as the inch diagonal).
UNIT_GUARDS: dict[str, tuple[re.Pattern, re.Pattern]] = {
    "display_size": (re.compile(r"\"|''|”|″|’’|\binch|\d\s*in\b", re.I), re.compile(r"\d\s*mm\b", re.I)),
    "luminance": (re.compile(r"cd|nit", re.I), re.compile(r"\d\s*(?:mm|ms|°C|W)\b", re.I)),
    "response_time": (re.compile(r"ms\b", re.I), re.compile(r"\d\s*(?:mm|cd|nits?)\b", re.I)),
    "thickness": (re.compile(r"mm\b", re.I), re.compile(r"\d\s*(?:\"|inch|g\b|kg\b)", re.I)),
    "weight": (re.compile(r"\d\s*k?g\b", re.I), re.compile(r"\d\s*mm\b", re.I)),
}


def unit_mismatch(parameter: str, value: str) -> bool:
    guard = UNIT_GUARDS.get(parameter)
    if not guard or not value:
        return False
    expected, conflicting = guard
    return bool(conflicting.search(value)) and not expected.search(value)


# Deterministic detectors (regex) used for the no-LLM fallback and as an
# independent cross-check of LLM-reported values. They only report *mentions*
# with their page; they never decide which value is "the" supplier offer.
PARAMETER_PATTERNS: dict[str, tuple[re.Pattern, ...]] = {
    "resolution": (re.compile(r"\b(\d{3,4})\s*(?:\(?RGB\)?\s*)?[x×*]\s*(\d{3,4})\b", re.I),),
    "luminance": (re.compile(r"(\d{3,4}(?:[.,]\d)?)\s*(?:cd\s*/\s*m\s*[²2]|nits?\b)", re.I),),
    "contrast_ratio": (re.compile(r"(?<![\d:,.])(\d{1,3}(?:[.,\s]\d{3})+|\d{3,7})\s*:\s*1\b"),),
    "color_gamut": (re.compile(r"(\d{2,3}(?:[.,]\d)?)\s*%\s*(?:of\s+)?(NTSC|DCI[- ]?P3|sRGB|Adobe\s*RGB|BT\.?\s*2020)", re.I),),
    "response_time": (re.compile(r"response\s+time[^\n]{0,40}?(\d{1,3}(?:[.,]\d)?)\s*ms", re.I),),
    "power_consumption": (re.compile(r"(?:power|consumption)[^\n]{0,40}?(\d{1,3}(?:[.,]\d{1,2})?)\s*W\b", re.I),),
    "operating_temperature": (re.compile(r"(-\s*\d{2})\s*°?\s*C?\s*(?:~|to|…|\.\.|/|–|-)\s*\+?\s*(\d{2,3})\s*°\s*C", re.I),),
    "asil_level": (re.compile(r"\bASIL[\s-]?([A-D])\b|\b(QM)\b(?=[^\n]{0,30}(?:ASIL|safety|26262))", re.I),),
    "aspice_level": (re.compile(r"\bA-?SPICE\b[^\n]{0,25}?(?:CL|level|L)\s*([1-3])\b", re.I),),
    "video_interface": (re.compile(r"\b(GMSL\s?\d?|FPD[- ]?Link\s?(?:III|IV|3|4)?|APIX\s?\d?|MIPI(?:\s?[A-Z]-PHY)?|eDP|LVDS|HSMT|ASA\s?ML)\b", re.I),),
    "panel_technology": (re.compile(r"\b(BOE|AUO|Innolux|Tianma|LG\s?Display|LGD|Sharp|JDI|CSOT|HannStar|Truly|IVO|Samsung Display|a-Si|LTPS|IGZO|Oxide TFT|OLED|mini[- ]?LED)\b", re.I),),
    "optical_bonding": (re.compile(r"\b(optical(?:ly)?\s+bond\w*|LOCA|OCA|OCR|air\s?gap|direct\s+bonding)\b", re.I),),
    "touch_technology": (re.compile(r"\b(in[- ]?cell|on[- ]?cell|out[- ]?cell|GFF|OGS|GG\b|projected\s+capacitive|PCAP)\b", re.I),),
    "cover_lens": (re.compile(r"\b(Gorilla|Dragontrail|soda[- ]?lime|alumino[- ]?silicate|chemically\s+strengthened|anti[- ]?glare|anti[- ]?reflect\w*|anti[- ]?finger\w*)\b", re.I),),
    "display_size": (re.compile(r"(?<![\d.])(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:\"|''|”|″|’’|inch(?:es)?\b|-inch\b)", re.I),),
    "thickness": (re.compile(r"(?:thickness|depth)[^\n]{0,30}?(\d{1,3}(?:[.,]\d{1,2})?)\s*mm\b", re.I),),
    "weight": (re.compile(r"(?:weight|mass)[^\n]{0,30}?(\d{2,5}(?:[.,]\d{1,2})?)\s*(g|kg)\b", re.I),),
}

COMMERCIAL_PATTERN = re.compile(
    r"\b(piece price|unit price|price|pricing|quotation price|cost breakdown|payment terms|"
    r"incoterms?|\busd\b|\beur\b|€|\$\s?\d|tooling cost|nre cost|invoice)\b", re.I,
)

KIND_HINTS: dict[str, re.Pattern] = {
    "deviation": re.compile(r"\b(deviation|d[ée]viation|not compliant|non[- ]?compliant|exception|cannot meet|not meet|nok\b|partially compliant|écart)\b", re.I),
    "open_point": re.compile(r"\b(tbd|tbc|to be (?:confirmed|defined|discussed|checked)|under (?:investigation|evaluation|discussion)|open (?:point|issue)|pending|clarification)\b", re.I),
    "risk": re.compile(r"\b(risk|concern|critical|challenge|limitation|bottleneck|shortage)\b", re.I),
    "assumption": re.compile(r"\b(assum\w+|hypothes\w+|provided by (?:stla|stellantis|oem|customer)|customer to provide|prerequisite|dependency)\b", re.I),
}


def weights_default() -> dict[str, float]:
    return {d.key: d.weight for d in DOMAINS}


def classify_text(text: str) -> dict[str, int]:
    """Return keyword hit counts per domain (deterministic, explainable)."""
    low = text.casefold()
    scores: dict[str, int] = {}
    for domain in DOMAINS:
        hits = 0
        for kw in domain.keywords:
            hits += len(re.findall(kw, low))
        if hits:
            scores[domain.key] = hits
    return scores
