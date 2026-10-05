"""
Rapport Excel AERIS — centré sur les incohérences.

Règle de conception : le fichier s'ouvre sur la liste des INCOHÉRENCES,
et rien d'autre n'est nécessaire pour travailler. Une exigence cohérente
n'apparaît pas dans cette feuille.

    1. Incohérences   ← la feuille active à l'ouverture
    2. Synthèse       ← quelques chiffres seulement
    3. Détail complet ← toutes les exigences, pour référence

Pas de code machine en colonne principale (CLAIM_OK_EVIDENCE_FAILS,
PREUVE_INSUFFISANTE…) : chaque ligne est une phrase lisible.
"""
from __future__ import annotations

import io
import re
from copy import copy
from typing import Dict, List

from openpyxl import Workbook
from openpyxl.chart import PieChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


_HEADER_BG = "0B2545"
_GRAVITE_FILL = {
    "1 - Bloquant": "F8C9C5",
    "2 - Majeur": "FBE0C7",
    "3 - A clarifier": "FFF2CC",
    "4 - Information": "DDEBF7",
}
_STATUS_FILL = {
    "CONFORME": "C6EFCE",
    "NON_CONFORME": "FFC7CE",
    "PARTIELLEMENT_CONFORME": "FCE4D6",
    "DEVIATION": "FFF2CC",
    "PREUVE_INSUFFISANTE": "DDEBF7",
    "MANQUANT": "E7E6E6",
    "NA": "D9D9D9",
}
_STATUS_FR = {
    "CONFORME": "Conforme",
    "NON_CONFORME": "Non conforme",
    "PARTIELLEMENT_CONFORME": "Partiellement conforme",
    "DEVIATION": "Déviation",
    "PREUVE_INSUFFISANTE": "Preuve insuffisante",
    "MANQUANT": "Aucune preuve dans le TDR",
    "NA": "Non applicable",
}


