# LEON — Excel Conformity Report in Copilot Studio
## Final Enterprise Setup Guide (v3.0)

> **Version 3.0** — Fixes BlobToText error, supports fileUrl, JSON-serialized file objects, and raw binary uploads

---

## ⚠️ CRITICAL FIX (v3.0): BlobToText Error Resolution

### The Problem
Copilot Studio's `FilePrebuiltEntity` (file upload question) returns a **blob** object.
The expression `=Text(Topic.ConformityFile)` tries to convert this blob to text using the `BlobToText` operator.
**`BlobToText` is NOT implemented** in Copilot Studio's Power Fx engine → error:
```
'Not yet implemented unary operator: BlobToText.'
```

### The Solution (3 Approaches — Try in Order)

#### Approach 1: Use `fileUrl` (PREFERRED — bypasses blob entirely)
Instead of passing file content, pass the file's URL:
1. Re-import the updated OpenAPI spec (`LEON_Conformity_Excel_Only.json` v3.0.0)
2. In the tool call node, set `fileUrl` input to `=Topic.ConformityFile.contentUrl` (or `=Topic.ConformityFile.url`)
3. The Azure Function downloads the file from the URL
4. **No `Text()` conversion needed** — the URL is already a string

#### Approach 2: Use `JSON()` function (if fileUrl is not available)
Instead of `=Text(Topic.ConformityFile)`, use:
```
=JSON(Topic.ConformityFile, JSONFormat.IncludeBinaryData)
```
This serializes the file object (including blob content) as a JSON string with base64 content.
The Azure Function's `_decode_file_content` now handles this format (Case 8).

1. Re-import the updated OpenAPI spec
2. In the tool call node, set `fileContent` input to `=JSON(Topic.ConformityFile, JSONFormat.IncludeBinaryData)`
3. The Azure Function parses the JSON and extracts the base64 content

#### Approach 3: Pass file object directly (no conversion)
1. Re-import the updated OpenAPI spec
2. In the tool call node, set `fileContent` input to `=Topic.ConformityFile` (WITHOUT `Text()`)
3. If Copilot Studio passes the blob as raw binary, the Azure Function handles it via `application/octet-stream`

---

## WHAT CHANGED (v1 → v2)

| Issue in v1 | Fix in v2 |
|-------------|-----------|
| Bot asked user for API key (`code` input) | **Endpoint is now anonymous** — no `code` parameter in OpenAPI spec |
| `fileName` not mapped → AI asked user | `fileName` is **optional** with default `conformity_matrix.xlsx` |
| `fileContent` format issues | Improved decoder handles base64, data URI, dict, URL-safe base64 |
| No way to deliver Excel file to user | **Azure Blob Storage** upload with 7-day SAS download URL |
| Excel sheet 3 had wrong column refs | Fixed auto-filter range and alignment columns |

---

## ARCHITECTURE (v2)

```
User in Teams: "Génère le rapport Excel" + attaches .xlsm file
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  COPILOT STUDIO TOPIC: "Rapport Excel Conformité"                │
│                                                                  │
│  1. TRIGGER — recognizes intent (trigger phrases)                │
│  2. QUESTION — File upload (FilePrebuiltEntity)                  │
│     → File captured in Topic.ConformityFile variable             │
│     → SKIPPED if file already attached to message                │
│  3. TOOL CALL — "Conformity Report" connector                     │
│     → fileContent: =Text(Topic.ConformityFile)                    │
│     → fileName: (optional, defaults to conformity_matrix.xlsx)   │
│     → NO code parameter needed (anonymous endpoint)              │
│  4. MESSAGE — Show results to user                               │
│     → answer text (statistics summary)                           │
│     → downloadUrl link (if Blob Storage configured)               │
│     → OR instructions to save reportExcel (base64)               │
└──────────────────────────┬───────────────────────────────────────┘
                           │ HTTPS POST (anonymous)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  AZURE FUNCTION — /api/conformity-excel (ANONYMOUS)              │
│                                                                  │
│  1. Receives JSON: { fileName, fileContent (base64) }            │
│  2. Decodes file content (handles all formats)                   │
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

## STEP 1: REDEPLOY AZURE FUNCTION

The Azure Function must be redeployed with the anonymous endpoint.

### What changed in the code:
- `function_app.py`: `conformity_excel` endpoint now has `auth_level=func.AuthLevel.ANONYMOUS`
- `azure_handler.py`: Added `_upload_to_blob_storage()` helper + improved response
- `function_app.py`: Improved `_decode_file_content()` handles dict objects, URL-safe base64
- `requirements.txt`: Added `azure-storage-blob>=12.19.0` and `lxml>=4.9.0`

### Deploy:
```powershell
# From the workspace root
.\_deploy_now.ps1
```

Or manually:
```powershell
cd azure_function
func azure functionapp publish leon-spec-gbexcnefdmakfpdg
```

### Verify:
```powershell
# Test anonymous endpoint (no code parameter in URL)
curl -X POST https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel `
  -H "Content-Type: application/json" `
  -d '{"fileName":"test.xlsx","fileContent":""}'
