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
    # Flex Consumption may not have /tmp — try multiple locations
    _tmp_uploads = None
    for _candidate in [
        Path("/tmp/data/uploads"),
        Path(os.environ.get("TMPDIR", "/tmp")) / "data" / "uploads",
        _FUNCTION_HOME / "data" / "uploads",
    ]:
        try:
            _candidate.mkdir(parents=True, exist_ok=True)
            _tmp_uploads = _candidate
            break
        except (OSError, PermissionError):
            continue
    if _tmp_uploads is None:
        # Last resort: use the function home directory
        _tmp_uploads = _FUNCTION_HOME / "data" / "uploads"
        try:
            _tmp_uploads.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass  # Non-fatal — uploads will fail but Q&A still works
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

    # ── Azure Blob Storage (for durable file uploads) ─────────────
    cfg.AZURE_STORAGE_CONNECTION_STRING = os.getenv(
        "AZURE_STORAGE_CONNECTION_STRING", ""
    )
    cfg.AZURE_STORAGE_CONTAINER = os.getenv(
        "AZURE_STORAGE_CONTAINER", "leon-uploads"
    )

    # ── Key Vault (optional — for managed identity secret access) ─
    cfg.AZURE_KEY_VAULT_URL = os.getenv("AZURE_KEY_VAULT_URL", "")

    # ── Application Insights (optional — for custom telemetry) ────
    cfg.APPINSIGHTS_CONNECTION_STRING = os.getenv(
        "APPINSIGHTS_CONNECTION_STRING",
        os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    )

    # ── Try loading secrets from Key Vault if configured ──────────
    _try_keyvault_secrets(cfg)

    print(f"[azure_config] DATA_DIR={cfg.DATA_DIR}")
    print(f"[azure_config] UPLOADS_DIR={cfg.UPLOADS_DIR}")
    print(f"[azure_config] REFS_DIR={cfg.REFS_DIR}")
    print(f"[azure_config] OpenAI endpoint configured: {bool(cfg.AZURE_OPENAI_ENDPOINT)}")
    print(f"[azure_config] Azure Search configured: {bool(cfg.AZURE_SEARCH_ENDPOINT)}")
    if cfg.AZURE_SEARCH_ENDPOINT:
        print(f"[azure_config]   Search endpoint: {cfg.AZURE_SEARCH_ENDPOINT}")
        print(f"[azure_config]   Search index: {cfg.AZURE_SEARCH_INDEX_NAME}")
    print(f"[azure_config] Blob Storage configured: {bool(cfg.AZURE_STORAGE_CONNECTION_STRING)}")
    print(f"[azure_config] Key Vault configured: {bool(cfg.AZURE_KEY_VAULT_URL)}")
    print(f"[azure_config] App Insights configured: {bool(cfg.APPINSIGHTS_CONNECTION_STRING)}")


def _try_keyvault_secrets(cfg):
    """
    Attempt to load secrets from Azure Key Vault using Managed Identity.

    If AZURE_KEY_VAULT_URL is set and azure-identity + azure-keyvault-secrets
    are installed, this overrides config values with Key Vault secrets.

    Secret names expected in Key Vault:
      - azure-openai-api-key
      - azure-search-api-key
      - azure-storage-connection-string
    """
    if not cfg.AZURE_KEY_VAULT_URL:
        return

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential()
        client = SecretClient(
            vault_url=cfg.AZURE_KEY_VAULT_URL,
            credential=credential,
        )

        # Override API keys from Key Vault if they exist
        try:
            cfg.AZURE_OPENAI_API_KEY = client.get_secret("azure-openai-api-key").value
            print("[azure_config] Loaded AZURE_OPENAI_API_KEY from Key Vault")
        except Exception:
            pass  # Secret not found — keep env var value

        try:
            cfg.AZURE_SEARCH_API_KEY = client.get_secret("azure-search-api-key").value
            print("[azure_config] Loaded AZURE_SEARCH_API_KEY from Key Vault")
        except Exception:
            pass

        try:
            cfg.AZURE_STORAGE_CONNECTION_STRING = client.get_secret(
                "azure-storage-connection-string"
            ).value
            print("[azure_config] Loaded AZURE_STORAGE_CONNECTION_STRING from Key Vault")
        except Exception:
            pass

    except ImportError:
        print("[azure_config] Key Vault URL set but azure-identity/keyvault not installed — using env vars")
    except Exception as exc:
        print(f"[azure_config] Key Vault access failed: {exc} — using env vars")


# Auto-configure when this module is imported in Azure
if os.environ.get("FUNCTIONS_WORKER_RUNTIME") == "python":
    try:
        configure_for_azure()
        print("[azure_config] Auto-configured for Azure Functions")
    except Exception as exc:
        # NEVER let config crash the entire function app — log and continue
        import traceback
        print(f"[azure_config] WARNING: configure_for_azure failed: {exc}")
        traceback.print_exc()
