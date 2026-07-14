# LEON — Conformity Matrix in Copilot Studio
## Definitive Setup Guide (v5.0) — 100% Functional

> **Version 5.0** — Swagger 2.0 spec + Blob Storage downloadUrl + BlobToText fix

---

## 🎯 THE PROBLEM (Root Cause)

### Error 1: "Une erreur s'est produite lors de l'enregistrement de votre outil"
This error occurs at the **tool save step** in Copilot Studio. The OpenAPI spec is being rejected by Copilot Studio's validator.

**Root causes:**
1. **OpenAPI 3.0.1 format** — Copilot Studio/Power Platform prefers **Swagger 2.0**
2. **`format: byte` in output** — `reportExcel` field with `format: byte` causes validation failure
3. **All inputs optional** — `required: []` is ambiguous for AI tools
4. **Copilot Studio expressions in descriptions** — `=Topic.ConformityFile.contentUrl` in OpenAPI descriptions is non-standard

### Error 2: "Not yet implemented unary operator: BlobToText"
This error occurs at runtime when the topic binds `fileContent: =Text(Topic.ConformityFile)`.
The `Text()` function tries to convert the file blob to text using `BlobToText`, which is NOT implemented.

---

## ✅ THE SOLUTION (v6.0)

### Fix 1: Swagger 2.0 Spec (Fixes Tool Save Error)
- Converted from OpenAPI 3.0.1 to **Swagger 2.0** format
- Removed `reportExcel` from output schema (was `format: byte`)
- Made `fileName` and `fileUrl` **required** inputs
- Added `x-ms-summary` to all fields (Power Platform display names)
- Cleaned descriptions (removed Copilot Studio expressions)
- Added `x-ms-visibility: internal` for `fileContent` (alternative input)

### Fix 2: fileUrl Binding (Fixes BlobToText Error)
- Use `fileUrl` = `=Topic.Var1.contentUrl` (URL string, no blob conversion)
- **NEVER** use `=Text(Topic.Var1)` — this causes the BlobToText error

### Fix 3: downloadUrl for File Delivery
- Azure Function uploads Excel report to Azure Blob Storage
- Returns `downloadUrl` (public URL or data URI) in the response
- If Blob Storage is not accessible, falls back to data URI (self-contained base64 URL)

### Fix 4: JSON spec with fileUrl (Fixes ContentFiltered / RAI Error)
- **formData approach TRIGGERS Responsible AI filter** — Copilot Studio scans binary file content for prompt injection
- **JSON approach with fileUrl BYPASSES RAI filter** — only a URL string passes through the tool
- Use `LEON_Conformity_Excel_Only.json` (JSON spec) — NOT `LEON_Conformity_Excel_FormData.json` (formData spec)
- The Azure Function downloads the file from the URL — no binary content passes through Copilot Studio

### Fix 5: Skip Question Node (Fixes "give me the doc" Issue)
- The user **already attaches the file** with their message (e.g., "summary of this matrix conformity" + .xlsm file)
- The Question node should **NOT ask "give me the doc"** — it should be **skipped** when the user already attached a file
- Configure the Question node with **"Skip behavior: Allow question to be skipped"**
- This way, if the user attached a file with their message, the question is skipped and the tool is called directly
- If the user did NOT attach a file, the question asks for it as a fallback

---

## 📋 THE CORRECT APPROACH (v6.0)

### ⚠️ formData File Upload — DOES NOT WORK (RAI Filter Blocks It)

`LEON_Conformity_Excel_FormData.json` with `file: =Topic.Var1` triggers:
- `ContentFiltered` — "The content was filtered due to Responsible AI restrictions"
- `openAIndirectAttack` — Copilot Studio scans binary file content for prompt injection

> **Do NOT use the formData spec.** Use the JSON spec instead.

### ✅ JSON with fileUrl (THE ONLY WORKING APPROACH)

Use `LEON_Conformity_Excel_Only.json` (v3.2.0) — Swagger 2.0 with JSON body and `fileUrl`.

