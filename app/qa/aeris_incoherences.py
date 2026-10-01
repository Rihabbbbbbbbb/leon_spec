"""
AERIS — la liste des incohérences, en français, lisible par un qualiticien.

Le reste du moteur produit des statuts machine (CLAIM_OK_EVIDENCE_FAILS,
PREUVE_INSUFFISANTE, MATRIX_TOO_OPTIMISTIC…). Personne ne relit un
fournisseur avec ça.

Ce module transforme tout cela en UNE ligne par exigence incohérente :

    Ce que Stellantis demande | Ce que le fournisseur a déclaré dans la
    matrice | Ce qu'il a écrit dans le TDR | Où | Pourquoi c'est une
    incohérence | Quelle action demander

Une exigence conforme et cohérente n'apparaît jamais ici. Un NOK honnête
(la matrice dit NOK et le TDR le confirme) n'est pas une incohérence :
c'est une non-conformité assumée, elle est suivie ailleurs.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from app.qa.aeris_constraints import pretty_value


# Gravité lisible. L'ordre de tri est l'ordre de lecture du relecteur.
_GRAVITE = {
    "critical": "1 - Bloquant",
    "high": "2 - Majeur",
    "medium": "3 - À clarifier",
    "info": "4 - Information",
}
_RANK = {"critical": 3, "high": 2, "medium": 1, "info": 0}

# Libellé court du motif, en français.
_MOTIF = {
    "CLAIM_OK_EVIDENCE_FAILS": "Matrice OK, mais le TDR ne tient pas l'exigence",
    "CLAIM_OK_PARTIAL": "Matrice OK, mais le TDR n'est conforme que partiellement",
    "CLAIM_OK_TDR_SAYS_NOK": "Matrice OK, mais le TDR écrit lui-même NOK / Déviation",
    "CLAIM_OK_NO_EVIDENCE": "Matrice OK, mais aucune preuve dans le TDR",
    "CLAIM_NOK_EVIDENCE_PASSES": "Matrice NOK/Déviation, mais le TDR atteint la cible",
    "CLAIM_NOK_TDR_SAYS_OK": "Matrice NOK, mais le TDR écrit OK",
    "COMMENT_CONTRADICTS_TDR": "Le commentaire matrice et le TDR ne donnent pas les mêmes chiffres",
    "VALUE_MISMATCH": "Le commentaire matrice et le TDR ne donnent pas les mêmes chiffres",
    "DECLARATION_OPPOSITE": "La matrice et le TDR disent l'inverse l'un de l'autre",
    "TDR_RESTATES_WRONG_TARGET": "Le TDR répond à une cible différente de l'exigence",
    "TDR_INTERNAL_CONFLICT": "Le TDR se contredit lui-même",
}


def build_incoherences(report) -> List[Dict]:
    """
    Une ligne par exigence incohérente, triée par gravité.

    `report` est un CrossCheckReport (dataclass), pas un dict : on a besoin
    des items, du crosswalk et de la file de contradictions.
    """
    items = {i.req_id: i for i in report.items if i.req_id}
    walks = {w.req_id: w for w in report.crosswalk if w.req_id}

    grouped: Dict[str, List] = {}
    for c in report.contradictions:
        if not c.req_id:
            continue
        grouped.setdefault(c.req_id, []).append(c)

    rows: List[Dict] = []
    for req_id, found in grouped.items():
        found = sorted(found, key=lambda c: -_RANK.get(c.severity, 0))
        main = found[0]
        item = items.get(req_id)
        walk = walks.get(req_id)

        demande = _demande(item, walk, main)
        matrice = _matrice(item, walk, main)
        tdr = _tdr(item, walk, main)
        ou = main.location or (item.evidence_location if item else "")

        rows.append({
            "gravite": _GRAVITE.get(main.severity, main.severity),
            "req_id": req_id,
            "domaine": main.domain or (item.domain if item else ""),
            "motif": _MOTIF.get(main.type, main.title),
            "exigence": (item.description if item else "")[:300],
            "demande": demande,
            "matrice": matrice,
            "tdr": tdr,
            "ou": ou,
            "pourquoi": _pourquoi(main, item, walk, demande, matrice, tdr, ou),
            "écart": _écart(item, main),
            "action": _action(main),
            "autres_motifs": "; ".join(
                _MOTIF.get(c.type, c.title) for c in found[1:]
            ),
            "extrait_tdr": (main.excerpt or "").replace("\n", " ")[:300],
            "confiance": _confiance(main.confidence),
            "code": main.type,
            "_severity": main.severity,
        })

    rows.sort(key=lambda r: (-_RANK.get(r["_severity"], 0), r["req_id"]))
    for n, row in enumerate(rows, 1):
        row["n"] = n
    return rows


def incoherence_summary(rows: Sequence[Dict]) -> Dict:
    par_motif: Dict[str, int] = {}
    bloquant = majeur = clarifier = 0
    for r in rows:
        par_motif[r["motif"]] = par_motif.get(r["motif"], 0) + 1
        sev = r.get("_severity")
        if sev == "critical":
            bloquant += 1
        elif sev == "high":
            majeur += 1
        else:
            clarifier += 1
    return {
        "total": len(rows),
        "bloquant": bloquant,
        "majeur": majeur,
        "aClarifier": clarifier,
        "parMotif": par_motif,
    }


# ── rédaction ──────────────────────────────────────────────────────

def _demande(item, walk, contra) -> str:
    target = contra.target or (item.target if item else "") or (
        walk.stellatis_target if walk else ""
    )
    return pretty_value(target)


def _matrice(item, walk, contra) -> str:
    statut = contra.matrix_status or (item.matrix_status if item else "")
    commentaire = ""
    if item and item.comment:
        commentaire = item.comment.strip()
    elif contra.claimed:
        commentaire = str(contra.claimed).strip()
    statut = {"OK": "OK", "NOK": "NOK", "NA": "NA", "DEVIATION": "DEVIATION",
              "EMPTY": "(case vide)"}.get(statut, statut or "(case vide)")
    commentaire = _clean(commentaire)
    if commentaire and commentaire.upper() != statut.upper():
        return f"{statut} - {commentaire}"
    return statut


def _tdr(item, walk, contra) -> str:
    mesure = (item.supplier_result if item else "") or ""
    if mesure:
        return _clean(mesure)
    if walk and walk.tdr_said and not walk.tdr_said.startswith("("):
        return _clean(walk.tdr_said)
    if contra.type == "CLAIM_OK_NO_EVIDENCE":
        return "Rien d'exploitable dans le TDR"
    return _clean(str(contra.evidenced or ""))


def _clean(text: str) -> str:
    """Une seule ligne, sans la reference de slide (colonne dediee)."""
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    out = re.sub(r"\s*\((?:Slide|Page|Block|Section)\s+[^)]*\)\s*$", "", out, flags=re.I)
    out = re.sub(r"\s*\[(?:FAIL|PASS|MIXED|NONE)\]\s*", " ", out)
    out = pretty_value(out)
    # Ne jamais rogner un "-" de tete : c'est un signe, pas une puce (-40 °C).
    out = re.sub(r"^[\s;,]+|[\s;,-]+$", "", out)
    return out[:200]


def _écart(item, contra) -> str:
    if item and item.gap:
        return pretty_value(item.gap)
    return ""


def _confiance(code: str) -> str:
    return {
        "VERY_HIGH": "Très élevée", "HIGH": "Élevée", "MEDIUM": "Moyenne",
        "LOW": "Faible", "NONE": "Aucune preuve",
    }.get(code or "", code or "")


def _pourquoi(contra, item, walk, demande, matrice, tdr, ou) -> str:
    """Une phrase complète qui explique l'incohérence, sans code machine."""
    ref = f" ({ou})" if ou else ""
    kind = contra.type

    if kind == "CLAIM_OK_EVIDENCE_FAILS":
        return (
            f"La matrice déclare OK, mais le TDR{ref} donne {tdr} alors que "
            f"l'exigence demande {demande}. Le OK n'est pas démontré."
        )
    if kind == "CLAIM_OK_PARTIAL":
        detail = _points(item)
        return (
            f"La matrice déclare OK, mais le TDR{ref} n'atteint la cible "
            f"{demande} que sur une partie des points de fonctionnement"
            + (f" : {detail}." if detail else ".")
        )
    if kind == "CLAIM_OK_TDR_SAYS_NOK":
        return (
            f"La matrice déclare OK alors que le TDR lui-même{ref} écrit "
            f"NOK / Déviation sur ce sujet."
        )
    if kind == "CLAIM_OK_NO_EVIDENCE":
        return (
            "La matrice déclare OK mais le TDR ne contient aucune preuve "
            "exploitable : c'est une affirmation, pas une conformité démontrée."
        )
    if kind == "CLAIM_NOK_EVIDENCE_PASSES":
        return (
            f"La matrice déclare {contra.matrix_status}, mais le TDR{ref} donne "
            f"{tdr} qui respecte l'exigence {demande}. Matrice probablement "
            f"obsolète."
        )
    if kind == "CLAIM_NOK_TDR_SAYS_OK":
        return (
            f"La matrice déclare NOK alors que le TDR{ref} écrit OK sur ce point."
        )
    if kind in ("VALUE_MISMATCH", "COMMENT_CONTRADICTS_TDR"):
        commentaire = _clean(item.comment if item else "") or matrice
        return (
            f"Le commentaire de la matrice annonce \"{commentaire}\", alors que "
            f"le TDR{ref} indique {tdr} pour la même grandeur. Les deux "
            f"documents du fournisseur ne disent pas la même chose."
        )
    if kind == "DECLARATION_OPPOSITE":
        return (
            f"La matrice et le TDR{ref} se contredisent : la matrice conclut "
            f"{matrice} et le TDR conclut l'inverse."
        )
    if kind == "TDR_RESTATES_WRONG_TARGET":
        cible = _clean(walk.restated_target) if walk else ""
        return (
            f"Le TDR{ref} répond à la cible {cible}, alors que l'exigence "
            f"Stellantis demande {demande}. Le fournisseur n'a pas traité la "
            f"bonne limite."
        )
    if kind == "TDR_INTERNAL_CONFLICT":
        conflit = _clean(walk.tdr_internal_conflict) if walk else ""
        return (
            f"Le TDR se contredit lui-même sur cette grandeur : {conflit}. "
            f"Impossible de valider la declaration de la matrice tant que le "
            f"fournisseur n'a pas tranché."
        )
    return contra.title or ""


