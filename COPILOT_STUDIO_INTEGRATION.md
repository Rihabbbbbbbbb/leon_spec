# LEON → Copilot Studio Integration Guide
**Function URL:** `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net`
**Auth:** Function key (append `?code=<KEY>` to every call)

---

## Quick Test (PowerShell)

```powershell
$key = "YOUR_FUNCTION_KEY"
$base = "https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net"

# Health check
Invoke-RestMethod "$base/api/health?code=$key"

# Ask a question
Invoke-RestMethod "$base/api/ask?code=$key" -Method POST -Body '{"question":"Where is the ASU?"}' -ContentType "application/json"

# Validate a spec
Invoke-RestMethod "$base/api/validate?code=$key" -Method POST -Body '{"fileName":"spec_extracted.txt"}' -ContentType "application/json"

# Upload a file (base64 JSON — RECOMMENDED for Copilot Studio)
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("my_spec.docx"))
$body = @{fileName="my_spec.docx"; fileContent=$b64} | ConvertTo-Json
Invoke-RestMethod "$base/api/upload?code=$key" -Method POST -Body $body -ContentType "application/json"
```

---

## Step 1: Get Your Function Key

**In Azure Portal:**
1. Open [leon-spec Function App](https://portal.azure.com/#@shiftup.onmicrosoft.com/resource/subscriptions/24ecd15b-fbf4-4574-b22d-57ff83e2c440/resourceGroups/MLWorkloadsRG/providers/Microsoft.Web/sites/leon-spec/appServices)
2. Go to **Functions** → click **health** (any function works, they share keys)
3. Click **Function Keys** in the sidebar
4. Copy the **default** key value (or click "Add new function key" to create one specifically for Copilot Studio)

Save this key — you'll need it for all calls below.

---

## Step 2: Create a Power Automate Flow

Copilot Studio talks to external APIs via **Power Automate**. Here's the setup:

### 2a. Create a new Instant Cloud Flow
1. Go to [Power Automate](https://make.powerautomate.com)
2. **Create** → **Instant cloud flow**
3. Name: `LEON-Ask-Question`
4. Trigger: **When an HTTP request is received** (this is what Copilot Studio calls)

### 2b. Add the HTTP action to call LEON
5. Click **+ New step** → search for **HTTP**
6. Add the **HTTP** action (premium connector)
7. Configure:

| Field | Value |
|-------|-------|
| Method | `POST` |
| URI | `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/ask?code=YOUR_FUNCTION_KEY` |
| Headers | `Content-Type: application/json` |
| Body | `{"question": "@{triggerBody()?['text']}"}` |

> Replace `YOUR_FUNCTION_KEY` with the key from Step 1.
> `triggerBody()?['text']` passes the user's question from Copilot Studio.

### 2c. Parse the response
8. Add a **Parse JSON** action after the HTTP call:

```json
{
  "type": "object",
  "properties": {
    "answer": { "type": "string" },
    "status": { "type": "string" },
    "confidence": { "type": "string" },
    "sources": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "fileName": { "type": "string" },
          "excerpt": { "type": "string" }
        }
      }
    }
  }
}
```

### 2d. Return the response
9. Add a **Response** action (from the Request trigger)
10. Set:
    - **Status Code:** `200`
    - **Body:**
```json
{
  "answer": "@{body('Parse_JSON')?['answer']}",
  "confidence": "@{body('Parse_JSON')?['confidence']}"
}
```

### 2e. Save the flow
11. Click **Save** → copy the **HTTP POST URL** from the trigger (you'll need it for Copilot Studio)

---

## Step 3: Connect to Copilot Studio

### 3a. Create a Custom Topic
1. Go to [Copilot Studio](https://copilotstudio.microsoft.com)
2. Open your bot → **Topics** → **+ Add a topic** → **From blank**
3. Name: `Ask LEON`

### 3b. Add trigger phrases
4. In the **Trigger** node, add phrases like:
   - "ask leon about the ASU"
   - "what does the spec say about"
   - "question about specification"
   - "where is the ASU located"

### 3c. Call Power Automate
5. Click **+** → **Call an action** → **Create a flow** (or select existing)
6. Select your `LEON-Ask-Question` flow
7. Map the inputs:
   - **text** → `System.Activity.Text` (the user's question)

### 3d. Display the answer
8. Add a **Message** node after the flow call
9. Set the message to:
```
@{PowerAutomate('LEON-Ask-Question').answer}

(Confidence: @{PowerAutomate('LEON-Ask-Question').confidence})
```

### 3e. Publish
10. Click **Publish** in the top right

---

## Step 4: File Upload — Copilot Studio Specific Configuration

### ⚠️ CRITICAL: The OpenAPI Schema Fix

Copilot Studio uses the OpenAPI schema to determine input types. If `file` is
imported as a **flat String**, AI Dynamic Filling will NOT pass the actual file
content — it will send just the file name or nothing.

**After re-importing the OpenAPI, verify the input type:**
1. Open your Copilot Studio action → **Entrées** (Inputs)
2. Click on the `file` input
3. Check the **data type**:
   - ✅ `Object` with nested `name` and `contentBytes` → **CORRECT**
   - ❌ `String` → **WRONG** — re-import the OpenAPI

The latest OpenAPI defines `file` as:
```json
{
  "file": {
    "type": "object",
    "required": ["name", "contentBytes"],
    "properties": {
      "name": {"type": "string"},
      "contentBytes": {"type": "string"}
    }
  }
}
```

### 4a. Re-import the OpenAPI in Copilot Studio
1. Go to Copilot Studio → **Topics** → **Settings** (gear icon)
2. **Connections** → **mia final LEON** → **Refresh** or **Re-import**
3. Select the updated `leon-api-openapi2.json` from this project
4. Verify the upload action shows `file` as an **Object** type

### 4b. Diagnostic: Use the Debug Endpoint
If uploads still fail, use the debug endpoint to see exactly what arrives:
1. Import `POST /api/debug-request` as a tool
2. Call it from Copilot Studio chat with a file attachment
3. The response shows exactly what Copilot Studio sent
4. If `has_file` is `false` or `file_field_type` is `str`, the schema wasn't imported correctly

### 4c. Power Automate Flow (if using manual HTTP instead of connector)
```json
{
  "file": {
    "name": "@{triggerBody()?['file']?['name']}",
    "contentBytes": "@{triggerBody()?['file']?['contentBytes']}"
  }
}
```

### 4d. Validation Flow
After upload succeeds, ask to validate:
1. Call `/api/upload` → get `fileName` from response
2. Call `/api/validate` with `{"fileName": "<returned fileName>"}`
3. Display the validation verdict and score

---

## Step 4: Test End-to-End

1. Open **Microsoft Teams**
2. Find your Copilot Studio bot
3. Type: **"Where is the ASU located in the vehicle?"**
4. Expected response:
   > *The ASU is located under the hood, in the plenum, or at the rear left arch of the vehicle.*

---

## Additional Endpoints You Can Expose

### Validation Topic
Create a second flow/topic for `/api/validate`:
- **URI:** `https://leon-spec...azurewebsites.net/api/validate?code=KEY`
- **Body:** `{"fileName": "ASU_Spec.docx"}`

### File Upload Topic
Create a third flow/topic for `/api/upload`:
- **URI:** `https://leon-spec...azurewebsites.net/api/upload?code=KEY`
- Use multipart form with field `file` containing the .docx

---

## API Reference (for Power Automate)

### POST /api/ask
```json
// REQUEST
{ "question": "Where is the ASU located?" }

// RESPONSE
{
  "answer": "The ASU is located under the hood...",
  "status": "answered",
  "confidence": "HIGH",
  "sources": [
    { "fileName": "ASU_Spec.docx", "excerpt": "The ASU is located..." }
  ],
  "evidence": ["ASU_Spec.docx"]
}
```

### POST /api/validate
```json
// REQUEST
{ "fileName": "ASU_Spec.docx" }

// RESPONSE
{
  "answer": "Verdict: ACCEPTABLE_WITH_FIXES (83%)...",
  "validationReport": { ... }
}
```

### GET /api/health
```json
// RESPONSE
{ "status": "healthy", "version": "2.0.0-enterprise" }
```
