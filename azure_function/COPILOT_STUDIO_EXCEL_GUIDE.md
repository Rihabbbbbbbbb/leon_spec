# LEON — Excel Report in Copilot Studio
## Complete Step-by-Step Guide for the XLSX Conformity Report Feature

---

## PREREQUISITES (Already Done — Verified ✅)

| Item | Status | Details |
|------|--------|---------|
| Azure Function `/api/conformity-excel` | ✅ LIVE | `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel` |
| Function Key | ✅ | `<YOUR_FUNCTION_KEY>` |
| API Tested | ✅ | 200 OK — 87KB Excel file generated from real ODS |
| Excel Report | ✅ | 2 sheets (Summary + All Items), color-coded rows |
| Copilot Studio Agent | ✅ | LEON agent already exists in Copilot Studio |

---

## ARCHITECTURE — How It Works End-to-End

### Main Flow: User Attaches File in First Message

```
User in Teams types: "Voici mon rapport Excel [attaches ODS file]"
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│  COPILOT STUDIO — Topic: "Rapport Excel Conformité"              │
│                                                                   │
│  1. Trigger node — recognizes request + captures attached file   │
│     (the file is available as a trigger variable)                │
│  2. Message node — "Fichier reçu, génération du rapport..."      │
│  3. Tool node — Calls Power Automate flow directly               │
│     (passes file from trigger: FileContent + FileName)           │
│  4. Message node — Shows summary + SharePoint file link          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  POWER AUTOMATE FLOW — "LEON - Excel Conformity Report"      │
│                                                               │
│  Trigger: When an agent calls the flow                        │
│  Input: FileContent (base64), FileName (string)               │
│                                                               │
│  Action 1: Compose — Build JSON request body                  │
│  Action 2: HTTP POST → Azure Function /api/conformity-excel  │
│  Action 3: Parse JSON — Extract answer + reportExcel (base64)│
│  Action 4: Create file in SharePoint/OneDrive                │
│  Action 5: Respond to Copilot Studio — answer + file link    │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  AZURE FUNCTION — /api/conformity-excel                       │
│                                                               │
│  1. Receives JSON: { fileName, fileContent (base64) }         │
│  2. Saves file to /data/uploads/                              │
│  3. Calls analyze_conformity_matrix()                         │
│     → Reads ODS/XLSX (odfpy/openpyxl)                         │
│     → Auto-detects sheet, header row, columns                 │
│     → Classifies: OK / NOK / NA / STANDBY / UNKNOWN           │
│     → Detects AI inconsistencies                              │
│  4. Calls generate_conformity_excel()                         │
│     → Creates XLSX with color-coded rows                      │
│     → Sheet 1: Summary (stats with colors)                    │
│     → Sheet 2: All Items (every requirement, color-coded)     │
│  5. Returns JSON: { answer, analysis, reportExcel (base64) } │
└─────────────────────────────────────────────────────────────┘
```

### Alternative Flow: User Asks Without Attaching (Step-by-Step)

If the user just asks without attaching a file, the topic can still ask for it:

```
User: "Donne-moi le rapport Excel de conformité"
    → Trigger fires
    → Message: "Veuillez joindre votre matrice à votre message"
    → User attaches the file → continues with the same flow as above
```

