# LEON — Copilot Studio + Azure Function Integration Guide

## €10,000 Enterprise Deployment — Complete Setup Instructions

---

## ARCHITECTURE OVERVIEW

```
┌──────────────────────────────────────────────────────────────────────┐
│                        COPILOT STUDIO (Teams)                        │
│  User: "What is the heartbeat signal?"                               │
│  Topic: "Ask LEON" → detects intent → calls Power Automate           │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ HTTP POST (JSON)
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      POWER AUTOMATE FLOW                             │
│  1. Receive question from Copilot Studio                             │
│  2. Add x-api-key header                                             │
│  3. HTTP POST → Azure Function                                       │
│  4. Parse response → return answer to Copilot Studio                 │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ HTTPS
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      AZURE FUNCTION (Python)                         │
│  function_app.py                                                     │
│    POST /api/ask    → Q&A handler                                    │
│    POST /api/validate → Spec validation                              │
│    POST /api/upload  → File upload                                   │
│    GET  /api/files   → List files                                    │
│    GET  /api/health   → Health check                                 │
│                                                                      │
│  Uses: azure_handler.py + app/qa/*  (same as FastAPI backend)        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## PART 1: PREREQUISITES (What You Need)

### No-Admin Deployment (works without admin rights)

You only need **Python** (already in your `.venv`) and a **browser**.

1. Install requests (already done):
   ```powershell
   .venv\Scripts\pip install requests
   ```

2. Open https://portal.azure.com in your browser (enterprise login)

3. That's it. No Azure CLI, no Node.js, no admin rights needed.

---

## PART 2: DEPLOY THE AZURE FUNCTION (No-Admin Method)

### Step 1: Create Function App via Azure Portal (ONE-TIME, 5 minutes)

Open https://portal.azure.com and:

1. **Create Resource Group**: `rg-leon-copilot-prod` (West Europe)
2. **Create Storage Account**: `stleonfuncprod` (Standard LRS)
3. **Create Function App**:
   - Name: `func-leon-spec-qa`
   - Runtime: Python 3.11
   - OS: Linux
   - Plan: Consumption (Serverless)
   - Region: West Europe

> Or use Azure Cloud Shell (browser-based terminal):
> Click the `>_` icon in the Azure Portal top bar, then paste the commands from the "CLI Method" section below.

### Step 2: Configure Settings via Portal

In your Function App → Settings → Environment variables, add:

| Name | Value |
|------|-------|
| `AZURE_OPENAI_ENDPOINT` | `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_API_KEY` | `YOUR_AZURE_OPENAI_KEY` |
| `AZURE_OPENAI_LLM_DEPLOYMENT` | `gpt-4o` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-large` |
| `API_KEY` | `choose-a-strong-api-key` |
| `PYTHON_ENABLE_WORKER_EXTENSIONS` | `1` |

Also enable CORS: Function App → API → CORS → Add `https://*.flow.microsoft.com`

### Step 3: Deploy Code (Python script)

```powershell
# From your project root (Spec AI Project):

# First, get your Publish Profile:
# Azure Portal → Function App → Overview → "Get publish profile"
# Save the downloaded XML file in your project root

# Then deploy:
.venv\Scripts\python.exe azure_function\deploy_no_admin.py --profile publish_profile.xml
```

This script:
- Copies app/ and data/ into the function package
- Installs pip dependencies
- Creates a ZIP file
- Uploads it to Azure via REST API
- Verifies the deployment

### Step 4: Get Function Key

1. In Azure Portal → Function App → Functions → `ask`
2. Click "Function Keys" → Copy the `default` key
3. Save this key — you need it for Copilot Studio

---

## PART 2B: CLI Method (if you have Azure CLI)

```powershell
$resourceGroup = "rg-leon-copilot-prod"
$location = "westeurope"
$storageName = "stleonfuncprod"
$functionAppName = "func-leon-spec-qa"

az group create --name $resourceGroup --location $location
az storage account create --name $storageName --resource-group $resourceGroup --location $location --sku Standard_LRS
az functionapp create --name $functionAppName --resource-group $resourceGroup --storage-account $storageName --consumption-plan-location $location --runtime python --runtime-version 3.11 --functions-version 4 --os-type Linux

az functionapp config appsettings set --name $functionAppName --resource-group $resourceGroup --settings "AZURE_OPENAI_ENDPOINT=..." "AZURE_OPENAI_API_KEY=..." "API_KEY=..."

cd azure_function
func azure functionapp publish $functionAppName --python
```

