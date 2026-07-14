# LEON Deployment — Final Status & Manual Action Checklist
**Date: 2026-06-26 | Project value: €10,000 | Status: CODE DEPLOYED ✅**

---

## ✅ What I Completed Automatically

### 1. Route Prefix Bug — FIXED & DEPLOYED
- **Problem:** All 5 routes in `function_app.py` were double-prefixed (`route="api/ask"` → served at `/api/api/ask`)
- **Fix:** Changed all routes to remove the `api/` prefix (`route="ask"` → served at `/api/ask`)
- **Verified:** Old `/api/api/health` → 404, New `/api/health` → 401 (live)

### 2. Deploy Script — Updated for Flex Consumption
- `deploy_no_admin.py` now supports OneDeploy (`--method onedeploy`) with correct `Content-Type: application/zip`
- Kudu zipdeploy kept as fallback (`--method kudu`) for non-Flex plans
- `--method auto` tries OneDeploy first, falls back to Kudu
- PowerShell deploy script `_deploy_ps.ps1` created (uses WinINET for NTLM proxy)

### 3. Function Redeployed — LIVE
- **Deployment ID:** `d257218c-d594-4a17-8920-fbc521222def`
- **Status:** 4 (Success), complete: true, active: true
- **Deployer:** LegionOneDeploy
- **URL:** `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net`
- **Endpoints (all live at correct paths):**
  - `POST /api/ask` — Q&A
  - `POST /api/validate` — Spec validation
  - `POST /api/upload` — File upload
  - `GET  /api/files` — List files
  - `GET  /api/health` — Health check

### 4. Azure Search Index — VERIFIED
- **255/255 documents** indexed (verified via API)
- Search queries return results (tested "ASU" → 3 results)
- Index: `leon-specs-index` on `leon-spec-search-915f.search.windows.net`
- Embeddings: 3072-dim from `text-embedding-3-large` via `Ragchatbotemwh.services.ai.azure.com`

### 5. Code Integrity — VERIFIED
- All Python files compile cleanly (no syntax errors)
- `azure_search.py` — `_search_client_index` global tracks index name (no private attribute access)
- `index_specs_to_search.py` — document keys sanitized with `re.sub(r"[^A-Za-z0-9_\-=]", "_", ...)`
- `embeddings.py` — OpenAI client correctly configured for Azure endpoint
- `azure_config.py` — environment variable overrides work for Azure Functions
- `.gitignore` — `.env`, `publish_profile.xml`, `*.PublishSettings` all excluded

---

## 🚨 What YOU Must Do Manually

### ACTION 1: Set Environment Variables in Azure Portal (CRITICAL — DO THIS FIRST)

**The function will NOT work correctly until these are set.** The code reads from environment variables, and the Portal currently has leftover wrong-named settings.

1. Go to: [leon-spec Function App](https://portal.azure.com/#@shiftup.onmicrosoft.com/resource/subscriptions/24ecd15b-fbf4-4574-b22d-57ff83e2c440/resourceGroups/MLWorkloadsRG/providers/Microsoft.Web/sites/leon-spec/appServices)
2. Navigate to: **Settings → Environment variables**
3. **Add/Update these 8 variables EXACTLY:**

| Name | Value |
|------|-------|
| `AZURE_OPENAI_ENDPOINT` | `https://Ragchatbotemwh.services.ai.azure.com/openai/v1/` |
| `AZURE_OPENAI_API_KEY` | `<YOUR_AZURE_OPENAI_API_KEY>` |
| `AZURE_OPENAI_LLM_DEPLOYMENT` | `gpt-4o` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-large` |
| `AZURE_SEARCH_ENDPOINT` | `https://leon-spec-search-915f.search.windows.net` |
| `AZURE_SEARCH_API_KEY` | `<YOUR_AZURE_SEARCH_API_KEY>` |
| `AZURE_SEARCH_INDEX_NAME` | `leon-specs-index` |
| `API_KEY` | *(choose a strong key for Copilot Studio auth)* |

4. **Delete these leftover WRONG-named variables if they exist:**
   - `AZURE_AISEARCH_ENDPOINT` (wrong name — code won't read it)
   - `AZURE_AISEARCH_KEY` (wrong name)
   - `AZURE_OPENAI_KEY` (wrong name — code has a fallback but don't rely on it)
   - Any `AZURE_OPENAI_ENDPOINT` pointing at `leon-spec-openai` (WRONG resource — vectors won't match)

5. Click **Apply** and wait for the function to restart

> **⚠️ WHY THIS MATTERS:** The 255 embeddings in your search index were computed using `Ragchatbotemwh.services.ai.azure.com`. If the function's `AZURE_OPENAI_ENDPOINT` points at a different resource, query-time embeddings will be in a different vector space — the function will return 200 OK but give garbage answers.

### ACTION 2: Get the Function Key & Test

1. In the Portal: **leon-spec → Functions → `health` → Function Keys**
2. Copy the **default function key**
3. Open PowerShell and run:

```powershell
$key = "<PASTE YOUR KEY HERE>"

# Test health (should return {"status":"healthy","version":"2.0.0-enterprise"})
Invoke-RestMethod -Uri "https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/health?code=$key"

# Test Q&A (should return an answer about the ASU)
$body = '{"question":"Where is the ASU located?"}'
Invoke-RestMethod -Uri "https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/ask?code=$key" -Method POST -Body $body -ContentType "application/json"
```

> **Note:** From your corporate network, `Invoke-RestMethod` uses WinINET and handles the NTLM proxy automatically. If you get connection errors, it's the proxy, not the function.

### ACTION 3: Verify in Azure Portal

1. Go to: [leon-spec-search-915f](https://portal.azure.com/#@shiftup.onmicrosoft.com/resource/subscriptions/24ecd15b-fbf4-4574-b22d-57ff83e2c440/resourceGroups/MLWorkloadsRG/providers/Microsoft.Search/searchServices/leon-spec-search-915f/overview)
2. Check that the search service is active
3. Go to **Search management → Indexes** → confirm `leon-specs-index` exists with 255 documents

---

## 📋 Deployment Architecture Summary

```
Copilot Studio (Teams)
    ↓ Power Automate HTTP
Azure Function: leon-spec (Flex Consumption, France Central)
    ├── POST /api/ask      → Azure AI Search (hybrid vector+text) → Azure OpenAI GPT-4o
    ├── POST /api/validate → Evidence-based validation against CTS template
    ├── POST /api/upload   → Save spec + rebuild index
    ├── GET  /api/files    → List accessible specs
    └── GET  /api/health   → Health check

Azure AI Search: leon-spec-search-915f
    └── Index: leon-specs-index (255 chunks, 3072-dim HNSW)

Azure OpenAI: Ragchatbotemwh.services.ai.azure.com
    ├── gpt-4o (LLM)
    └── text-embedding-3-large (embeddings)
```

---

## 🔧 Files Modified in This Session

| File | Change |
|------|--------|
| `azure_function/function_app.py` | Removed `api/` prefix from all 5 routes |
| `azure_function/deploy_no_admin.py` | Added OneDeploy support, fixed content type to `application/zip` |
| `_deploy_ps.ps1` | New PowerShell deploy script (NTLM proxy compatible) |
| `_build_zip.py` | New ZIP builder (no bundled deps for Flex remote build) |