---
```

---

## STEP 1: Create a New Topic in Copilot Studio

### 1.1 Open Your Agent

1. Go to **https://copilotstudio.microsoft.com**
2. Sign in with your Microsoft 365 account
3. Select **Agents** → find your **LEON** agent
4. Click on it to open the agent editor

### 1.2 Create the Topic

1. In the left navigation, click **Topics**
2. Click **+ Add a topic** → **From blank**
3. A new topic canvas opens with a **Trigger** node

### 1.3 Name the Topic

1. Click the topic title at the top (default: "New topic")
2. Rename it to: **Rapport Excel Conformité FNR**
3. In the **Description** field (right panel), enter:
   ```
   Generates a color-coded Excel report from a conformity matrix file (ODS or XLSX).
   The report includes a Summary sheet with statistics and an All Items sheet with
   every requirement color-coded by conformity status (green=OK, red=NOK, yellow=STANDBY).
   ```

### 1.4 Add Trigger Phrases

Click on the **Trigger** node, then in the **Phrases** panel on the right:

Add these trigger phrases (one per line, press Enter after each):

```
rapport excel conformité
rapport excel conformity
donne-moi le rapport excel
excel conformity report
fichier excel conformité
télécharger excel conformité
export excel matrice conformité
conformity matrix excel
rapport xlsx conformité
génère excel conformité FNR
voici ma matrice de conformité
matrice conformité FNR
```

> **Important**: Add at least 5-10 trigger phrases so the AI can recognize variations of the user's request — including ones where they attach a file.

### 1.5 Configure the Trigger to Capture the Attached File

This is the **critical step** that makes the "file in first message" work:

1. In the **Trigger** node properties (right panel), look for **Inputs** or **Parameters**
2. Add an input parameter:
   - **Name**: `AttachedFile`
   - **Type**: `File` (or `Attachment`, depending on your Copilot Studio version)
3. This captures the file the user attaches to their message

> **If your Copilot Studio version doesn't support file as a trigger input**:
> The file attachment will still be available in the conversation context. When you call the Power Automate flow, you can map `Activity.Attachments[0].Content` or `Conversation.Attachments` to the flow inputs. The exact path depends on your Copilot Studio version — check the **Dynamic content** panel when configuring the flow node.

### 1.6 Save

Click **Save** in the top toolbar.

---

## STEP 2: Add a Message Node (Confirmation)

No need to ask for the file — it's already in the user's message. Just confirm receipt.

### 2.1 Add the Message

1. Below the Trigger node, click the **+ Add node** icon
2. Select **Send a message**
3. In the text box, enter:

```
📊 Merci, j'ai bien reçu votre fichier. Je génère le rapport Excel coloré...

Votre matrice va être analysée — colonnes de conformité détectées automatiquement.
Résultats dans quelques instants...
```

### 2.2 Save

Click **Save**.

---

## STEP 3: No File Upload Question Needed

Since the user attaches the file **directly in their first message**, you **skip the Question node**. The file data is already captured by the Trigger node.

### 3.1 Summary of the Simplified Flow

```
Trigger (recognizes intent + captures file)
    ↓
Message ("Fichier reçu, génération...")
    ↓
Tool (Power Automate flow — passes file from trigger)
    ↓