def _points(item) -> str:
    if not item or not item.condition_verdicts:
        return ""
    bits = []
    for v in item.condition_verdicts[:4]:
        cond = v.get("condition") or "nominal"
        statut = "conforme" if v.get("status") == "CONFORME" else "non conforme"
        bits.append(f"{pretty_value(cond)} {pretty_value(v.get('measured',''))} {statut}")
    return " ; ".join(bits)


def _action(contra) -> str:
    return {
        "CLAIM_OK_EVIDENCE_FAILS":
            "Refuser le OK. Demander soit une demande de dérogation formelle, "
            "soit une correction technique.",
        "CLAIM_OK_PARTIAL":
            "Demander au fournisseur de preciser les points de fonctionnement "
            "couverts et de traiter ceux qui échouent.",
        "CLAIM_OK_TDR_SAYS_NOK":
            "Faire corriger la matrice : le dossier reconnaît déjà l'écart.",
        "CLAIM_OK_NO_EVIDENCE":
            "Demander la slide ou le rapport d'essai qui justifie ce OK avant "
            "de l'accepter.",
        "CLAIM_NOK_EVIDENCE_PASSES":
            "Demander confirmation : soit la matrice est obsolète, soit la "
            "slide TDR ne correspond pas au bon point de mesure.",
        "CLAIM_NOK_TDR_SAYS_OK":
            "Demander quel document fait foi.",
        "VALUE_MISMATCH":
            "Ne retenir aucun des deux chiffres tant que le fournisseur n'a pas "
            "aligné la matrice et le TDR.",
        "COMMENT_CONTRADICTS_TDR":
            "Ne retenir aucun des deux chiffres tant que le fournisseur n'a pas "
            "aligné la matrice et le TDR.",
        "DECLARATION_OPPOSITE":
            "Contester la declaration de la matrice : le dossier dit l'inverse.",
        "TDR_RESTATES_WRONG_TARGET":
            "Rappeler la cible contractuelle et demander une nouvelle reponse "
            "sur la bonne limite.",
        "TDR_INTERNAL_CONFLICT":
            "Demander quelle valeur du TDR fait foi avant toute validation.",
    }.get(contra.type, contra.action or "")
