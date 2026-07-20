# LEON Spec Validator — Copilot Studio Integration Guide (v4.1 — 2026-07-16)

**Function URL:** `https://leon-spec-gbexcnefdmakfpdg.francecentral-01.azurewebsites.net`
**Auth:** Anonymous (no API key required)
**Status:** ✅ Production-ready, deployed and verified

---

## ⚠️ CRITICAL: `Topic.Var1.ContentUrl` DOES NOT WORK

**`FilePrebuiltEntity` in Copilot Studio returns a BLOB type.** The dot operator (`.`) is FORBIDDEN on Blob types in Power Fx. These ALL fail:

| Expression | Error |
|-----------|-------|
| `=Topic.Var1.ContentUrl` | ❌ "ContentUrl n'est pas reconnu" / "dot operator cannot be used on Blob values" |
| `=Topic.Var1.Name` | ❌ Same — dot operator forbidden on Blob |
| `=Topic.Var1.Content` | ❌ Same — dot operator forbidden on Blob |
| `=Text(Topic.Var1)` | ❌ "BlobToText not implemented" |

**The working approach uses `System.Activity.Attachments` (NOT `Topic.Var1`).** `System.Activity.Attachments` is a system collection that IS NOT a Blob type — the dot operator works on it.

---

## Working Setup (Direct Tool Call — NO Power Automate needed)

### Step 1: Import Custom Connector

Import `LEON_Spec_Custom_Connector.json` into Power Platform as a custom connector.

### Step 2: Create Copilot Studio Topic

Create a new topic with trigger phrases ("validate this spec", "check this CTS", etc.)

### Step 3: Call the Tool

Add a **"Call a tool"** node → select **LEON Spec Validator** → select **UploadAndValidate** action.

Set inputs to **"From a variable"** (NOT "Remplissage dynamique avec l'IA"):

| Input | Value |
|-------|-------|
| `fileName` | `=First(System.Activity.Attachments).Name` |
| `fileContent` | `=JSON(First(System.Activity.Attachments).Content, JSONFormat.IncludeBinaryData)` |

> ⚠️ **No FilePrebuiltEntity question node needed!** The user just attaches a file with their message. `System.Activity.Attachments` automatically contains all conversation attachments.

### Step 4: Display Result

```
📋 Validation Result
Verdict: {Tool.UploadAndValidate.verdict}
Score: {Tool.UploadAndValidate.overallScore}
{Tool.UploadAndValidate.answer}
📄 Download: {Tool.UploadAndValidate.documentUrl}
```

### Step 5: Publish and Test

Click Publish → test in Teams or Copilot Studio test pane.

---

## Why This Works (Technical Details)

1. `System.Activity.Attachments` is a **collection** (not a Blob) → `.Name` and `.Content` work
2. `JSON(..., IncludeBinaryData)` converts the FileDataType to `{"$content-type":"...","$content":"UEsDBBQ..."}` → a **StringDataType** (no type mismatch with connector)
3. The JSON string passes through Copilot Studio as text → **no RAI content filter trigger**
4. Azure Function `_decode_file_content` Case 8 parses the JSON, extracts `$content`, decodes base64 → file bytes

---

## Alternate Approaches

### If using Power Automate (Flow):
- Flow trigger: FileContent (File type) ← handles Blob natively
- Compose: `base64(triggerBody()?['FileContent'])`
- HTTP POST to `/api/upload-and-validate` with `{fileName, fileContent}`

### If user pastes a URL (no file upload):
- Copilot Studio uses Text entity → `=Topic.Var1` is a string URL
- Call `/api/validate-url` with `{fileUrl: "=Topic.Var1"}`

---

## Bindings Reference

| Input | ✅ WORKING Binding | ❌ NON-WORKING Binding |
|-------|-------------------|----------------------|
| `fileName` | `=First(System.Activity.Attachments).Name` | `=Topic.Var1.Name` |
| `fileContent` | `=JSON(First(System.Activity.Attachments).Content, JSONFormat.IncludeBinaryData)` | `=Topic.Var1.ContentUrl` |
| `fileUrl` (text) | `=Topic.Var1` (Text entity only) | `=Topic.Var1.ContentUrl` (Blob entity) |

---

## Response Format

```json
{
  "answer": "Verdict: GOOD (83%): The specification covers 18 of 22 mandatory sections...",
  "status": "answered",
  "verdict": "GOOD",
  "overallScore": 0.83,
  "fileName": "spec.docx",
  "validationReport": { ... },
  "documentUrl": "https://storage1leonspec.blob.core.windows.net/...",
  "documentAvailable": true
}
```

### Verdicts
| Verdict | Score |
|---------|-------|
| GOOD | ≥ 80%, 0 errors |
| ACCEPTABLE_WITH_FIXES | ≥ 60%, ≤ 2 errors |
| NOT_RELIABLE | ≥ 35% |
| NON_COMPLIANT | < 35% |

---

## Verified (2026-07-16)

- ✅ Upload page: 200 (anonymous)
- ✅ Validate-url error handling: 422 (correct)
- ✅ Full upload → SAS URL → validate: 200, GOOD, 83%, document available
- ✅ All 6 production bugs fixed (500, 422, 502 errors eliminated)