Message (shows results + download link)
```

### 3.2 What If the User Doesn't Attach a File?

Add a **Condition node** after the Message node to check if a file was received:

1. Below the Message node, click **+ Add node** → **Add a condition**
2. Name it: "Vérifier si fichier joint"
3. Configure the condition:
   - **Value 1**: `@Topic.AttachedFile` (or `@Conversation.Attachments`)
   - **Operator**: `is not equal to`
   - **Value 2**: *(empty)*
4. **If YES** (file received): Continue to the Tool node
5. **If NO** (no file): Ask the user to attach one

   Add a Message node in the NO branch:
   ```
   Veuillez joindre votre fichier de matrice de conformité (format ODS ou XLSX) dans votre message.
   ```
   Then add a **Redirect** node → redirect back to the Trigger to re-capture the file

> This way, the topic handles BOTH cases seamlessly:
> - User attaches file → direct processing
> - User just asks → prompted to attach

---

## STEP 4: Create the Power Automate Flow

This is the most critical step. The Power Automate flow connects Copilot Studio to the Azure Function.

### 4.1 Start Creating the Flow

1. Below the Question node, click the **+ Add node** icon
2. Select **Add a tool** → **Create a flow**
3. A new Power Automate flow editor opens in a new browser tab

### 4.2 Configure the Trigger

The flow already has a trigger: **"When an agent calls the flow"**

1. Click on the trigger node
2. In the **Parameters** tab, add two inputs:

   | Input Name | Type | Description |
   |-----------|------|-------------|
   | `FileContent` | Text | The uploaded file content (base64 encoded) |
   | `FileName` | Text | The name of the uploaded file (e.g., "matrix.ods") |

   > In Copilot Studio, when a user uploads a file, the file content is automatically available as a base64 string. You'll map this to the `FileContent` input.

### 4.3 Add Action 1: Compose (Build JSON Body)

1. Below the trigger, click **+ Insert a new action**
2. Search for **Compose** (Data Operations → Compose)
3. In the **Inputs** field, click **Expression** tab and enter:

```json
{
  "fileName": "@{triggerBody()?['FileName']}",
  "fileContent": "@{triggerBody()?['FileContent']}"
}
```

> Or switch to the **Dynamic content** tab and select `FileName` and `FileContent` from the trigger outputs.

### 4.4 Add Action 2: HTTP POST to Azure Function

1. Below the Compose action, click **+ Insert a new action**
2. Search for **HTTP** (Built-in → HTTP)
3. Configure:

   | Field | Value |
   |-------|-------|
   | **Method** | `POST` |
   | **URI** | `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel?code=<YOUR_FUNCTION_KEY>` |
   | **Headers** | `Content-Type: application/json` |
   | **Body** | `@{outputs('Compose')}` |

   > **Important**: The function key is included in the URI as `?code=...`. This is the simplest authentication method. Alternatively, you can use the `x-api-key` header instead.

   > **Timeout**: The Azure Function may take 30-60 seconds to process a large ODS file. The default Power Automate timeout is 100 seconds (which is sufficient).

### 4.5 Add Action 3: Parse JSON (Extract Response)

1. Below the HTTP action, click **+ Insert a new action**
2. Search for **Parse JSON** (Data Operations → Parse JSON)
3. Configure:

   | Field | Value |
   |-------|-------|
   | **Content** | `@{body('HTTP')}` (from Dynamic content) |
   | **Schema** | (see below) |

   Paste this schema:

```json
{
  "type": "object",
  "properties": {
    "answer": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "confidence": {
      "type": "string"
    },
    "fileName": {
      "type": "string"
    },
    "analysis": {
      "type": "object",
      "properties": {
        "fileName": { "type": "string" },
        "sheetName": { "type": "string" },
        "totalRows": { "type": "integer" },
        "stats": {
          "type": "object",
          "properties": {
            "OK": { "type": "integer" },
            "NOK": { "type": "integer" },
            "NA": { "type": "integer" },
            "STANDBY": { "type": "integer" },
            "UNKNOWN": { "type": "integer" },
            "EMPTY": { "type": "integer" }
          }
        },
        "summary": {
          "type": "object",
          "properties": {
            "total": { "type": "integer" },
            "ok": { "type": "integer" },
            "nok": { "type": "integer" },
            "na": { "type": "integer" },
            "standby": { "type": "integer" },
            "unknown": { "type": "integer" },
            "empty": { "type": "integer" },
            "inconsistencies": { "type": "integer" }
          }
        }
      }
    },
    "reportExcel": {
      "type": "string"
    }
  }
}
```

### 4.6 Add Action 4: Create File in SharePoint (or OneDrive)

> This step saves the Excel file to a SharePoint document library so the user can download it via a link. Copilot Studio cannot directly send a file as a download — it can only send text messages and links.

1. Below the Parse JSON action, click **+ Insert a new action**
2. Search for **SharePoint** → **Create file**
   > If you prefer OneDrive, search for **OneDrive for Business** → **Create file**
3. Configure:

   | Field | Value |
   |-------|-------|
   | **Site Address** | Select your team's SharePoint site (e.g., "LEON Team Site") |
   | **Folder Path** | `/Shared Documents/LEON Reports/` (or any folder you choose) |
   | **File Name** | `LEON_Conformity_Report_@{formatDateTime(utcNow(), 'yyyy-MM-dd_HHmm')}.xlsx` |
   | **File Content** | `@{base64ToBinary(body('Parse_JSON')?['reportExcel'])}` |

   > **Expression for File Name**: Use the Expression tab and enter:
   > ```
   > concat('LEON_Conformity_Report_', formatDateTime(utcNow(), 'yyyy-MM-dd_HHmm'), '.xlsx')
   > ```

   > **Expression for File Content**: Use the Expression tab and enter:
   > ```
   > base64ToBinary(body('Parse_JSON')?['reportExcel'])
   > ```

### 4.7 Add Action 5: Create Share Link (Optional but Recommended)

1. Below the Create file action, click **+ Insert a new action**
2. Search for **SharePoint** → **Create sharing link**
3. Configure:

   | Field | Value |
   |-------|-------|
   | **Site Address** | Same SharePoint site as above |
   | **Drive ID** | Select the document library |
   | **File ID** | `@{outputs('Create_file')?['body/ItemId']}` (from Dynamic content) |
   | **Link Type** | `View` |
   | **Link Scope** | `Anonymous` (or `Organization` if you want to restrict to your org) |

### 4.8 Add Action 6: Respond to Copilot Studio

1. Below the last action, click **+ Insert a new action**
2. Search for **Respond to the agent** (Copilot Studio → Respond to the agent)
3. Configure the response outputs:

   Add these outputs:

   | Output Name | Type | Value |
   |-------------|------|-------|
   | `AnswerText` | Text | See expression below |
   | `FileLink` | Text | `@{body('Create_sharing_link')?['link']}` (from Dynamic content) |
   | `FileName` | Text | `@{outputs('Create_file')?['body/Name']}` (from Dynamic content) |
   | `TotalReqs` | Text | `@{body('Parse_JSON')?['analysis']?['summary']?['total']}` |
   | `OkCount` | Text | `@{body('Parse_JSON')?['analysis']?['summary']?['ok']}` |
   | `NokCount` | Text | `@{body('Parse_JSON')?['analysis']?['summary']?['nok']}` |
   | `NaCount` | Text | `@{body('Parse_JSON')?['analysis']?['summary']?['na']}` |
   | `Inconsistencies` | Text | `@{body('Parse_JSON')?['analysis']?['summary']?['inconsistencies']}` |

   For `AnswerText`, use this expression:
   ```
   concat(
     body('Parse_JSON')?['answer'],
     '\n\n📊 Répartition:\n',
     '✅ OK: ', body('Parse_JSON')?['analysis']?['summary']?['ok'], ' | ',
     '❌ NOK: ', body('Parse_JSON')?['analysis']?['summary']?['nok'], ' | ',
     '⚪ NA: ', body('Parse_JSON')?['analysis']?['summary']?['na'], '\n',
     '🔍 Incohérences IA: ', body('Parse_JSON')?['analysis']?['summary']?['inconsistencies']
   )
   ```

### 4.9 Check for Errors

1. In the top menu, click **Flow checker**
2. Fix any errors that appear
3. All errors must be fixed before you can publish

### 4.10 Publish the Flow

1. Click **Publish** in the top menu
2. Confirm by clicking **Publish** again

### 4.11 Test the Flow

1. Click **Test** in the top menu
2. Select **Manually**
3. Click **Run flow**
4. Enter test values:
   - `FileContent`: Paste a base64-encoded ODS file content
   - `FileName`: `test_matrix.ods`
5. Click **Run flow**
6. Wait for completion (should take 30-60 seconds)
7. Check each action's output to verify

---

## STEP 5: Connect the Flow to Your Copilot Studio Topic

### 5.1 Go Back to Copilot Studio

1. Return to your Copilot Studio browser tab
2. Open the **Rapport Excel Conformité FNR** topic

### 5.2 Add the Flow as a Tool Node

1. Below the Message node (or the Condition node if you added one), click the **+ Add node** icon
2. Select **Add a tool**
3. Select the flow you just created: **"LEON - Excel Conformity Report"**
4. A new **Action** node appears

### 5.3 Map the Flow Inputs — File FROM THE TRIGGER

This is the **most important part** — you need to pass the file the user attached in their first message to the flow.

Click on the Action node and map the inputs. The exact variable name depends on your Copilot Studio version:

#### Option 1: File from Trigger Input (if you configured AttachedFile in Step 1.5)

| Flow Input | Map to |
|-----------|--------|
| `FileContent` | `Trigger.AttachedFile.Content` (or `Topic.AttachedFile.Content`) |
| `FileName` | `Trigger.AttachedFile.Name` (or `Topic.AttachedFile.Name`) |

#### Option 2: File from Activity Attachments (most common)

If the file comes from the user's message, it's stored in the conversation activity:

| Flow Input | Map to |
|-----------|--------|
| `FileContent` | `Activity.Attachments[0].Content` |
| `FileName` | `Activity.Attachments[0].Name` |

> 💡 **How to find the right variable**: When you click on the Flow input field, a **Dynamic content** panel opens. Search for:
> - "Attachment" — look for variables containing the file content
> - "File" — look for file-related variables
> - "Activity" — look for activity-level content
> 
> The variable name might be `@Conversation.Attachments`, `@Activity.Attachments`, or `@Trigger.Attachments` depending on your version. Pick the one that gives you the base64 content and file name.

#### Option 3: Using Power Automate Direct Connection

If you cannot find the attachment in Copilot Studio variables:
1. In the Power Automate flow, add a **SharePoint Get file content** action before the Compose action
2. Ask the user for the file name only (as a text input)
3. Use the file name to retrieve the file from SharePoint
4. Then encode it to base64 in Power Automate using: `@{base64(body('Get_file_content'))}`

### 5.4 Add a Message Node (Show Results)

1. Below the Action node, click the **+ Add node** icon
2. Select **Send a message**
3. In the text box, enter:

```
✅ Rapport Excel généré avec succès!

