"""
Azure Function configuration adapter.

Overrides app/config.py settings for the Azure Functions environment.
In Azure Functions:
- DATA_DIR should point to a location in the function's file system
- UPLOADS_DIR should be writable (use /tmp in Consumption plan)
- Environment variables come from Azure Function App Settings

Usage:
  from azure_config import configure_for_azure
  configure_for_azure()
  # Now app.config points to the right Azure locations
"""
import os
import sys
from pathlib import Path

# ── Azure Function base directory ──────────────────────────────────
# In Azure Functions, the function app runs from the deployed package.
# The function's home directory contains our code.
_FUNCTION_HOME = Path(os.environ.get(
    "AzureWebJobsScriptRoot",
    Path(__file__).resolve().parent
))


def configure_for_azure():
    """
    Override app.config paths for Azure Functions environment.

    - Static reference files (template, guide): read from function package
    - Dynamic uploads: store in /tmp (writable in Consumption plan)
    - Azure OpenAI creds: from Function App Settings
    """
    import app.config as cfg

    # ── Paths ──────────────────────────────────────────────────────
    # Static data (references) lives in the deployed package
    cfg.DATA_DIR = _FUNCTION_HOME / "data"
    cfg.REFS_DIR = cfg.DATA_DIR / "refs"
    cfg.INDEX_PATH = cfg.DATA_DIR / "reference_index.json"

    # Uploads go to a writable temp location
    _tmp_uploads = Path("/tmp/data/uploads")
    _tmp_uploads.mkdir(parents=True, exist_ok=True)
    cfg.UPLOADS_DIR = _tmp_uploads

    # ── Azure OpenAI (from Function App Settings) ──────────────────
    cfg.AZURE_OPENAI_ENDPOINT = os.getenv(
        "AZURE_OPENAI_ENDPOINT", cfg.AZURE_OPENAI_ENDPOINT
    )
    cfg.AZURE_OPENAI_API_KEY = (
        os.getenv("AZURE_OPENAI_API_KEY")
        or os.getenv("AZURE_OPENAI_KEY")  # fallback: Azure Portal default name
        or cfg.AZURE_OPENAI_API_KEY
    )
    cfg.AZURE_OPENAI_LLM_DEPLOYMENT = os.getenv(
        "AZURE_OPENAI_LLM_DEPLOYMENT", cfg.AZURE_OPENAI_LLM_DEPLOYMENT
    )
    cfg.AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.getenv(
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", cfg.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    )

    # ── Azure AI Search (from Function App Settings) ──────────────
    cfg.AZURE_SEARCH_ENDPOINT = os.getenv(
        "AZURE_SEARCH_ENDPOINT", cfg.AZURE_SEARCH_ENDPOINT
    )
    cfg.AZURE_SEARCH_API_KEY = os.getenv(
        "AZURE_SEARCH_API_KEY", cfg.AZURE_SEARCH_API_KEY
    )
    cfg.AZURE_SEARCH_INDEX_NAME = os.getenv(
        "AZURE_SEARCH_INDEX_NAME", cfg.AZURE_SEARCH_INDEX_NAME
    )

    # ── API Key for Copilot Studio auth ────────────────────────────
    cfg.API_KEY = os.getenv("API_KEY", "")

    print(f"[azure_config] DATA_DIR={cfg.DATA_DIR}")
    print(f"[azure_config] UPLOADS_DIR={cfg.UPLOADS_DIR}")
    print(f"[azure_config] REFS_DIR={cfg.REFS_DIR}")
    print(f"[azure_config] OpenAI endpoint configured: {bool(cfg.AZURE_OPENAI_ENDPOINT)}")
    print(f"[azure_config] Azure Search configured: {bool(cfg.AZURE_SEARCH_ENDPOINT)}")
    if cfg.AZURE_SEARCH_ENDPOINT:
        print(f"[azure_config]   Search endpoint: {cfg.AZURE_SEARCH_ENDPOINT}")
        print(f"[azure_config]   Search index: {cfg.AZURE_SEARCH_INDEX_NAME}")


# Auto-configure when this module is imported in Azure
if os.environ.get("FUNCTIONS_WORKER_RUNTIME") == "python":
    configure_for_azure()
    print("[azure_config] Auto-configured for Azure Functions")
