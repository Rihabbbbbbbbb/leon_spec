# LEON — Conformity Matrix Analyzer: Access Guide

How to open and use the Conformity Matrix web interface — no installation, no coding.

---

## 1. What it does

Suppliers return conformity matrices (ODS/XLSX spreadsheets) marking each requirement **OK**, **NOK** (not conforming), or **NA** (not applicable), with a comment. Reviewing them by hand is slow, and a row marked "OK" can hide a problem in its comment ("pending validation", "partially covered"…).

The third tab (AERIS) goes further: it treats the matrix row as a *claim* and the TDR as *evidence*, then builds a statement crosswalk of what the supplier wrote on each side.

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

The interface has three tabs:

**📊 Matrice de Conformité**
1. **Click or drag** your matrix file (`.ods`, `.xlsx`, `.xlsm`) into the upload zone.
2. Click **📊 Analyser la conformité** — statistics, pie chart, points d'attention and the full requirements table appear on screen.
3. Click **📗 Rapport Excel** to download the color-coded Excel report.

**🔎 Matrix ↔ TDR (AERIS)**
1. Upload the supplier conformity matrix **and** the TDR / PPT / PDF technical dossier.
2. Click **🔎 Cross-check matrix ↔ TDR**. AERIS extracts measurable targets, finds the matching slide/page, compares the numbers, then compares **what the supplier said in the TDR** with **what they declared in the matrix** (opposite OK/NOK, different figures, restated-wrong target, TDR self-conflict).
3. The screen opens on the **incohérences** — one card per requirement, with what Stellantis asks, what the matrix declares, what the TDR says, where, and why it is an incoherence. Conforming requirements are not listed.
4. Download the **📗 Rapport des incohérences (Excel)**. It opens on the `Incohérences` sheet; `Synthèse` and `Détail complet` are there only if you need them.

AERIS reads individual text boxes, table rows, chart values and (when OCR is
available) embedded images in PPTX; PDF pages use positioned text, tables and
image regions. Repeated page furniture and copyright footers are excluded.
Measurements are matched to the *named property* (for example, display refresh
rate rather than any value in Hz), not the first number with the same unit.
Unsupported `.ppt` files must first be exported as `.pptx` or PDF.

OCR of scans and images requires **Tesseract OCR** on the server, with its
executable available on `PATH`, in addition to the Python packages in
`requirements.txt`. Without it, AERIS shows an OCR warning under the results
and treats unread image-only evidence as unverified, **not compliant**. The
Azure Functions deployment must provide the Tesseract executable separately;
installing the Python package alone does not enable OCR. Native vector
diagrams, SmartArt and visual chart trends cannot always be interpreted from
their text/series; these still require human review. If a supplier changes
templates, check the displayed source slide/page and evidence excerpt before
accepting an automated decision.

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

The Matrix ↔ TDR tab calls `POST /api/aeris-crosscheck` (multipart `matrix` + `evidence[]`). The engine lives in `app/qa/aeris_*.py`: deterministic number compare, statement crosswalk, contradiction queue. Azure OpenAI is optional and never overrides a numeric verdict.
Embedding reranking is **off by default** to avoid transmitting supplier TDR
content; set `AERIS_ENABLE_EMBEDDINGS=1` only when the configured embedding
service is approved for that content. See `tests/test_aeris_layout.py` and
`tests/test_aeris_robustness.py` for the presentation and negative-control
fixtures.