# Should return 422 (missing file content) — NOT 401 (unauthorized)
```

---

## STEP 2: CONFIGURE AZURE BLOB STORAGE (Optional but Recommended)

For the Excel file to be delivered as a download link (instead of base64), configure Azure Blob Storage.

### 2.1 Create a Storage Account (if not already done)

1. Go to Azure Portal → Storage Accounts
2. Create a new storage account (or use existing)
3. Note the **Connection String** (Access Keys → Key1 → Connection string)

### 2.2 Add Connection String to Function App Settings

1. Go to your Azure Function App → Configuration
2. Add a new setting:
   - **Name**: `AZURE_STORAGE_CONNECTION_STRING`
   - **Value**: `DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net`
3. Save

### 2.3 Create the Container

The function will auto-create a `leon-reports` container on first use. You can also create it manually:
1. Go to Storage Account → Containers → + Container
2. Name: `leon-reports`
3. Public access: Private (SAS URLs will be used for download)

> If Blob Storage is NOT configured, the function still works — it returns `reportExcel` (base64) without a `downloadUrl`. Copilot Studio can use a Power Automate flow to save the file.

---

## STEP 3: RE-IMPORT THE OPENAPI SPEC IN COPILOT STUDIO

The OpenAPI spec has been updated (v2.0.0). You need to re-import it.

### 3.1 Get the updated spec file

The file is: `azure_function/LEON_Conformity_Excel_Only.json`

### 3.2 Re-import in Copilot Studio

1. Go to **Copilot Studio** → your **LEON** agent
2. **Tools** → find the existing "Conformity Report" tool
3. Click **Edit** (or delete and re-create)
4. Upload the updated `LEON_Conformity_Excel_Only.json`
5. The tool should now show only **2 inputs**:
   - `fileContent` (required) — the file content as base64
   - `fileName` (optional) — the file name with extension
6. **NO `code` input** — it's been removed

### 3.3 Configure the tool inputs

The OpenAPI spec v3.0.0 has **3 inputs** (none required — at least one of `fileUrl` or `fileContent` must be provided):

| Input | Filling Mode | Value | Notes |
|-------|-------------|-------|-------|
| `fileUrl` | **From a variable** | `=Topic.ConformityFile.contentUrl` | **PREFERRED** — bypasses BlobToText error |
| `fileContent` | **From a variable** | `=JSON(Topic.ConformityFile, JSONFormat.IncludeBinaryData)` | **FALLBACK** — if fileUrl doesn't work |
| `fileName` | **Set manually** | `conformity_matrix.xlsx` | Optional — defaults to `conformity_matrix.xlsx` |

> **⚠️ CRITICAL**: Do NOT use `=Text(Topic.ConformityFile)` — this causes the `BlobToText` error!
> 
> **Do NOT set any input to "Dynamic filling with AI"** — this causes the bot to ask the user for values.

### 3.4 Troubleshooting Tool Inputs

If `fileUrl` with `=Topic.ConformityFile.contentUrl` doesn't work:
- Try `=Topic.ConformityFile.url` (different property name)
- Try `=Topic.ConformityFile.ContentUrl` (capital C)
- Check the Copilot Studio variable browser for available properties

If `fileContent` with `=JSON(Topic.ConformityFile, JSONFormat.IncludeBinaryData)` doesn't work:
- Try `=JSON(Topic.ConformityFile)` (without the format flag)
- Try `=Topic.ConformityFile` (direct, no conversion)
- Try `=Topic.ConformityFile.content` (if content is a string)

---

## STEP 4: CONFIGURE THE COPILOT STUDIO TOPIC

### 4.1 Open the Topic

1. Go to **Topics** → find "Rapport Excel Conformité" (or create it)
2. Open the topic editor

### 4.2 Trigger Node

Add trigger phrases (one per line):
```
rapport excel conformité
donne-moi le rapport excel
fichier excel conformité
excel conformity report
génère rapport xlsx conformité
voici ma matrice de conformité
matrice conformité FNR
télécharger excel conformité
export excel matrice conformité
rapport xlsx conformité
generate excel report
conformity matrix report
```

### 4.3 Question Node (File Upload)

| Field | Value |
|-------|-------|
| **Question text** | `Veuillez télécharger votre matrice de conformité (fichier ODS ou XLSX)` |
| **Identify** | **File upload** (FilePrebuiltEntity) |
| **Save response as** | `ConformityFile` |

**Question behavior settings** (click `...` → Properties):
| Property | Value |
|----------|-------|
| Skip behavior | Allow question to be skipped |
| Reprompt | Don't repeat |
| No valid entity found | Set variable to empty (no value) |

> This makes the question invisible if the user already attached a file in their first message.

### 4.4 Message Node (Confirmation)

Add a **Send a message** node:
```
📊 Merci, fichier reçu ! Analyse en cours...

