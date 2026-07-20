# LEON Spec Validator — Copilot Studio Integration Guide (v5.0 — 2026-07-19)

**Function URL:** `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net`
**Auth:** Anonymous (no API key required)
**Connector file:** `LEON_Spec_Custom_Connector.json` v3.1.0 (Swagger 2.0)
**Status:** ✅ Connector schema fixed — ready for Copilot Studio import & test

---

## 🔴 ROOT CAUSE OF THE BLOCKING ERROR

### Error Message
```
Input variable 'File content (base64)' is of incorrect type: Record
```

### Root Cause
The connector schema defined `fileContent` as **`type: file`** inside a JSON `body` parameter.
In Swagger 2.0, `type: file` is only valid for `multipart/form-data` file uploads.
When used inside `consumes: ["application/json"]` body parameters, Copilot Studio interprets
the input as a **base64 string** (hence "(base64)" in the error name), but the Copilot Studio
binding was passing a **FileDataType / Record** object → type mismatch → "incorrect type: Record".

### The Fix (v3.1.0)
Changed `UploadAndValidateInput.fileContent` from `type: file` → **`type: string`**.
This allows the binding to pass a **JSON string** (produced by Power Fx `JSON(..., IncludeBinaryData)`)
which the Azure Function backend already parses (Case 8 in `_decode_file_content`).

---

## ✅ PRIMARY APPROACH — Direct File Upload (Recommended)

Uses the `UploadAndValidate` action on the custom connector. The user attaches a DOCX file
to their message; Copilot Studio sends it to the Azure Function as a JSON string.

### Step 1: Import the Custom Connector

1. Go to https://make.powerautomate.com → **Data** → **Custom connectors**
2. Click **+ New custom connector** → **Import an OpenAPI file**
3. Upload `LEON_Spec_Custom_Connector.json` (v3.1.0)
4. Name it **LEON Spec Validator**
5. Click **Create connector**
6. On the **Definition** tab, verify the `UploadAndValidate` action shows:
   - `fileName` — string, required
   - `fileContent` — **string**, required (NOT "file" / "base64")
7. Click **Update connector** (if any changes were made)
8. Go to **Test** tab → **+ New connection** → create a connection (no API key needed)

> ⚠️ **If the old connector is cached**: Delete the old "LEON Spec Validator" connector
> and re-import v3.1.0. Copilot Studio caches connector schemas — a stale `type: file`
> schema will keep producing the "incorrect type: Record" error even after the file is fixed.

### Step 2: Configure the Copilot Studio Topic

For each topic ("Validate Document" `861fdd59...` and "Validate Specification" `f1b10181...`):

1. **Trigger phrases**: "validate document", "validate this spec", "check my document", etc.
2. **Question node** (optional — for explicit file prompt):
   - Entity: **FilePrebuiltEntity**
   - Prompt: "Please attach the specification file (.docx) you want to validate."
   - Skip behavior: **Allow question to be skipped** (so if user already attached a file with their message, the question is skipped)
   - Save response as: `Topic.Var1` (not used directly in bindings — see below)
3. **Call a tool node** → select **LEON Spec Validator** → **UploadAndValidate** action
4. Set inputs to **"From a variable"** (NOT "Remplissage dynamique avec l'IA"):

   | Input | Value |
   |-------|-------|
   | `fileName` | `=First(System.Activity.Attachments).Name` |
   | `fileContent` | `=JSON(First(System.Activity.Attachments).Content, JSONFormat.IncludeBinaryData)` |

   > ⚠️ **Do NOT** use `=Topic.Var1.Content` or `=Topic.Var1.ContentUrl` —
   > `Topic.Var1` from `FilePrebuiltEntity` is a **Blob** type and the dot operator (`.`)
   > is forbidden on Blob values in Power Fx.
   >
   > `System.Activity.Attachments` is a **collection** (not a Blob) — the dot operator
   > works on it. `First(...).Content` returns a FileDataType, and `JSON(..., IncludeBinaryData)`
   > converts it to a **string** like `{"$content-type":"...","$content":"UEsDBBQ..."}`.

5. **Message node** — display the result:

   ```
   📋 Validation Result
   Verdict: {Tool.UploadAndValidate.verdict}
   Score: {Tool.UploadAndValidate.overallScore}
   
   {Tool.UploadAndValidate.answer}
   
   📄 Download: {Tool.UploadAndValidate.documentUrl}
   ```