@{outputs('AnswerText')}

📥 Téléchargez le rapport Excel:
@{outputs('FileLink')}
```

> Use the **Dynamic content** tab to insert the flow outputs. Click **{x}** in the toolbar to insert variables.

### 5.5 Add Quick Replies (Optional)

1. In the message node toolbar, click **Add** → **Quick reply**
2. Add quick replies:
   - "Analyser un autre fichier"
   - "Voir le rapport PDF"
   - "Comparer deux matrices"

### 5.6 Save and Test

1. Click **Save**
2. Click **Test** in the top menu (test the topic in Copilot Studio)
3. Type one of the trigger phrases: **"Voici ma matrice de conformité"** and **attach an ODS file** to your test message
4. Verify the flow runs and you receive the summary + file link
5. Also test without attaching a file: type **"Donne-moi le rapport Excel"** — the condition node should ask you to attach one

---

## STEP 6: Handle Error Cases

### 6.1 Add a Condition Node

Between the Action node and the Message node, add error handling:

1. Below the Action node, click **+ Add node**
2. Select **Add a condition**
3. Configure the condition:
   - **Value 1**: `@{outputs('LEON_-_Excel_Conformity_Report')?['body/AnswerText']}`
   - **Operator**: `is not equal to`
   - **Value 2**: *(empty)*

   Or better, check the status:
   - **Value 1**: `@{body('Parse_JSON')?['status']}`
   - **Operator**: `is equal to`
   - **Value 2**: `answered`

4. In the **If yes** branch: Add the success message (from Step 5.4)
5. In the **If no** branch: Add an error message:

```
❌ Désolé, une erreur s'est produite lors de la génération du rapport Excel.

