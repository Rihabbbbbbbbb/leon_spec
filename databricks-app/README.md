# LEON on Databricks Apps

Self-contained copy of LEON packaged to run as a **Databricks App** so enterprise
users get workspace SSO and the UI directly inside the Databricks workspace.

> This folder is an independent copy. The original project at the repository root
> is unchanged. Edits here do not affect it.

## What runs

A single FastAPI process (`main.py`) exposes:

| Path | Surface |
| --- | --- |
| `/` | Conformity Matrix Analyzer + Spec Validation UI |
| `/qa` | Spec Q&A Assistant UI |
| `/api/*` | REST API (`ask`, `validate`, `upload`, `files`, `conformity`, `conformity-excel`, `conformity-batch`, `conformity-compare`, `conformity-powerbi`, `spec-to-matrix`, ...) |
| `/health` | Health check |

## Design decisions baked in

- **Auth**: none in the app — Databricks Apps enforce workspace SSO + `CAN_USE` on the app. Grant your enterprise users/groups `CAN_USE` after deploy.
- **LLM + embeddings**: continues to call **Azure OpenAI** (`gpt-4o`, `text-embedding-3-large`) over egress.
- **Retrieval**: continues to use **Azure AI Search** (`leon-specs-index`) over egress. If either Azure service is unreachable, LEON degrades to local keyword retrieval + grounded excerpts (no crash).
- **Uploads**: written to an **ephemeral** `/tmp` workdir (see `databricks_config.py`). Files do not persist across restarts — every flow is upload → analyze → download.
- **Port/host**: binds `0.0.0.0:${DATABRICKS_APP_PORT}` (required by the platform).

## Prerequisites

1. Databricks CLI ≥ 0.294.0 installed and authenticated to this workspace:
   ```bash
   databricks auth login --host https://adb-5678659344564033.13.azuredatabricks.net --profile leon
   ```
2. A secret scope named `leon` with the two Azure keys:
   ```bash
   databricks secrets create-scope leon --profile leon
   databricks secrets put-secret leon azure-openai-api-key --profile leon   # paste the Azure OpenAI key
   databricks secrets put-secret leon azure-search-api-key --profile leon   # paste the Azure AI Search key
   ```
   (The deploying user needs `MANAGE` on the scope so the app SP can be granted `READ`.)
3. If the workspace has **egress restrictions**, allowlist the Azure OpenAI + Azure AI Search hosts (and `pypi.org`, `files.pythonhosted.org` for install), then restart the app after changing egress.

## Deploy (DABs)

From this `databricks-app/` directory:

```bash
databricks bundle validate --profile leon
databricks apps deploy leon-spec -t dev --profile leon   # deploys AND starts; returns the app URL
databricks apps get leon-spec --profile leon             # check app_status.state == RUNNING and the URL
```

Then in the workspace UI, grant your users/groups `CAN_USE` on the `leon-spec` app.

## Local smoke test

```bash
pip install -r requirements.txt
DATABRICKS_APP_PORT=8000 python main.py
# in another shell:
curl -s localhost:8000/health
curl -s localhost:8000/api/files
```

## Notes / limits

- Databricks Apps enforce a **120-second** per-request proxy timeout. Very large
  LLM validations or big `conformity-batch` requests may need to be split.
- Endpoints/keys are configured via `app.yaml` (UI deploys) and the `config:`
  block in `databricks.yml` (DABs). Edit the non-secret endpoint values there if
  your Azure resources differ.
- Adjust the workspace `host` in `databricks.yml` / your CLI profile if deploying
  elsewhere.