---

## PART 3: COPILOT STUDIO CONFIGURATION

### Option A: Power Automate (RECOMMENDED for enterprise)

#### Step 1: Create Power Automate Flow

1. Go to https://make.powerautomate.com
2. Click **Create** → **Instant cloud flow**
3. Name: `LEON-QA-Flow`
4. Trigger: `When Power Apps calls a flow` (or `Manually trigger a flow`)
5. Add input parameter: `question` (type: Text)

#### Step 2: Add HTTP Action

1. Click **+ New step**
2. Search for **HTTP**
3. Select **HTTP** action (the premium one)
4. Configure:
   - **Method**: POST
   - **URI**: `https://<your-function-url>/api/ask?code=<function-key>`
   - **Headers**:
     ```
     Content-Type: application/json
     x-api-key: YOUR_API_KEY
     ```
   - **Body**:
     ```json
     {
       "question": "@{triggerBody()['text']}"
     }
     ```

#### Step 3: Parse Response

1. Add **Parse JSON** action after HTTP
2. Content: `Body` from HTTP action
3. Schema:
   ```json
   {
     "type": "object",
     "properties": {
       "answer": { "type": "string" },
       "status": { "type": "string" },
       "confidence": { "type": "string" },
       "sources": { "type": "array" }
     }
   }
   ```

#### Step 4: Return to Copilot Studio

1. Add **Respond to Power Apps or Flow** action
2. Output: `answer` → `body('Parse_JSON')?['answer']`

### Option B: Direct HTTP from Copilot Studio Topic (Simpler)

1. Go to https://copilotstudio.microsoft.com
2. Open your bot → **Topics** → **+ Add a topic** → **From blank**
3. Name: `Ask Specification`
4. Add trigger phrases:
   ```
   What is the heartbeat signal?
   What are the functional requirements?
   Where is the ASU located?
   Validate the document
   What should I put in the PURPOSE section?
   How do I write the requirements?
   ```
5. Add **Question** node:
   - Message: "What would you like to know about the specification?"
   - Variable: `VarQuestion` (text)
6. Add **Call an action** → **Create a flow**
7. Select the Power Automate flow created above
8. Map `VarQuestion` → `question` input
9. Add **Message** node: `{x}answer` → display the answer

---

## PART 4: TESTING

### Test 1: Direct Function Test (before Copilot Studio)

```powershell
# Test the ask endpoint
$body = @{ question = "Where is the ASU located?" } | ConvertTo-Json
Invoke-RestMethod `
  -Uri "https://<function-url>/api/ask?code=<function-key>" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body

# Test the validate endpoint
$body = @{ fileName = "00692_25_01250_ASU_Technical_Specification_SPX _1_.docx" } | ConvertTo-Json
Invoke-RestMethod `
  -Uri "https://<function-url>/api/validate?code=<function-key>" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body

# Test health check
Invoke-RestMethod `
  -Uri "https://<function-url>/api/health?code=<function-key>"
```

### Test 2: Power Automate Flow Test

1. Open the flow in Power Automate
2. Click **Test** → **Manually**
3. Enter a question: "What is the purpose of the ASU specification?"
4. Verify the answer is returned correctly

### Test 3: Copilot Studio End-to-End

1. Open your bot in Copilot Studio
2. Click **Test bot** (bottom left)
3. Type: "What is the heartbeat signal frequency?"
4. Verify the answer is returned and displayed

---

## PART 5: DATA & FILE MANAGEMENT

### Uploading Spec Files

Since Copilot Studio doesn't natively handle file uploads to Azure Functions,
use one of these approaches:

**Option A: Separate upload tool**
- Keep your existing FastAPI server for file uploads
- Or create a simple Power App for file upload

**Option B: SharePoint integration**
1. Store spec files in a SharePoint document library
2. Power Automate copies them to the Azure Function on upload
3. Call `POST /api/upload` from Power Automate

**Option C: Blob Storage trigger**
1. Upload spec files to Azure Blob Storage
2. Create a Blob-triggered Function that indexes on upload
3. Add to function_app.py:
```python
@app.blob_trigger(arg_name="myblob", path="spec-uploads/{name}",
                   connection="AzureWebJobsStorage")