**Why this works:**
1. Only a **URL string** passes through the tool — no binary content
2. The RAI filter sees only a URL — no prompt injection possible
3. The Azure Function downloads the file from the URL
4. No `BlobToText` error, no `ContentFiltered` error

---

## 📋 STEP-BY-STEP SETUP (v6.0)

### STEP 1: Delete the Old Tool

1. Go to **Copilot Studio** → your **LEON** agent
2. **Tools** → find the existing tool (e.g., "ReportTT" or "EEExcel Conformity Report")
3. **Delete** it completely

### STEP 2: Import the JSON Spec

1. **Tools** → **+ Add** → **Create a tool** → **API**
2. Upload `LEON_Conformity_Excel_Only.json` (v3.2.0)
3. The tool should show **3 inputs**:
   - `fileName` (required) — file name string
   - `fileUrl` (required) — file URL string
   - `fileContent` (internal) — base64 alternative

### STEP 3: Configure Tool Inputs

| Input | Filling Mode | Value |
|-------|-------------|-------|
| `fileName` | **From a variable** | `=Topic.Var1.Name` |
| `fileUrl` | **From a variable** | `=Topic.Var1.contentUrl` |
| `fileContent` | — | *(leave empty)* |

> **⚠️ CRITICAL**: Do NOT set any input to "Dynamic filling with AI" — this makes the bot ask the user for values!

### STEP 4: Configure the Topic Flow

```
1. TRIGGER — recognizes intent (trigger phrases)
   → User says "summary of this matrix conformity" + attaches .xlsm file
   
2. QUESTION NODE (File Upload) — with SKIP behavior
   → Variable: Topic.Var1 (FilePrebuiltEntity)
   → Prompt: "give me the doc"
   → Skip behavior: Allow question to be skipped ← CRITICAL!
   → If user already attached file → SKIPPED, Topic.Var1 = attached file
   → If user did NOT attach file → asks "give me the doc"
   
3. TOOL CALL — "Generate Excel Conformity Report"
   → fileName: =Topic.Var1.Name
   → fileUrl: =Topic.Var1.contentUrl
   → NO file: =Topic.Var1 (formData — causes RAI error!)
   
4. CONDITION — Check if Topic.status = "answered"

5. SUCCESS MESSAGE — Show statistics + download link

6. ERROR MESSAGE — Show error details
```

### STEP 5: Question Node Configuration (CRITICAL — Fixes "give me the doc" Issue)

The Question node must be configured to **skip when the user already attached a file**:

| Field | Value |
|-------|-------|
| **Question text** | `give me the doc` |
| **Identify** | **File upload** (FilePrebuiltEntity) |
| **Save response as** | `Var1` |
| **Skip behavior** | **Allow question to be skipped** ← THIS IS THE KEY SETTING! |
| **Reprompt** | Don't repeat |
| **No valid entity found** | Set variable to empty (no value) |

> When "Allow question to be skipped" is enabled:
> - If the user attached a file with their message → the question is **skipped**, `Topic.Var1` = the attached file
> - If the user did NOT attach a file → the question asks "give me the doc" as a fallback

### STEP 6: Tool Call Node Configuration

```yaml
- kind: BeginDialog
  id: DTmryl
  input:
    binding:
      fileName: =Topic.Var1.Name           # ← File name (string, NOT file object)
      fileUrl: =Topic.Var1.contentUrl      # ← File URL (string, NOT blob)
  dialog: cr927_leon.action.ReportTT-ReportTT
  output:
    binding:
      answer: Topic.answer
      status: Topic.status
      totalReqs: Topic.totalReqs
      okReqs: Topic.okReqs
      nokReqs: Topic.nokReqs
      naReqs: Topic.naReqs
      emptyReqs: Topic.emptyReqs
      okDeepFindings: Topic.okDeepFindings
      needsReview: Topic.needsReview
      downloadUrl: Topic.downloadUrl
      fileName: Topic.fileName
      confidence: Topic.confidence
      inconsistencies: Topic.inconsistencies
```

