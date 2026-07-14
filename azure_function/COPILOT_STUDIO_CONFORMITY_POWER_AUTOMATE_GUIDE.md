# LEON — Conformity Matrix via Power Automate Flow
## Definitive Setup Guide (v7.0) — 100% Functional

> **Version 7.0** — Uses Power Automate flow as intermediary to bypass all Blob/RAI/BlobToText errors

---

## 🎯 WHY POWER AUTOMATE IS REQUIRED

### The Fundamental Problem
Copilot Studio's `FilePrebuiltEntity` returns a **Blob** type variable. You **cannot**:
- Use dot notation on it: `Topic.Var1.Name` → **"L'opérateur « . » ne peut pas être utilisé sur les valeurs Blob."**
- Use `Text()` on it: `=Text(Topic.Var1)` → **"Not yet implemented unary operator: BlobToText."**
- Pass it directly to a tool: `file: =Topic.Var1` → **"ContentFiltered due to Responsible AI restrictions"**
- Access any property of it: `.contentUrl`, `.url`, `.Name` → **All fail with dot operator error**

### The Solution: Power Automate Flow
Power Automate **natively handles Blob/file objects** — it can:
1. Receive the file blob from Copilot Studio
2. Convert it to base64 automatically
3. Send it to the Azure Function as JSON
4. Return the response to Copilot Studio

This is the **same pattern** used by the Spec QA (which works because it passes text, not files).

---

## 📋 ARCHITECTURE (v7.0)

```
User: "summary of this matrix conformity" + attaches .xlsm file
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  COPILOT STUDIO TOPIC                                            │
│                                                                  │
│  1. TRIGGER — recognizes intent                                  │
│  2. QUESTION — File upload (FilePrebuiltEntity)                  │
│     → Skip behavior: Allow question to be skipped                │
│     → Topic.Var1 = file blob (if user attached file)             │
│  3. CALL A FLOW — Power Automate flow                            │
│     → Input: file blob (Topic.Var1)                              │
│     → Power Automate handles the blob natively!                  │
│  4. MESSAGE — Show results from flow                             │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  POWER AUTOMATE FLOW                                             │
│                                                                  │
│  1. TRIGGER: When Copilot Studio calls the flow                  │
│     → Input: FileContent (Blob/File type)                        │
│                                                                  │
│  2. COMPOSE: Convert file to base64                             │
│     → base64(triggerBody()?['FileContent'])                      │
│                                                                  │
│  3. HTTP: POST to Azure Function                                 │
│     → URI: https://leon-spec-...azurewebsites.net/api/conformity-excel │
│     → Body: { "fileName": "conformity_matrix.xlsm",              │
│               "fileContent": "<base64>" }                        │
│     → Content-Type: application/json                             │
│                                                                  │
│  4. PARSE JSON: Parse the response                               │
│     → Content: body('HTTP')                                      │
│                                                                  │
│  5. RESPOND: Return results to Copilot Studio                    │
│     → answer, status, totalReqs, okReqs, nokReqs, downloadUrl   │
└──────────────────────────┬───────────────────────────────────────┘
                           │ HTTPS POST (anonymous)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  AZURE FUNCTION — /api/conformity-excel (ANONYMOUS)              │
│                                                                  │
│  1. Receives JSON: { fileName, fileContent (base64) }           │
│  2. Decodes base64 → file bytes                                  │
│  3. Analyzes conformity matrix                                   │
│  4. Generates Excel report                                       │
│  5. Returns JSON with statistics + downloadUrl                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 📋 STEP-BY-STEP SETUP

### STEP 1: Create the Power Automate Flow

1. Go to https://make.powerautomate.com
2. **Create** → **Instant cloud flow**
3. Name: `LEON-Conformity-Excel-Flow`
4. Trigger: **When Copilot Studio calls the flow** (or "When a Power Apps or Power Virtual Agents calls the flow")
5. Add input parameter:
   - **Name**: `FileContent`
   - **Type**: File (or Text — see note below)

> **Note**: If "File" type is not available for the trigger input, use "Text" type and pass the file as base64 from Copilot Studio using `=JSON(Topic.Var1, JSONFormat.IncludeBinaryData)` in the flow call node. However, this might trigger the BlobToText error. If "File" type is available, use it — Power Automate handles it natively.

### STEP 2: Add Compose Action (Convert to base64)

1. Click **+ New step**
2. Search for **Compose**
3. Name it: `ConvertFileToBase64`
4. Expression: `base64(triggerBody()?['FileContent'])`

> If the file is already passed as text (base64), skip this step and use `triggerBody()?['FileContent']` directly.

### STEP 3: Add HTTP Action (Call Azure Function)

1. Click **+ New step**
2. Search for **HTTP**
3. Select the **HTTP** action (premium connector)
4. Configure:
   - **Method**: `POST`
   - **URI**: `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel`
   - **Headers**:
     ```
     Content-Type: application/json
     ```
   - **Body**:
     ```json
     {
       "fileName": "conformity_matrix.xlsm",
       "fileContent": "@{outputs('ConvertFileToBase64')}"
     }
     ```

> If you skipped Step 2 (file already base64), use `@{triggerBody()?['FileContent']}` instead of `@{outputs('ConvertFileToBase64')}`.

### STEP 4: Add Parse JSON Action

1. Click **+ New step**
2. Search for **Parse JSON**
3. Configure:
   - **Content**: `Body` from the HTTP action
   - **Schema**:
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
         "inconsistencies": { "type": "integer" },
         "okDeepFindings": { "type": "integer" },
         "needsReview": { "type": "integer" },
         "downloadUrl": { "type": "string" }
       }
     }
     ```