def on_spec_upload(myblob: func.InputStream):
    logging.info(f"New spec uploaded: {myblob.name}")
    handle_upload(myblob.name, myblob.read())
```

### Reference Documents (Template + Writing Guide)

The template and writing guide DOCX files must be included in the deployment:
```
azure_function/
└── data/
    └── refs/
        ├── Component_or_Part_Specification_Template 1.docx
        └── Component_or_Part_Specification_Writing_guide 1.docx
```

For production, store these in Azure Blob Storage and download on cold start.

---

## PART 6: MONITORING & LOGS

### Application Insights

```powershell
# Query logs
az monitor app-insights query `
  --apps $appInsightsName `
  --resource-group $resourceGroup `
  --analytics-query "traces | where timestamp > ago(1h) | project timestamp, message | order by timestamp desc | take 50"
```

### Key Metrics to Monitor

| Metric | What to watch |
|--------|--------------|
| Request count | Track usage volume |
| Response time | Should be < 5 seconds |
| Error rate | Should be < 1% |
| Cold start time | First request after idle may be 5-10s |
| Memory usage | Stay under 1.5 GB (Consumption limit) |

---

## PART 7: SECURITY CHECKLIST

- [ ] API key added to function settings (`API_KEY`)
- [ ] Function-level auth enabled (default: `FUNCTION` level)
- [ ] HTTPS enforced (default in Azure)
- [ ] CORS restricted to Power Automate domains only
- [ ] Azure OpenAI key stored in Key Vault (not plain text)
- [ ] Network restrictions on Function App (optional: VNet integration)
- [ ] Audit logging enabled via Application Insights

### Upgrade to Key Vault (Recommended)

```powershell
# Store secrets in Key Vault instead of app settings
az keyvault create --name kv-leon-prod --resource-group $resourceGroup
az keyvault secret set --vault-name kv-leon-prod --name openai-key --value "YOUR_KEY"

# Reference in Function App settings:
az functionapp config appsettings set `
  --name $functionAppName `
  --resource-group $resourceGroup `
  --settings "AZURE_OPENAI_API_KEY=@Microsoft.KeyVault(SecretUri=https://kv-leon-prod.vault.azure.net/secrets/openai-key/)"
```

---

## EXPECTED RESPONSE FORMATS

### /api/ask response
```json
{
  "answer": "The ASU is located under the hood, in the plenum, or at the rear left arch.",
  "status": "answered",
  "confidence": "HIGH",
  "sources": [
    {"fileName": "ASU_Spec.docx", "excerpt": "The ASU is located under the hood..."}
  ],
  "evidence": ["ASU_Spec.docx"]
}
```

### /api/validate response
```json
{
  "answer": "Verdict: ACCEPTABLE_WITH_FIXES (82%): ...",
  "status": "answered",
  "confidence": "",
  "validationReport": {
    "verdict": "ACCEPTABLE_WITH_FIXES",
    "overallScore": 0.82,
    "scores": {"structure": 0.95, ...},
    "findings": [...],
    "rulesUsed": {"mandatory_sections_count": 41, "writing_guide_rules_count": 63}
  }
}
```

---

## ROLES & RESPONSIBILITIES (Who Does What)

| Task | Responsible | Estimated Time |
|------|------------|----------------|
| Install Azure CLI + Func Tools | You (user) | 15 min |
| Create Azure resources (RG, Storage, Function) | You (user) | 10 min |
| Set environment variables in Azure | You (user) | 5 min |
| Copy app code + deploy | You (user) | 10 min |
| Test function endpoints | You (user) | 15 min |
| Create Power Automate flow | You (user) | 20 min |
| Configure Copilot Studio topic | You (user) | 15 min |
| End-to-end testing | Together | 20 min |
| **TOTAL** | | **~2 hours** |