Je génère le rapport Excel coloré avec :
• La liste complète des exigences OK/NOK/NA
• Les lignes colorées par statut (🟢 🔴 ⚪)
• L'analyse approfondie des réponses OK (points d'attention)

Résultats dans quelques instants ⏳
```

### 4.5 Tool Call Node (Conformity Report)

Add a **Call a tool** node:
- **Tool**: Conformity Report (the connector you imported)

**Input bindings** (v3.0 — BlobToText fix):
| Input | Value | Notes |
|-------|-------|-------|
| `fileUrl` | `=Topic.ConformityFile.contentUrl` | **PREFERRED** — no blob conversion needed |
| `fileContent` | `=JSON(Topic.ConformityFile, JSONFormat.IncludeBinaryData)` | **FALLBACK** — serializes blob as base64 JSON |
| `fileName` | (leave empty — uses default `conformity_matrix.xlsx`) | Optional |

> **⚠️ CRITICAL**: Do NOT use `=Text(Topic.ConformityFile)` — this causes the `BlobToText` error!
>
> If the file name is available as a property of the file variable, you can also use:
> `fileName` = `=Topic.ConformityFile.Name` or `=Topic.ConformityFile.name`

**Output bindings** (map all outputs to topic variables):
| Output | Variable |
|--------|----------|
| `answer` | `Topic.answer` |
| `status` | `Topic.status` |
| `totalReqs` | `Topic.totalReqs` |
| `okReqs` | `Topic.okReqs` |
| `nokReqs` | `Topic.nokReqs` |
| `naReqs` | `Topic.naReqs` |
| `emptyReqs` | `Topic.emptyReqs` |
| `okDeepFindings` | `Topic.okDeepFindings` |
| `needsReview` | `Topic.needsReview` |
| `reportExcel` | `Topic.reportExcel` |
| `downloadUrl` | `Topic.downloadUrl` |
| `fileName` | `Topic.fileName` |

### 4.6 Condition Node (Check Status)

Add a **Condition** node to check if the report was successful:

**Condition**: `Topic.status` is equal to `answered`

- **If YES**: Continue to the success message
- **If NO**: Go to error message

### 4.7 Success Message Node

Add a **Send a message** node in the YES branch:

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

**If downloadUrl is available**, add:
```
📥 Téléchargez le rapport Excel :
{Topic.downloadUrl}
```

**If downloadUrl is NOT available**, add:
```
Le rapport Excel a été généré. Contactez l'administrateur pour récupérer le fichier.
```

### 4.8 Error Message Node

Add a **Send a message** node in the NO branch:
```
❌ Une erreur s'est produite lors de la génération du rapport.

Détails : {Topic.answer}

Vérifiez que :
1. Le fichier est au format ODS ou XLSX
2. Le fichier contient des colonnes "Conformité FNR" et "Commentaires FNR"
3. Le fichier n'est pas corrompu
```

### 4.9 Save

Click **Save** in the top toolbar.

---

## STEP 5: ALTERNATIVE — POWER AUTOMATE FLOW (for SharePoint delivery)

If you don't have Azure Blob Storage configured, use a Power Automate flow to save the Excel file to SharePoint.

### 5.1 Create the Flow

1. In the topic, replace the "Tool Call" node with "Call a flow"
2. Create a new flow with:
   - **Trigger**: When an agent calls the flow
   - **Inputs**: `FileContent` (Text), `FileName` (Text)

### 5.2 Flow Actions

| Action | Configuration |
|--------|---------------|
| **Compose** | `{"fileName": "@{triggerBody()?['FileName']}", "fileContent": "@{triggerBody()?['FileContent']}"} ` |
| **HTTP** | Method: `POST`, URI: `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel`, Headers: `Content-Type: application/json`, Body: `@{outputs('Compose')}` |
| **Parse JSON** | Content: `@{body('HTTP')}`, Schema: (see below) |
| **Create file** (SharePoint) | Folder: `/Shared Documents/LEON Reports/`, Name: `LEON_Report_@{formatDateTime(utcNow(),'yyyy-MM-dd_HHmm')}.xlsx`, Content: `@{base64ToBinary(body('Parse_JSON')?['reportExcel'])}` |
| **Create sharing link** | File: `@{outputs('Create_file')?['body/ItemId']}`, Type: View, Scope: Organization |
| **Respond to agent** | `AnswerText`: `@{body('Parse_JSON')?['answer']}`, `FileLink`: `@{body('Create_sharing_link')?['link']}` |

### 5.3 Parse JSON Schema

```json
{
  "type": "object",
  "properties": {
    "answer": { "type": "string" },
    "status": { "type": "string" },
    "confidence": { "type": "string" },
    "fileName": { "type": "string" },
    "totalReqs": { "type": "integer" },
    "okReqs": { "type": "integer" },
    "nokReqs": { "type": "integer" },
    "naReqs": { "type": "integer" },
    "emptyReqs": { "type": "integer" },
    "okDeepFindings": { "type": "integer" },
    "needsReview": { "type": "integer" },
    "reportExcel": { "type": "string" },
    "downloadUrl": { "type": "string" }
  }
}
```

---

## STEP 6: TEST THE COMPLETE FLOW

### 6.1 Test the Azure Function directly

```powershell
# Test with a real XLSM file
$fileBytes = [System.IO.File]::ReadAllBytes("C:\Users\TA29225\Spec AI Project\data\uploads\test_matrix.xlsm")
$b64 = [Convert]::ToBase64String($fileBytes)
$body = @{ fileName = "test_matrix.xlsm"; fileContent = $b64 } | ConvertTo-Json
$response = Invoke-RestMethod -Uri "https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel" `
  -Method Post -ContentType "application/json" -Body $body
$response | ConvertTo-Json -Depth 2
```