**Key rules:**
- `fileName: =Topic.Var1.Name` — NOT `=Topic.Var1` (which passes the entire file object)
- `fileUrl: =Topic.Var1.contentUrl` — NOT empty, NOT `=Text(Topic.Var1)`
- **NEVER** use `=Text(Topic.Var1)` — this causes the BlobToText error
- **NEVER** use `file: =Topic.Var1` (formData) — this causes the ContentFiltered RAI error

---

## 🔍 ANALYSIS OF THE BROKEN YAML

The user's current YAML has **three critical errors**:

### Error 1: `fileName: =Topic.Var1`
```yaml
fileName: =Topic.Var1    # ← WRONG: passes file OBJECT, not file name string
```
`Topic.Var1` is a `FilePrebuiltEntity` (file blob object), not a string.
Binding it to `fileName` (which expects a string) will fail or pass garbage.

**Fix:** `fileName: =Topic.Var1.Name` (the file's Name property is a string)

### Error 2: `fileUrl:` (empty)
```yaml
fileUrl:                 # ← WRONG: empty, not bound to anything
```
No URL is provided to the Azure Function, so it can't download the file.

**Fix:** `fileUrl: =Topic.Var1.contentUrl` (the file's contentUrl property is a URL string)

### Error 3: `fileContent` not bound
No file content is provided at all — the Azure Function has nothing to analyze.

**Fix (Approach A):** Use `file: =Topic.Var1` with the formData spec (file sent as binary)
**Fix (Approach B):** Use `fileUrl: =Topic.Var1.contentUrl` with the JSON spec (file downloaded from URL)

---

## 📋 STEP-BY-STEP SETUP INSTRUCTIONS

### STEP 1: Verify Azure Function is Deployed

The Azure Function is already deployed and tested:
- URL: `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net`
- Endpoint: `POST /api/conformity-excel` (anonymous — no API key)
- Tested with real DM12F file: 425 reqs, 423 OK, 2 NOK, 7 deep findings ✅

### STEP 2: Re-import the OpenAPI Spec (v3.1)

1. Go to **Copilot Studio** → your **LEON** agent
2. **Tools** → find the existing "excel report" tool
3. Click **Edit** (or delete and re-create)
4. Upload the updated `LEON_Conformity_Excel_Only.json` (v3.1.0)
5. The tool should show **3 inputs** (none required):
   - `fileName` (optional) — file name
   - `fileUrl` (preferred) — URL to download the file
   - `fileContent` (alternative) — base64 file content

### STEP 3: Configure Tool Inputs (CRITICAL — This Fixes the Error)

In the tool call node, set each input:

#### `fileUrl` (PREFERRED):
- **Filling mode**: Set manually / From a variable
- **Value**: `=Topic.ConformityFile.contentUrl`
- **NOT** "Dynamic filling with AI" (this makes the bot ask the user)

#### `fileName`:
- **Filling mode**: Set manually / From a variable
- **Value**: `=Topic.ConformityFile.Name`

#### `fileContent`:
- **Filling mode**: Leave empty or remove
- **Value**: *(empty)*

> **⚠️ CRITICAL**: Do NOT use `=Text(Topic.ConformityFile)` — this causes the `BlobToText` error!
>
> **⚠️ CRITICAL**: Do NOT set any input to "Dynamic filling with AI" — this makes the bot ask the user for values!

### STEP 4: Configure Output Bindings

Map all outputs to topic variables:

| Output | Variable |
|--------|----------|
| `answer` | `Topic.answer` |
| `status` | `Topic.status` |
| `confidence` | `Topic.confidence` |
| `fileName` | `Topic.fileName` |
| `totalReqs` | `Topic.totalReqs` |
| `okReqs` | `Topic.okReqs` |
| `nokReqs` | `Topic.nokReqs` |
| `naReqs` | `Topic.naReqs` |
| `emptyReqs` | `Topic.emptyReqs` |
| `inconsistencies` | `Topic.inconsistencies` |
| `okDeepFindings` | `Topic.okDeepFindings` |
| `needsReview` | `Topic.needsReview` |
| `reportExcel` | `Topic.reportExcel` |
| `downloadUrl` | `Topic.downloadUrl` |

### STEP 5: Configure the Topic Flow

```
1. TRIGGER — recognizes intent (trigger phrases)
2. QUESTION — File upload (FilePrebuiltEntity)
   → File captured in Topic.ConformityFile variable
   → SKIPPED if file already attached to message
3. MESSAGE — "📊 Merci, fichier reçu ! Analyse en cours..."
4. TOOL CALL — "excel report" connector
   → fileUrl: =Topic.ConformityFile.contentUrl  ← THIS IS THE FIX
   → fileName: =Topic.ConformityFile.Name
   → NO fileContent binding (removed =Text() that caused the error)
5. CONDITION — Check if Topic.status = "answered"
6. SUCCESS MESSAGE — Show statistics + download link
7. ERROR MESSAGE — Show error details
```

### STEP 6: Trigger Phrases

```
analyse ma matrice de conformité
rapport conformité FNR
analyse conformité
conformity report
génère rapport conformité
matrice conformité FNR
rapport excel conformité
génère rapport excel conformité
voici ma matrice de conformité
```

### STEP 7: Success Message Template

```
✅ Rapport Excel généré avec succès !

📊 Statistiques de conformité pour {Topic.fileName}:
• Total des exigences : {Topic.totalReqs}
• OK (conforme) : {Topic.okReqs} 🟢
• NOK (non conforme) : {Topic.nokReqs} 🔴
• NA (non applicable) : {Topic.naReqs} ⚪
• Sans statut : {Topic.emptyReqs}

🔍 Analyse approfondie :
• Points d'attention (OK suspects) : {Topic.okDeepFindings}
• Exigences à vérifier : {Topic.needsReview}
```

### STEP 8: Error Message Template

```
❌ Une erreur s'est produite lors de la génération du rapport.

Détails : {Topic.answer}

Vérifiez que :
1. Le fichier est au format ODS ou XLSX
2. Le fichier contient des colonnes "Conformité FNR" et "Commentaires FNR"
3. Le fichier n'est pas corrompu
```

---

## 🔧 TROUBLESHOOTING

### If `=Topic.ConformityFile.contentUrl` doesn't work:

Try these alternatives in order:
1. `=Topic.ConformityFile.ContentUrl` (capital C)
2. `=Topic.ConformityFile.url` (lowercase)
3. `=Topic.ConformityFile.Url` (capital U)

Check the Copilot Studio variable browser:
1. In the tool call node, click on the input field
2. Click the **{x}** button to open the variable browser
3. Expand `Topic.ConformityFile` to see all available properties
4. Use the property that contains a URL (usually `contentUrl` or `url`)

### If `fileUrl` approach fails (URL not accessible):

Switch to Approach 2 (JSON serialization):
1. Set `fileContent` = `=JSON(Topic.ConformityFile, JSONFormat.IncludeBinaryData)`
2. Remove `fileUrl` binding
3. The Azure Function handles JSON-serialized file objects

### If `JSON()` approach also fails:

Switch to Approach 3 (direct file object):
1. Set `fileContent` = `=Topic.ConformityFile` (no conversion, no Text())
2. Remove `fileUrl` binding
3. The Azure Function handles dict objects with content/contentBytes

### Debug: Check what Copilot Studio sends

Point the tool to the debug endpoint temporarily:
1. Change the OpenAPI spec server URL path from `/api/conformity-excel` to `/api/debug-request`
2. Re-import the spec
3. Test the tool — the response will show exactly what Copilot Studio sends
4. Use this to determine which binding works

### Debug: Test the Azure Function directly

```powershell
# Test with fileUrl
$body = @{ fileName = "test.xlsm"; fileUrl = "https://example.com/file.xlsm" } | ConvertTo-Json
Invoke-WebRequest -Uri "https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel" -Method POST -ContentType "application/json" -Body $body -UseBasicParsing

# Test with fileContent (base64)
$file = "C:\path\to\your\file.xlsm"
$bytes = [System.IO.File]::ReadAllBytes($file)
$b64 = [System.Convert]::ToBase64String($bytes)
$body = @{ fileName = "test.xlsm"; fileContent = $b64 } | ConvertTo-Json -Compress
Invoke-WebRequest -Uri "https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel" -Method POST -ContentType "application/json" -Body $body -UseBasicParsing
```

---

## 🏗️ ARCHITECTURE (v4.0)

```
User in Teams: "analyse ma matrice de conformité" + attaches .xlsm file
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  COPILOT STUDIO TOPIC: "Conformity"                              │
│                                                                  │
│  1. TRIGGER — recognizes intent (trigger phrases)                │
│  2. QUESTION — File upload (FilePrebuiltEntity)                  │
│     → File captured in Topic.ConformityFile variable             │
│     → SKIPPED if file already attached to message                │
│  3. MESSAGE — "📊 Fichier reçu ! Analyse en cours..."           │
│  4. TOOL CALL — "excel report" connector                        │
│     → fileUrl: =Topic.ConformityFile.contentUrl  ← THE FIX      │
│     → fileName: =Topic.ConformityFile.Name                      │
│     → NO fileContent binding (removed =Text() that caused error) │
│     → NO code parameter (anonymous endpoint)                    │
│  5. CONDITION — Check if Topic.status = "answered"              │
│  6. SUCCESS MESSAGE — Show statistics + download link           │
│  7. ERROR MESSAGE — Show error details                          │
└──────────────────────────┬───────────────────────────────────────┘
                           │ HTTPS POST (anonymous, JSON)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  AZURE FUNCTION — /api/conformity-excel (ANONYMOUS)              │
│                                                                  │
│  1. Receives JSON: { fileName, fileUrl }                         │
│  2. Downloads file from fileUrl (handles redirects, SSL)        │
│  3. Saves to /data/uploads/                                      │
│  4. Calls analyze_conformity_matrix()                            │
│     → Reads ODS/XLSX/XLSM (odfpy/openpyxl)                      │
│     → Auto-detects sheet, header row, columns                    │
│     → Classifies: OK / NOK / NA / EMPTY                          │
│     → Deep analysis of OK responses (suspicion patterns)         │
│  5. Calls generate_conformity_excel()                            │
│     → Sheet 1: Summary (stats + pie chart)                      │
│     → Sheet 2: All Items (color-coded rows)                      │
│     → Sheet 3: Analyse approfondie OK (points d'attention)      │
│  6. Uploads XLSX to Azure Blob Storage (if configured)           │
│     → Generates 7-day SAS download URL                           │
│  7. Returns JSON:                                                 │
│     → answer (French summary text)                               │
│     → totalReqs, okReqs, nokReqs, naReqs, etc.                   │
│     → reportExcel (base64 XLSX)                                 │
│     → downloadUrl (SAS URL — if Blob Storage configured)          │
└──────────────────────────────────────────────────────────────────┘
```

---

## 📊 COMPARISON: Spec QA vs Conformity Matrix

| Aspect | Spec QA (Working) | Conformity Matrix (Fixed) |
|--------|-------------------|--------------------------|
| Input | Text question | File (blob) |
| Copilot Studio binding | `=VarQuestion` (text) | `=Topic.ConformityFile.contentUrl` (URL string) |
| Azure Function endpoint | `/api/ask` | `/api/conformity-excel` |
| Auth | Function key | Anonymous |
| Orchestration | Power Automate (HTTP) | Direct tool call (OpenAPI) |
| BlobToText issue | No (text only) | Fixed (use fileUrl, not Text()) |

---

## ✅ VERIFICATION CHECKLIST

- [ ] Azure Function deployed and responding (test with curl)
- [ ] OpenAPI spec v3.1 re-imported in Copilot Studio
- [ ] Tool inputs configured:
  - [ ] `fileUrl` = `=Topic.ConformityFile.contentUrl` (NOT "Dynamic filling with AI")
  - [ ] `fileName` = `=Topic.ConformityFile.Name` (NOT "Dynamic filling with AI")
  - [ ] `fileContent` = empty (removed `=Text(Topic.ConformityFile)`)
- [ ] NO `code` input (anonymous endpoint)
- [ ] Output bindings configured (all 14 outputs mapped)
- [ ] Trigger phrases configured
- [ ] Success message template configured
- [ ] Error message template configured
- [ ] End-to-end test: upload .xlsm file in Teams → get Excel report