Vérifiez que:
• Le fichier est au format ODS ou XLSX
• Le fichier contient des colonnes "Conformité FNR" et "Commentaires FNR"
• Le fichier n'est pas corrompu

Réessayez avec un autre fichier ou contactez le support.
```

---

## STEP 7: Publish the Agent

### 7.1 Publish in Copilot Studio

1. In the left navigation, click **Publish**
2. Click **Publish** button
3. Confirm

### 7.2 Add to Teams (if not already done)

1. Go to **Channels** → **Teams**
2. Click **Turn on Teams** (if not already enabled)
3. Your agent is now available in Teams

### 7.3 Test in Teams

1. Open Microsoft Teams
2. Find your LEON agent in the Teams app list
3. Start a chat with LEON
4. Type: "Donne-moi le rapport Excel de conformité"
5. Upload an ODS file when prompted
6. Wait 30-60 seconds
7. You should receive:
   - A text summary with OK/NOK/NA counts
   - A link to download the Excel report

---

## STEP 8: Verify the Complete Flow

Use this checklist to verify everything works:

| # | Check | How to Verify |
|---|-------|---------------|
| 1 | Azure Function is live | `GET https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/health` returns 401 (running) |
| 2 | Excel endpoint works | POST to `/api/conformity-excel` with base64 ODS returns 200 + `reportExcel` |
| 3 | Copilot Studio topic exists | Topics page shows "Rapport Excel Conformité FNR" |
| 4 | Trigger phrases work | Type "rapport excel conformité" in test panel → topic triggers |
| 5 | **File in first message** | Type trigger phrase **+ attach ODS file** → file is captured by trigger |
| 6 | **No file = prompted** | Type trigger **without** file → condition catches it → asks to attach |
| 7 | Power Automate flow is published | Flows page shows flow as "Published" |
| 8 | Flow runs successfully | Test the flow manually with a real file |
| 9 | SharePoint folder exists | Verify `/Shared Documents/LEON Reports/` exists |
| 10 | Excel file is created | Check SharePoint folder after flow run |
| 11 | File link works | Click the link → Excel file downloads |
| 12 | Agent is published | Publish page shows latest version |
| 13 | Teams integration works | Test in Teams with real file |

---

## TROUBLESHOOTING

### Problem: "404 Not Found" from Azure Function
- **Cause**: The function endpoint is not deployed or the URL is wrong
- **Fix**: Verify the URL is `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel`
- **Check**: The function key is included as `?code=...` in the URL

### Problem: "500 Internal Server Error" from Azure Function
- **Cause**: The file content is not valid base64, or the file is corrupted
- **Fix**: Verify the `fileContent` field contains valid base64 data
- **Check**: The `fileName` field has the correct extension (.ods or .xlsx)

### Problem: "Timeout" in Power Automate
- **Cause**: The ODS file is very large and processing takes >100 seconds
- **Fix**: The 100-second limit is a Copilot Studio constraint. For very large files, consider:
  - Splitting the matrix into smaller files
  - Using the asynchronous response pattern (advanced)

### Problem: File link doesn't work in Teams
- **Cause**: SharePoint sharing link permissions
- **Fix**: In the "Create sharing link" action, set **Link Scope** to `Organization` or `Anonymous`

### Problem: Copilot Studio doesn't recognize the trigger phrase
- **Cause**: Not enough trigger phrases, or they're too different from what the user typed
- **Fix**: Add more trigger phrases (aim for 10+), including French and English variants

### Problem: Flow input mapping shows "Expression is invalid"
- **Cause**: The dynamic content references are wrong
- **Fix**: Use the **Dynamic content** tab (not Expression tab) to select outputs from previous actions

### Problem: "Activity.Attachments" variable is empty or not found
- **Cause**: The attachment may be stored under a different variable name in your Copilot Studio version
- **Fix**: 
  1. In the test panel, send a message with an attached file
  2. Open the **Variables** panel to see all available variables
  3. Look for: `Conversation.Attachments`, `Activity.Attachments`, `Trigger.Attachments`, or `Topic.AttachedFile`
  4. Use the correct variable name in the flow input mapping

### Problem: User attaches file but Power Automate receives empty content
- **Cause**: Copilot Studio may not pass file content in some configurations (e.g., Teams file upload vs. direct upload)
- **Fix**: 
  1. Try the **multipart/form-data** format instead of JSON
  2. In the Power Automate flow, change the HTTP action to accept multipart
  3. Or use a **SharePoint** approach: save the file to SharePoint first, then pass the file URL instead of content

### Problem: Condition node can't find attachment
- **Cause**: The attachment variable path is different
- **Fix**: Instead of checking `@Activity.Attachments`, check the count: `@activity.attachmentsCount > 0` or similar variable available in your version

---

## API REFERENCE (Quick Reference)

### Endpoint
```
POST https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/conformity-excel?code=<YOUR_FUNCTION_KEY>
```

### Request Body
```json
{
  "fileName": "Conformity_Matrix.ods",
  "fileContent": "<base64-encoded file content>"
}
```

### Response Body
```json
{
  "answer": "Rapport Excel genere avec succes.\nTotal: 988 exigences | OK: 940 | NOK: 0 | NA: 1\n...",
  "status": "answered",
  "confidence": "HIGH",
  "fileName": "Conformity_Matrix.ods",
  "analysis": {
    "summary": {
      "total": 988,
      "ok": 940,
      "nok": 0,
      "na": 1,
      "standby": 0,
      "unknown": 20,
      "empty": 27,
      "inconsistencies": 0
    }
  },
  "reportExcel": "<base64-encoded XLSX file>"
}
```

### Excel Report Contents
- **Sheet 1 "Summary"**: Status counts with color indicators
- **Sheet 2 "All Items"**: Every requirement with color-coded rows:
  - 🟢 Green (#C6EFCE) = OK
  - 🔴 Red (#FFC7CE) = NOK
  - 🟡 Yellow (#FFEB9C) = STANDBY
  - ⚪ Gray (#D9D9D9) = NA
  - 🔵 Light Gray (#E7E6E6) = UNKNOWN
- Auto-filter and freeze panes enabled

---

## POWER AUTOMATE FLOW SUMMARY (Visual)

```
┌─────────────────────────────────────────────────┐
│  TRIGGER: When an agent calls the flow           │
│  Inputs: FileContent (text), FileName (text)     │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  ACTION 1: Compose                                │
│  Inputs:                                         │
│    {                                              │
│      "fileName": "@{FileName}",                  │
│      "fileContent": "@{FileContent}"             │
│    }                                             │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  ACTION 2: HTTP POST                              │
│  Method: POST                                     │
│  URI: https://leon-spec-.../api/conformity-excel│
│       ?code=xNbsGVHo9Pqs...                      │
│  Headers: Content-Type: application/json          │
│  Body: @{outputs('Compose')}                      │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  ACTION 3: Parse JSON                             │
│  Content: @{body('HTTP')}                        │
│  Schema: (see Step 4.5 above)                    │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  ACTION 4: SharePoint Create File                  │
│  Site: <your SharePoint site>                     │
│  Folder: /Shared Documents/LEON Reports/          │
│  File Name: LEON_Conformity_Report_<timestamp>.xlsx│
│  Content: @{base64ToBinary(body('Parse_JSON')?['reportExcel'])}│
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  ACTION 5: SharePoint Create Sharing Link         │
│  File ID: @{outputs('Create_file')?['body/ItemId']}│
│  Link Type: View                                  │
│  Link Scope: Organization                         │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  ACTION 6: Respond to the agent                   │
│  Outputs:                                        │
│    AnswerText: (summary with counts)              │
│    FileLink: @{body('Create_sharing_link')?['link']}│
│    FileName: @{outputs('Create_file')?['body/Name']}│
└─────────────────────────────────────────────────┘
```

---

## EXACT COPILOT STUDIO TOPIC STRUCTURE (Visual)

### Main Flow: User Attaches File in First Message

```
┌──────────────────────────────────────────────────────────┐
│  TRIGGER NODE                                             │
│  Phrases:                                                 │
│    - "rapport excel conformité"                           │
│    - "donne-moi le rapport excel"                         │
│    - "excel conformity report"                            │
│    - "fichier excel conformité"                           │
│    - "télécharger excel conformité"                      │
│    - "export excel matrice conformité"                   │
│    - "rapport xlsx conformité"                            │
│    - "génère excel conformité FNR"                       │
│    - "voici ma matrice de conformité"                    │
│    - "matrice conformité FNR"                             │
│                                                           │
│  💡 The file attached to the user's message is           │
│     automatically captured (Activity.Attachments)        │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MESSAGE NODE: "Confirmation"                             │
│  Message:                                                 │
│    📊 Merci, j'ai bien reçu votre fichier.                │
│    Je génère le rapport Excel coloré...                   │
│    Résultats dans quelques instants...                    │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  CONDITION NODE: "Vérifier si fichier joint"              │
│                                                           │
│  Condition: @Activity.Attachments ≠ empty                 │
│  (ou @Topic.AttachedFile ≠ empty)                        │
└──────────────┬───────────────────────────────┬────────────┘
               │                               │
          YES (file present)              NO (no file)
               │                               │
               ▼                               ▼