### 6.2 Test in Copilot Studio

1. Go to **Test** pane in Copilot Studio
2. Type: "Génère le rapport Excel de conformité"
3. Upload a `.xlsm` or `.ods` file
4. The bot should:
   - NOT ask for an API key
   - Show the statistics summary
   - Provide a download link (if Blob Storage configured)

### 6.3 Test in Teams

1. Open the LEON bot in Teams
2. Type: "Voici ma matrice de conformité" + attach a file
3. The bot should process the file and return the report

---

## TROUBLESHOOTING

### BlobToText error (v3.0 fix)
- **Error**: `'Not yet implemented unary operator: BlobToText.'`
- **Cause**: `=Text(Topic.ConformityFile)` tries to convert a blob to text, but `BlobToText` is not implemented
- **Fix**: Use one of these alternatives:
  1. `fileUrl` = `=Topic.ConformityFile.contentUrl` (PREFERRED)
  2. `fileContent` = `=JSON(Topic.ConformityFile, JSONFormat.IncludeBinaryData)` (FALLBACK)
  3. `fileContent` = `=Topic.ConformityFile` (direct, no conversion)
- **Do NOT use**: `=Text(Topic.ConformityFile)` — this is what causes the error

### Bot still asks for API key
- **Cause**: Old OpenAPI spec still imported in Copilot Studio
- **Fix**: Re-import the updated `LEON_Conformity_Excel_Only.json` (v3.0.0)
- **Verify**: The tool should show 3 inputs (`fileUrl`, `fileContent`, `fileName`), NOT `code`

### Bot says "Missing file content"
- **Cause**: Neither `fileUrl` nor `fileContent` was properly bound
- **Fix**: In the tool call node, set `fileUrl` to `=Topic.ConformityFile.contentUrl`
- **Alternative**: Set `fileContent` to `=JSON(Topic.ConformityFile, JSONFormat.IncludeBinaryData)`
- **Do NOT use**: `=Text(Topic.ConformityFile)` — causes BlobToText error

### Bot returns "Could not find 'Conformité FNR' columns"
- **Cause**: The spreadsheet doesn't have the expected column names
- **Fix**: The analyzer auto-detects columns using fuzzy matching. Check that your file has columns named "Conformité FNR" or "Conformity FNR" and "Commentaires FNR" or "Comments FNR"

### Excel file not downloadable
- **Cause**: Azure Blob Storage not configured
- **Fix**: Either configure Blob Storage (Step 2) or use Power Automate flow (Step 5)

### Function returns 500 error
- **Cause**: Internal error during analysis
- **Fix**: Check the Azure Function logs in Application Insights or the Function App's Log stream

---

## FILE REFERENCE

| File | Purpose |
|------|---------|
| `azure_function/LEON_Conformity_Excel_Only.json` | OpenAPI spec for Copilot Studio tool (v3.0.0 — fileUrl + fileContent, no code param) |
| `azure_function/function_app.py` | Azure Function endpoints (conformity-excel is anonymous, handles base64/JSON/raw binary) |
| `azure_function/azure_handler.py` | Handler logic + Blob Storage upload |
| `azure_function/requirements.txt` | Python dependencies (includes azure-storage-blob) |
| `app/qa/conformity_analyzer.py` | Matrix analysis engine (extraction, classification, deep OK analysis) |
| `app/qa/conformity_report.py` | Excel report generator (3 sheets, color-coded) |
| `azure_function/COPILOT_STUDIO_EXCEL_FINAL_SETUP.md` | This guide |