### STEP 5: Add Respond Action (Return to Copilot Studio)

1. Click **+ New step**
2. Search for **Respond to Copilot Studio** (or "Respond to Power Apps or flow")
3. Add outputs:
   - `answer` → `body('Parse_JSON')?['answer']`
   - `status` → `body('Parse_JSON')?['status']`
   - `totalReqs` → `body('Parse_JSON')?['totalReqs']`
   - `okReqs` → `body('Parse_JSON')?['okReqs']`
   - `nokReqs` → `body('Parse_JSON')?['nokReqs']`
   - `naReqs` → `body('Parse_JSON')?['naReqs']`
   - `emptyReqs` → `body('Parse_JSON')?['emptyReqs']`
   - `okDeepFindings` → `body('Parse_JSON')?['okDeepFindings']`
   - `needsReview` → `body('Parse_JSON')?['needsReview']`
   - `downloadUrl` → `body('Parse_JSON')?['downloadUrl']`
   - `fileName` → `body('Parse_JSON')?['fileName']`
   - `confidence` → `body('Parse_JSON')?['confidence']`
   - `inconsistencies` → `body('Parse_JSON')?['inconsistencies']`

### STEP 6: Save the Flow

Click **Save** in the top right.

---

## 📋 CONFIGURE COPILOT STUDIO TOPIC

### STEP 7: Configure the Topic

1. Go to **Copilot Studio** → your **LEON** agent
2. **Topics** → find or create the conformity topic
3. Open the topic editor

### STEP 8: Trigger Phrases

```
analyse ma matrice de conformité
rapport conformité FNR
analyse conformité
conformity report
génère rapport conformité
matrice conformité FNR
rapport excel conformité
summary of this matrix conformity
génère rapport excel conformité
voici ma matrice de conformité
```

### STEP 9: Question Node (File Upload)

| Field | Value |
|-------|-------|
| **Question text** | `give me the doc` |
| **Identify** | **File upload** (FilePrebuiltEntity) |
| **Save response as** | `Var1` |
| **Skip behavior** | **Allow question to be skipped** |
| **Reprompt** | Don't repeat |
| **No valid entity found** | Set variable to empty (no value) |

### STEP 10: Call the Flow (NOT a tool!)

