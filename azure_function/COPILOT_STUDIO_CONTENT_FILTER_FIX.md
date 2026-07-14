# Copilot Studio Content Filter Fix — `openAIndirectAttack`

## Problem
When a user attaches a DOCX specification file in Copilot Studio, the bot returns:
```
The content was filtered due to Responsible AI restrictions.
Error code: ContentFiltered.
openAIndirectAttack
```

## Root Cause
Copilot Studio's orchestration layer sends ALL conversation content (including file attachments) through Azure OpenAI's content filter. Technical specification documents contain:
- "Shall" statements (look like commands/prompts)
- Instructions and procedures
- Structured tables with requirements
- Engineering directives

Azure OpenAI's **Indirect Attack (Prompt Injection)** filter interprets these as potential prompt injection attacks and blocks the entire flow **before** the Azure Function is even called.

## Solution: URL-Based Validation (Bypasses Content Filter)

Instead of passing file **content** through Copilot Studio (which triggers the filter), pass only a **URL** string. The Azure Function downloads and validates the file server-side.

### New Endpoint
```
POST /api/validate-url
Body: { "fileUrl": "https://sharepoint.com/.../spec.docx", "fileName": "spec.docx" }
Returns: { answer, verdict, overallScore, validationReport, documentBase64, documentUrl, ... }
```

### Copilot Studio Configuration Changes

#### Option A: Use SharePoint/OneDrive URL (Recommended)

1. **Modify the topic** to ask for a SharePoint/OneDrive link instead of a file attachment:
   - Replace `FilePrebuiltEntity` with a text input
   - Prompt: "Please share the SharePoint or OneDrive link to your specification file."

2. **Update the action input**:
   - Change `file: =Topic.Var1.Content` to `fileUrl: =Topic.Var1` (the text input)
   - Update the action to call `/api/validate-url` instead of `/api/upload-and-validate`

3. **Update output bindings**:
   ```
   answer: Topic.answer
   fileName: Topic.fileName
   overallScore: Topic.overallScore
   status: Topic.status
   summary: Topic.summary
   validationReport: Topic.validationReport
   verdict: Topic.verdict
   documentUrl: Topic.documentUrl
   documentAvailable: Topic.documentAvailable
   ```

#### Option B: Two-Step Flow (Upload + Validate)

1. **Step 1**: Use Power Automate to upload the file to Azure Blob Storage
   - Power Automate → "Create file" in SharePoint/Blob Storage → Get file URL
   - This bypasses the content filter because Power Automate doesn't use Azure OpenAI

2. **Step 2**: Call `/api/validate-url` with the Blob Storage URL
   - Copilot Studio passes only the short URL string
   - Azure Function downloads and validates the file

#### Option C: Use File Name (Pre-Uploaded)

If the file is already uploaded via `/api/upload`:
1. Ask the user for the file name (text input, not file attachment)
2. Call `/api/validate` with `{ "fileName": "spec.docx" }`
3. The file content never goes through the content filter

### Copilot Studio Topic YAML (Option A)
```yaml
- kind: Question
  id: AskUrl
  variable: init:Topic.fileUrl
  prompt: Please share the SharePoint or OneDrive link to your specification file.
  entity:
    kind: TextPrebuiltEntity

- kind: BeginDialog
  id: CallValidateUrl
  input:
    binding:
      fileUrl: =Topic.fileUrl
  dialog: cr927_leon.action.ValidateUrl
  output:
    binding:
      answer: Topic.answer
      fileName: Topic.fileName
      overallScore: Topic.overallScore
      status: Topic.status
      summary: Topic.summary
      validationReport: Topic.validationReport
      verdict: Topic.verdict
      documentUrl: Topic.documentUrl
      documentAvailable: Topic.documentAvailable
```

### Copilot Studio Action Configuration (Option A)
- **Name**: Validate Spec from URL
- **Description**: Download a specification file from a URL and validate it against the CTS template
- **Input**: `fileUrl` (text, required)
- **Endpoint**: `POST /api/validate-url`
- **After execution**: Send a response (show the answer + documentUrl)

### Response Handling
After the action returns, the bot should:
1. Show the `answer` text (verdict + summary)
2. Provide a download link using `documentUrl` (the unified DOCX document)
3. If `documentAvailable` is true, say: "Your validation report is ready for download."

### Why This Works
- **URL strings are short** and don't contain technical specification text
- **Azure OpenAI's content filter** only sees the URL, not the file content
- **The Azure Function** downloads the file server-side, completely bypassing Copilot Studio's content filter
- **The validation pipeline** is 100% deterministic (no LLM), so no content filter is triggered during validation

### Alternative: Adjust Azure OpenAI Content Filter
If you have access to the Azure OpenAI resource:
1. Go to Azure Portal → Azure OpenAI → Content filters
2. Create a new content filter policy
3. Set "Prompt indirect attack" to a lower severity or disable it
4. Apply the policy to the GPT-4o deployment used by Copilot Studio

Note: This may not be possible in a corporate environment with strict AI safety policies.