def generate_aeris_excel(report: dict) -> bytes:
    """Classeur 3 feuilles, ouvert sur les incohérences."""
    wb = Workbook()
    header_fill = PatternFill("solid", fgColor=_HEADER_BG)
    header_font = Font(color="FFFFFF", bold=True, size=11)
    title_font = Font(color=_HEADER_BG, bold=True, size=14)
    thin = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    wrap = Alignment(wrap_text=True, vertical="top")

    summary = report.get("summary") or {}
    incoherences: List[Dict] = report.get("incoherences") or []
    inco_sum = report.get("incoherenceSummary") or {}
    items: List[Dict] = report.get("items") or []

    # ── 1. Incohérences ──────────────────────────────────────────
    ws = wb.active
    ws.title = "Incohérences"

    ws["A1"] = "AERIS - Incohérences entre la matrice de conformité et le TDR fournisseur"
    ws["A1"].font = title_font
    ws.merge_cells("A1:M1")
    ws["A2"] = (
        f"Matrice : {report.get('matrixFile', '')}   |   "
        f"TDR : {', '.join(report.get('evidenceFiles') or [])}   |   "
        f"{inco_sum.get('total', 0)} incohérence(s) : "
        f"{inco_sum.get('bloquant', 0)} bloquante(s), "
        f"{inco_sum.get('majeur', 0)} majeure(s), "
        f"{inco_sum.get('aClarifier', 0)} à clarifier."
    )
    ws["A2"].font = Font(color="5B6B7C", size=10)
    ws.merge_cells("A2:M2")
    ws["A3"] = (
        "Chaque ligne = une exigence où ce que le fournisseur a déclaré dans la "
        "matrice ne correspond pas à ce qu'il a écrit dans le TDR. "
        "Les exigences cohérentes ne sont pas listées ici."
    )
    ws["A3"].font = Font(color="5B6B7C", size=10, italic=True)
    ws.merge_cells("A3:M3")

    headers = [
        "N°",
        "Gravité",
        "Exigence",
        "Domaine",
        "Ce que Stellantis demande",
        "Ce que le fournisseur a déclaré dans la MATRICE",
        "Ce que le fournisseur a écrit dans le TDR",
        "Où dans le TDR",
        "Pourquoi c'est une incohérence",
        "Écart",
        "Action à demander au fournisseur",
        "Autres motifs sur la même exigence",
        "Confiance",
    ]
    head_row = 5
    for ci, h in enumerate(headers, 1):
        cell = ws.cell(head_row, ci, h)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = thin
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[head_row].height = 34

    keys = [
        "n", "gravite", "req_id", "domaine", "demande", "matrice", "tdr",
        "ou", "pourquoi", "écart", "action", "autres_motifs", "confiance",
    ]
    for ri, row in enumerate(incoherences, head_row + 1):
        fill = PatternFill("solid", fgColor=_GRAVITE_FILL.get(row.get("gravite", ""), "FFFFFF"))
        for ci, key in enumerate(keys, 1):
            cell = ws.cell(ri, ci, row.get(key, ""))
            cell.border = thin
            cell.alignment = wrap
            # Seules les 2 premieres colonnes sont colorees : le reste reste
            # lisible a l'ecran et a l'impression.
            if ci <= 2:
                cell.fill = fill
        ws.cell(ri, 2).font = Font(bold=True)
        ws.cell(ri, 3).font = Font(name="Consolas", bold=True)
        ws.row_dimensions[ri].height = 46

    if not incoherences:
        ws.cell(head_row + 1, 1, (
            "Aucune incohérence détectée. Cela ne certifie pas la conformité ; "
            "vérifier la couverture, les preuves insuffisantes et les remarques."
        ))
        ws.merge_cells(start_row=head_row + 1, start_column=1, end_row=head_row + 1, end_column=13)

    widths = [5, 15, 16, 20, 26, 40, 40, 14, 62, 12, 50, 34, 13]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = f"A{head_row + 1}"
    if incoherences:
        ws.auto_filter.ref = f"A{head_row}:M{head_row + len(incoherences)}"

    notes = report.get("notes") or []
    if notes:
        note_row = head_row + max(len(incoherences), 1) + 2
        ws.cell(note_row, 1, "Remarques : " + " ".join(notes)).font = Font(italic=True, color="9C0006")

    # ── 2. Synthèse ──────────────────────────────────────────────
    wsy = wb.create_sheet("Synthèse")
    wsy["A1"] = "Synthèse"
    wsy["A1"].font = title_font
    wsy["A3"] = "Exigences analysées"
    wsy["B3"] = summary.get("total", 0)
    wsy["A4"] = "Incohérences matrice / TDR"
    wsy["A4"].font = Font(bold=True, color="9C0006")
    wsy["B4"] = inco_sum.get("total", 0)
    wsy["B4"].font = Font(bold=True, color="9C0006")
    wsy["A5"] = "  dont bloquantes"
    wsy["B5"] = inco_sum.get("bloquant", 0)
    wsy["A6"] = "  dont majeures"
    wsy["B6"] = inco_sum.get("majeur", 0)
    wsy["A7"] = "  dont à clarifier"
    wsy["B7"] = inco_sum.get("aClarifier", 0)
    wsy["A9"] = "Taux de conformité (exigences jugeables)"
    wsy["B9"] = f"{summary.get('conformityRate', 0)}%"
    wsy["A10"] = "Couverture par le TDR"
    wsy["B10"] = f"{summary.get('evidenceCoverage', 0)}%"

    wsy["A12"] = "Incohérences par motif"
    wsy["A12"].font = Font(bold=True, color=_HEADER_BG)
    row = 13
    for motif, n in sorted((inco_sum.get("parMotif") or {}).items(), key=lambda kv: -kv[1]):
        wsy.cell(row, 1, motif)
        wsy.cell(row, 2, n)
        row += 1

    risks = report.get("topRisks") or []
    if risks:
        row += 1
        wsy.cell(row, 1, "Domaines les plus à risque").font = Font(bold=True, color=_HEADER_BG)
        row += 1
        for i, r in enumerate(risks, 1):
            wsy.cell(row, 1, f"{i}. {r.get('domain', '')}")
            wsy.cell(row, 2, f"{r.get('hardFails', 0)} non conformite(s) / {r.get('count', 0)} point(s)")
            row += 1

    # Camembert des verdicts, en français.
    labels = [
        ("Conforme", "conforme", "157347"),
        ("Non conforme", "nonConforme", "b3261e"),
        ("Partiellement conforme", "partiel", "c65911"),
        ("Déviation", "deviation", "9C6500"),
        ("Preuve insuffisante", "preuveInsuffisante", "2F5496"),
        ("Aucune preuve", "manquant", "595959"),
        ("Non applicable", "na", "6c757d"),
    ]
    wsy["D3"] = "Statut"
    wsy["E3"] = "Nombre"
    pie_row = 4
    colors = []
    for label, key, color in labels:
        n = summary.get(key, 0)
        if n:
            wsy.cell(pie_row, 4, label)
            wsy.cell(pie_row, 5, n)
            colors.append(color)
            pie_row += 1
    if pie_row > 4:
        pie = PieChart()
        pie.title = "Répartition des verdicts"
        pie.width, pie.height = 14, 9
        pie.add_data(Reference(wsy, min_col=5, min_row=3, max_row=pie_row - 1), titles_from_data=True)
        pie.set_categories(Reference(wsy, min_col=4, min_row=4, max_row=pie_row - 1))
        for i, color in enumerate(colors):
            pt = DataPoint(idx=i)
            pt.graphicalProperties.solidFill = color
            pie.series[0].data_points.append(pt)
        wsy.add_chart(pie, "G3")

    wsy.column_dimensions["A"].width = 46
    wsy.column_dimensions["B"].width = 34
    wsy.column_dimensions["D"].width = 26

    # ── 3. Détail complet ────────────────────────────────────────
    wd = wb.create_sheet("Détail complet")
    dh = [
        "Exigence", "Verdict AERIS", "Matrice", "Ce que dit la matrice",
        "Ce que dit le TDR", "Cible", "Écart", "Où dans le TDR",
        "Incohérence", "Explication", "Libellé de l'exigence",
        "Sources des mesures", "Comparaisons par condition", "Justification",
    ]
    for ci, h in enumerate(dh, 1):
        cell = wd.cell(1, ci, h)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = thin
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    wd.row_dimensions[1].height = 30

    inco_by_id = {r["req_id"]: r for r in incoherences}
    for ri, item in enumerate(items, 2):
        status = item.get("final_status", "")
        inco = inco_by_id.get(item.get("req_id", ""))
        vals = [
            item.get("req_id", ""),
            _STATUS_FR.get(status, status),
            item.get("matrix_status", ""),
            (item.get("matrix_said") or item.get("comment") or "")[:220],
            (item.get("tdr_said") or item.get("supplier_result") or "")[:220],
            item.get("target", ""),
            item.get("gap", ""),
            item.get("evidence_location", ""),
            inco["motif"] if inco else "",
            inco["pourquoi"] if inco else "",
            item.get("description", ""),
            "\n".join(
                f"{s.get('file_name', '')}: {s.get('location', '')}\n{s.get('excerpt', '')}"
                for s in item.get("evidence_sources", [])
            ),
            "\n".join(
                f"{v.get('condition', '')}: {v.get('target', '')} / "
                f"{v.get('measured', '') or 'aucune mesure'} -> {v.get('status', '')}"
                f" ({v.get('evidence_file', '')}: {v.get('evidence_location', '')})"
                for v in item.get("condition_verdicts", [])
            ),
            item.get("rationale", ""),
        ]
        fill = PatternFill("solid", fgColor=_STATUS_FILL.get(status, "FFFFFF"))
        for ci, val in enumerate(vals, 1):
            cell = wd.cell(ri, ci, val)
            cell.border = thin
            cell.alignment = wrap
            if ci <= 2:
                cell.fill = fill
        if inco:
            wd.cell(ri, 9).fill = PatternFill("solid", fgColor="F8C9C5")

    for i, w in enumerate([16, 24, 12, 38, 38, 20, 12, 14, 34, 56, 44, 50, 50, 60], 1):
        wd.column_dimensions[get_column_letter(i)].width = w
    wd.freeze_panes = "A2"
    if items:
        wd.auto_filter.ref = f"A1:N{len(items) + 1}"

    wb.active = 0
    if report.get("engineVersion") == "tdr-review-1":
        _add_review_annex(wb, report)
        wb.active = wb.sheetnames.index("Revue humaine")
    _neutralise_formulas(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _add_review_annex(wb, report: dict) -> None:
    import json

    def write_sheet(title, headers, rows):
        ws = wb.create_sheet(title)
        ws.append(headers)
        for row in rows:
            values = [
                json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                for v in row
            ]
            ws.append([
                v[:32650] + " [TRUNCATED: full content in JSON export]"
                if isinstance(v, str) and len(v) > 32767 else v
                for v in values
            ])
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="23445D")
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = 30
            for cell in column:
                cell.alignment = Alignment(wrap_text=True, vertical="top")

    items = report["items"]
    write_sheet("Revue humaine",
                ["Row key", "Requirement", "Source matrix", "AI proposal (immutable)",
                 "Technical verdict", "Scope", "Human result", "Action", "Reviewer (unverified)",
                 "Time UTC", "Revision", "Comment", "Review reasons", "Original requirement"],
                [
                    [i["row_key"], i["req_id"], i["matrix_source"], i["proposal_status"],
                     i["final_status"], i["scope_status"], (i.get("human_decision") or {}).get("result_status"),
                     (i.get("human_decision") or {}).get("action"),
                     (i.get("human_decision") or {}).get("reviewer"),
                     (i.get("human_decision") or {}).get("recordedAt"),
                     i["review_revision"], (i.get("human_decision") or {}).get("comment"),
                     i["escalation_reasons"], i["description"]] for i in items
                ])
    write_sheet("Historique",
                ["Row key", "Requirement", "Revision", "UTC", "Reviewer (unverified)",
                 "Action", "Original AI proposal", "Human result", "Comment", "Technical acceptance"],
                [[i["row_key"], i["req_id"], h["revision"], h["recordedAt"], h["reviewer"],
                  h["action"], h["original_proposal"], h["result_status"], h["comment"], False]
                 for i in items for h in i.get("review_history", [])])
    write_sheet("Documents et perimetre", ["Type", "Value"],
                [["Case ID", report.get("caseId")], ["Engine", report["engineVersion"]],
                 ["Scope", report["scope"]], ["Schema", report["schemaMapping"]],
                 ["No automatic acceptance", True]] +
                [["Document", doc] for doc in report["documents"]] +
                [["Limitation", note] for note in report["limitations"]])


# Excel interprète une cellule commençant par = + - @ comme une formule.
# Le texte vient du dossier d'un fournisseur externe : il ne doit jamais
# s'exécuter à l'ouverture du rapport (CWE-1236).
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _neutralise_formulas(wb) -> None:
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                value = cell.value
                if not isinstance(value, str) or not value.startswith(_FORMULA_LEAD):
                    continue
                # "-40 °C" reste un nombre lisible, pas une formule.
                if value[:1] in ("-", "+") and re.match(r"^[-+]?\d", value):
                    continue
                style = copy(cell._style)
                style.quotePrefix = True
                cell._style = style
                # openpyxl marks strings beginning with "=" as formulas when
                # they are assigned. quotePrefix affects display only; leaving
                # data_type="f" emits an invalid/untrusted formula that Excel
                # removes while repairing the workbook.
                cell.data_type = "s"