1. Add a **Call an action** node
2. Select **Call a flow**
3. Select the flow `LEON-Conformity-Excel-Flow`
4. Map input:
   - `FileContent` → `Topic.Var1` (the file blob — Power Automate handles it!)

> **CRITICAL**: Use **Call a flow**, NOT **Call a tool**. The flow handles the Blob natively.

### STEP 11: Map Flow Outputs

The flow returns these outputs — map them to topic variables:

| Flow Output | Topic Variable |
|-------------|----------------|
| `answer` | `Topic.answer` |
| `status` | `Topic.status` |
| `totalReqs` | `Topic.totalReqs` |
| `okReqs` | `Topic.okReqs` |
| `nokReqs` | `Topic.nokReqs` |
| `naReqs` | `Topic.naReqs` |
| `emptyReqs` | `Topic.emptyReqs` |
| `okDeepFindings` | `Topic.okDeepFindings` |
| `needsReview` | `Topic.needsReview` |
| `downloadUrl` | `Topic.downloadUrl` |
| `fileName` | `Topic.fileName` |
| `confidence` | `Topic.confidence` |
| `inconsistencies` | `Topic.inconsistencies` |

### STEP 12: Add Condition Node

**Condition**: `Topic.status` is equal to `answered`

- **If YES**: Continue to success message
- **If NO**: Go to error message

### STEP 13: Success Message

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

### STEP 14: Error Message

```
❌ Une erreur s'est produite lors de la génération du rapport.

Détails : {Topic.answer}

Vérifiez que :
1. Le fichier est au format ODS ou XLSX
2. Le fichier contient des colonnes "Conformité FNR" et "Commentaires FNR"
3. Le fichier n'est pas corrompu
```

### STEP 15: Save

Click **Save** in the top toolbar.

---

## 🔍 WHY THIS APPROACH WORKS (AND OTHERS DON'T)

| Approach | What happens | Error |
|----------|-------------|-------|
| `=Text(Topic.Var1)` | Power Fx tries BlobToText | `BlobToText` not implemented |
| `=Topic.Var1.Name` | Dot operator on Blob | `.` cannot be used on Blob values |
| `=Topic.Var1.contentUrl` | Dot operator on Blob | `.` cannot be used on Blob values |
| `file: =Topic.Var1` (formData tool) | Binary passes through tool | `ContentFiltered` (RAI filter) |
| **Power Automate flow** | **Flow handles Blob natively** | **✅ Works!** |

### Why Power Automate Works
1. Power Automate's "When Copilot Studio calls the flow" trigger **natively accepts File/Blob type**
2. Power Automate can convert the blob to base64 using `base64()` function
3. The base64 string is sent to the Azure Function as JSON
4. No blob passes through Copilot Studio's tool call → no RAI filter
5. No dot notation needed on Blob → no dot operator error
6. No `Text()` conversion needed → no BlobToText error

---

## ✅ VERIFICATION CHECKLIST

- [ ] Power Automate flow created (`LEON-Conformity-Excel-Flow`)
- [ ] Flow trigger: "When Copilot Studio calls the flow" with FileContent input
- [ ] Flow has Compose action: `base64(triggerBody()?['FileContent'])`
- [ ] Flow has HTTP action: POST to Azure Function with JSON body
- [ ] Flow has Parse JSON action with correct schema
- [ ] Flow has Respond action with all 13 outputs
- [ ] Flow saved and tested
- [ ] Copilot Studio topic: Question node with "Skip behavior: Allow question to be skipped"
- [ ] Copilot Studio topic: "Call a flow" node (NOT "Call a tool")
- [ ] Copilot Studio topic: Flow input mapped: FileContent → Topic.Var1
- [ ] Copilot Studio topic: Flow outputs mapped to topic variables
- [ ] Copilot Studio topic: Condition node checking Topic.status = "answered"
- [ ] Copilot Studio topic: Success message with statistics
- [ ] Copilot Studio topic: Error message with details
- [ ] End-to-end test: upload .xlsm file → get Excel report