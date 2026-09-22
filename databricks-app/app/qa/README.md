# Spec Q&A Assistant

A minimal web-based Q&A assistant that answers questions **only** from accessible
engineering specification files. It never uses BeStandard or standards repositories,
never invents details, and clearly says when support is not found.

## Architecture (modular)

```
app/
├── qa/
│   ├── __init__.py
│   ├── retrieval.py   # file indexing + chunking + keyword/semantic retrieval
│   ├── prompt.py       # strict prompt template (answer only from retrieved content)
│   ├── mock_data.py    # example mock data so the UI runs before real retrieval
│   └── route.py        # FastAPI router: POST /api/ask, GET /api/files
├── qa_ui/
│   └── index.html      # React chat UI (CDN, no build step)
└── qa_server.py        # standalone FastAPI app serving UI + API
```

Separation of concerns:
- **UI** — `app/qa_ui/index.html` (React chat interface)
- **API route** — `app/qa/route.py`
- **Retrieval logic** — `app/qa/retrieval.py`
- **Prompt logic** — `app/qa/prompt.py`

## Output contract

```json
{
  "answer": "string",
  "sources": [{ "fileName": "string", "excerpt": "string" }]
}
```

## Guardrails

- Never answers from general model knowledge if retrieval has no support.
- Never claims standard compliance without an accessible source.
- Never hides uncertainty — prefers "not found" over guessing.
- Sources are always visible in the UI.
- Questions about standards / BeStandard get a fixed refusal message.

## Running

```bash
# from the project root
.venv\Scripts\python.exe -m app.qa_server
# then open http://localhost:8010
```

The UI starts in **mock mode** (checkbox on by default) so it works without Azure
OpenAI credentials. Uncheck "Mock mode" to use real retrieval + the LLM.

## API

### POST /api/ask
```json
// request
{ "question": "What is the purpose of the ASU spec?", "useMock": true }

// response
{
  "answer": "According to [spec_extracted.txt] (PURPOSE): ...",
  "sources": [{ "fileName": "spec_extracted.txt", "excerpt": "..." }]
}
```

### GET /api/files
Returns the list of accessible spec file names.