### Step 3: Test (DO NOT PUBLISH)

1. Open the **Test pane** in Copilot Studio
2. Type a trigger phrase: "validate this spec"
3. When prompted, attach a .docx file (e.g., from the `upload/` folder)
4. The bot should call the tool and return a verdict + score

> **Do NOT click Publish** — test only, per user request.

---

## 🔄 Why This Works (Technical Chain)

```
User attaches DOCX
    ↓
Copilot Studio stores it in System.Activity.Attachments
    ↓
=First(System.Activity.Attachments).Content  →  FileDataType (binary)
    ↓
=JSON(..., JSONFormat.IncludeBinaryData)  →  String: {"$content-type":"...","$content":"UEsDBBQ..."}
    ↓
Connector input (type: string) accepts it  →  NO type mismatch
    ↓
Azure Function receives JSON body: { "fileName": "...", "fileContent": "{\"$content-type\":\"...\",\"$content\":\"UEsDBBQ...\"}" }
    ↓
_decode_file_content Case 8: parses JSON string → extracts $content → base64 decode → file bytes
    ↓
handle_upload_and_validate: save → index → validate → return JSON with verdict/score/report
```

### Why the RAI Content Filter Does NOT Trigger
- The `$content` field is **base64-encoded binary** — it is NOT readable text
- Copilot Studio's Azure OpenAI RAI filter scans for prompt injection in readable text
- Base64 strings are not flagged as `openAIndirectAttack`
- Only the short `fileName` string passes through as readable text (e.g., "ASU_Spec.docx")

---

## 🔄 FALLBACK APPROACH — URL-Based (No File Upload)

If the `JSON(..., IncludeBinaryData)` expression produces a PowerFxError in your
Copilot Studio environment (some tenants disable `IncludeBinaryData`), use the
URL-based approach instead:

### Step 1: User Uploads File via Web Page
1. User opens: `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/upload-page`
2. Uploads their .docx file → gets a public Blob Storage URL (7-day SAS token)

### Step 2: Copilot Studio Topic (Text input, NOT File)
1. **Question node**: Entity = **Text** (NOT FilePrebuiltEntity)
   - Prompt: "Please paste the URL to your specification file."
   - Save response as: `Topic.Var1` (this is a **string**, not a Blob)
2. **Call a tool node** → **LEON Spec Validator** → **ValidateFromUrl** action
3. Set inputs:

   | Input | Value |
   |-------|-------|
   | `fileUrl` | `=Topic.Var1` |
   | `fileName` | (leave empty — derived from URL) |

4. Display result same as primary approach.

> ✅ This approach avoids ALL type issues — `Topic.Var1` is a plain string (Text entity),
> and `fileUrl` is `type: string` in the connector. No Blob, no FileDataType, no JSON conversion.

---

## ❌ WHAT DOES NOT WORK (Do NOT Retry)

| Approach | Error |
|----------|-------|
| `type: file` in Swagger 2.0 body parameter | "should be one of: array, boolean, integer, number, object, string" / "incorrect type: Record" |
| `fileContent: =Topic.Var1.Content` | FileDataType is a Record, not a string → "incorrect type: Record" |
| `fileContent: =Topic.Var1.ContentUrl` | "L'opérateur « . » ne peut pas être utilisé sur les valeurs Blob" |
| `fileContent: =Text(Topic.Var1)` | "BlobToText not implemented" |
| `fileContent: =Topic.Var1` (File object → string input) | Type mismatch: FileDataType ≠ StringDataType |
| `JSONFormat.IncludeBinaryData` in a **SetVariable** node | Runtime error (only works in tool input bindings) |
| `fileUrl: =Topic.FileVar.ContentUrl` | `ContentUrl` doesn't exist on File/Blob type |
| Connection Manager URL | Conversation-specific URL expired → stuck loading |

---

## 📋 TOPIC ARCHITECTURE (3 Topics)

| # | Topic | ID | Status | Role |
|---|-------|----|--------|------|
| 1 | **Validate Document** | `861fdd59...` | ON | Entry point; matches "validate document", "validate this spec", "check my document" |
| 2 | **Validate Specification** | `f1b10181...` | ON | Has Question + Call a tool (UploadAndValidate) |
| 3 | **Spec Validatorrrrrrrrrrr** | unknown | MISSING/DELETED | Was redirect target of #1 — DO NOT recreate |

