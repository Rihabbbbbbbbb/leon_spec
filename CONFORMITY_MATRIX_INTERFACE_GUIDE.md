# LEON — Conformity Matrix Analyzer: Access Guide

How to open and use the Conformity Matrix web interface — no installation, no coding.

---

## 1. What it does

Suppliers return conformity matrices (ODS/XLSX spreadsheets) marking each requirement **OK**, **NOK** (not conforming), or **NA** (not applicable), with a comment. Reviewing them by hand is slow, and a row marked "OK" can hide a problem in its comment ("pending validation", "partially covered"…).

The analyzer does this automatically. Upload a matrix and it:

- **Finds the data by itself** — the right sheet, header row, and "Conformité FNR" / "Commentaires FNR" columns, even when suppliers change names or layout.
- **Classifies every requirement** OK / NOK / NA / empty and flags ambiguous rows as "needs review".
- **Double-checks every OK with AI** — GPT reads each OK comment and raises a point d'attention only for real problems: comment contradicting the status (error) or partial/pending conformity (warning), each with an explanation and the exact quote.
- **Shows the results on screen** — statistics cards, pie chart, searchable/filterable table of all requirements.
- **Gives you a color-coded Excel report** to download and share (green/red/grey rows, 3 sheets: Summary, All Items, Analyse approfondie des OK).

## 2. How to access it (for everybody)

### Option 1 — Public web address (recommended)

Open this link in any browser (works from anywhere, nothing to install):

> **https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-ui**

The interface has two tabs:

**📊 Matrice de Conformité**
1. **Click or drag** your matrix file (`.ods`, `.xlsx`, `.xlsm`) into the upload zone.
2. Click **📊 Analyser la conformité** — statistics, pie chart, points d'attention and the full requirements table appear on screen.
3. Click **📗 Rapport Excel** to download the color-coded Excel report.

**📄 Validation de Spec**
1. **Click or drag** a specification file (`.docx`, `.pdf`, `.txt`).
2. Click **🔍 Valider la spécification** — verdict (GOOD / ACCEPTABLE / NON COMPLIANT), scores, and detailed findings with fix suggestions appear on screen.
3. Download the **📘 structured Word report** (standardized template, generated for every uploaded file) or the **📕 PDF** version.

That's it. Anyone with the link can use it — share the URL by email or Teams.

> ⚠️ Note: this address is reachable by anyone who has the link (it is not restricted to the company network). Don't publish it outside the team.

### Option 2 — On the local network (when the owner's PC is running it)

If the Azure address is unavailable, anyone on the same network can use the interface served from the project owner's machine:

1. Owner: double-click **`start_conformity_ui.bat`** at the project root. It starts the server and prints the address to share (e.g. `http://10.x.x.x:8012/`). Allow Python through the Windows Firewall if prompted.
2. Colleagues: open that address in their browser. Same interface, same features.

This option only works while the owner's PC is on and the server is running.

## 3. Reading the results

| On screen | Meaning |
|---|---|
| **Total / OK / NOK / NA** cards | Counts of requirements per status. |
| **Points d'attention** | OK rows whose comment looks suspicious — check these first. |
| **Camembert** | Status distribution at a glance. |
| **Analyse approfondie des réponses OK** | Each flagged OK with the signal detected (pending, partial, N/A-in-OK…), colored by severity: red = critical, yellow = to check. |
| **Exigences détaillées** | Every requirement with its REQ-ID, status badge and exact supplier comment. Filter by status or search by keyword. |

In the downloaded Excel: **Summary** (stats + chart), **All Items** (all requirements, rows colored by status, filterable), **Analyse approfondie OK** (the flagged rows).

## 4. For developers (optional)

The interface is a single page ([app/conformity_ui/index.html](app/conformity_ui/index.html)) that calls one endpoint: `POST /api/conformity-excel` (multipart file upload), which returns the full analysis JSON **and** the Excel report in one response. The same page is served two ways:

- Locally by [app/conformity_server.py](app/conformity_server.py) (`python -m app.conformity_server`, page at `/`).
- Publicly by the Azure Function ([azure_function/function_app.py](azure_function/function_app.py), endpoint `GET /api/conformity-ui`, anonymous).

Other API endpoints (PDF report, multi-matrix comparison, Power BI dataset) exist under `/api/conformity*` — see [app/qa/route.py](app/qa/route.py) and the analyzer engine [app/qa/conformity_analyzer.py](app/qa/conformity_analyzer.py).