┌──────────────────────┐     ┌──────────────────────────────────┐
│  TOOL NODE           │     │  MESSAGE NODE: "Ask for file"     │
│  Flow: LEON - Excel  │     │  Message:                         │
│  Conformity Report   │     │    Veuillez joindre votre        │
│  Inputs:             │     │    fichier de matrice de          │
│    FileContent →     │     │    conformité (ODS ou XLSX)      │
│      Activity.       │     │    dans votre message.            │
│      Attachments[0]. │     │                                  │
│      Content         │     │  → REDIRECT back to Trigger       │
│    FileName →        │     └──────────────────────────────────┘
│      Activity.       │     
│      Attachments[0]. │     
│      Name            │     
└──────────┬───────────┘     
           │                   
           ▼                   
┌──────────────────────────────────────────────────────────┐
│  CONDITION NODE: "Check Status"                            │
│  Condition: @body('Parse_JSON')?['status'] = "answered"   │
└──────────────┬───────────────────────────────┬────────────┘
               │                               │
          YES (success)                   NO (error)
               │                               │
               ▼                               ▼
┌──────────────────────┐     ┌──────────────────────────────┐
│  MESSAGE: "Success"  │     │  MESSAGE: "Error"            │
│                      │     │                              │
│  ✅ Rapport généré!  │     │  ❌ Erreur lors de           │
│  @{AnswerText}       │     │     la génération...         │
│                      │     │                              │
│  📥 Téléchargez:     │     │  Vérifiez que:               │
│  @{FileLink}         │     │  • Format ODS ou XLSX        │
│                      │     │  • Colonnes Conformité FNR   │
│  Quick replies:      │     │  • Fichier non corrompu      │
│  - Autre fichier     │     │                              │
│  - Rapport PDF       │     │  Quick replies:              │
│  - Comparer matrices │     │  - Réessayer                 │
└──────────────────────┘     └──────────────────────────────┘
```

### Alternative Flow: User Asks Without Attaching (then prompted)

If the user only types a phrase without attaching a file, the Condition node catches it and asks them to attach one. Once they attach it, the conversation continues through the same path:

```
User: "Donne-moi le rapport Excel"
    → Trigger fires (no file yet)
    → Confirmation message
    → Condition: NO file → asks to attach
    → User attaches file in next message
    → Redirect → Trigger re-evaluates → now has file
    → Tool node → results
```