### Routing Fix (Already Applied)
Both topics #1 and #2 now have **direct Question + Call a tool** nodes (no redirects to
deleted topics). Topic #1 ("Validate Document") should either:
- **Option A**: Redirect to topic #2 ("Validate Specification") which has the tool call, OR
- **Option B**: Have its own Question + Call a tool node (same bindings as topic #2)

> ⚠️ Do NOT redirect to the deleted "Spec Validatorrrrrrrrrrr" topic — it no longer exists.

---

## 🔧 TROUBLESHOOTING

### "Incorrect type: Record" still appears after connector update
1. **Delete the old connector** in Power Automate (Data → Custom connectors → ... → Delete)
2. **Re-import** `LEON_Spec_Custom_Connector.json` v3.1.0
3. **Recreate the connection** (Test tab → + New connection)
4. **Re-select the tool** in the Copilot Studio topic (the tool reference may be stale)
5. **Re-bind the inputs** using the exact expressions above

### "File content (base64)" still shows as input name
This means Copilot Studio is using a **cached connector schema**. The v3.1.0 connector
has `x-ms-summary: "File content (JSON string)"` — if you still see "(base64)", the
old schema is cached. Delete and re-import the connector.

### PowerFxError on `=JSON(..., JSONFormat.IncludeBinaryData)`
Some Copilot Studio tenants disable `JSONFormat.IncludeBinaryData`. If this happens:
- Use the **Fallback URL-based approach** (see above)
- OR use a **Power Automate flow** (flow trigger receives File natively, `base64()` converts it)

### Tool returns 422 "Could not extract text"
- Verify the file is a valid .docx (not .doc, not a renamed .zip)
- Verify the file is not password-protected
- Test the file directly: `curl -X POST https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net/api/upload-and-validate -H "Content-Type: application/json" -d '{"fileName":"test.docx","fileContent":"<base64>"}'`

### Tool returns 502 "Failed to download file from URL"
- Only applies to the URL-based approach
- Verify the URL is publicly accessible (not behind authentication)
- Verify the URL hasn't expired (Blob SAS tokens last 7 days)

---

## 📁 KEY FILES

| File | Purpose |
|------|---------|
| `LEON_Spec_Custom_Connector.json` v3.1.0 | Swagger 2.0 custom connector (IMPORT THIS) |
| `leon-api-openapi2.json` | OpenAPI 3.0.1 spec (for reference / URL-based approach) |
| `azure_function/function_app.py` | Azure Function entry point (endpoints) |
| `azure_function/azure_handler.py` | Azure Function handlers (validation logic) |
| `COPILOT_STUDIO_INTEGRATION_v4.md` | Previous guide (v4.1 — 2026-07-16) |
| `COPILOT_STUDIO_INTEGRATION_v5.md` | **This guide** (v5.0 — 2026-07-19) |

---

## 📡 ENDPOINTS

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `POST /api/upload-and-validate` | Anonymous | Upload + validate (JSON with fileName + fileContent string) |
| `POST /api/validate-url` | Anonymous | Validate from URL (JSON with fileUrl string) |
| `GET /api/upload-page` | Anonymous | HTML upload page (user uploads file → gets URL) |
| `POST /api/upload-and-get-url` | Anonymous | Upload to Blob Storage → returns { fileUrl, fileName, fileSize } |
| `POST /api/upload-and-validate-pdf` | Function key | Upload + validate + return raw PDF |
| `GET /api/health` | Anonymous | Health check |
| `POST /api/debug-request` | Anonymous | Echo request details (diagnostic) |

---

## ✅ VERIFICATION CHECKLIST

- [ ] Connector imported as v3.1.0 (fileContent = type: string)
- [ ] Connection created (Test tab → + New connection)
- [ ] Topic has Question node (FilePrebuiltEntity, skip allowed)
- [ ] Topic has Call a tool node (UploadAndValidate)
- [ ] `fileName` bound to `=First(System.Activity.Attachments).Name`
- [ ] `fileContent` bound to `=JSON(First(System.Activity.Attachments).Content, JSONFormat.IncludeBinaryData)`
- [ ] Inputs set to "From a variable" (NOT "Remplissage dynamique avec l'IA")
- [ ] Message node displays verdict, score, answer, documentUrl
- [ ] Test pane: trigger phrase → attach file → verdict returned
- [ ] **NOT PUBLISHED** (per user request)