"""Path/runtime shim for running LEON as a Databricks App.

Databricks Apps expose a single dynamic port (``DATABRICKS_APP_PORT``) and an
ephemeral filesystem. This module is imported *before* any ``app.*`` module
builds state. It:

- keeps the read-only bundled reference documents (``data/refs``) resolving from
  the deployed app copy (absolute paths), and
- redirects every *writable* path (uploaded files, the generated embeddings
  index) to a writable, ephemeral working directory under ``/tmp`` so the
  several endpoints that write to a relative ``data/uploads`` path keep working
  even if the deployed app directory is read-only.

Uploads are intentionally ephemeral (lost on container recycle), matching the
chosen "no external storage" deployment.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
WORKDIR = Path(os.environ.get("LEON_WORKDIR", "/tmp/leon"))


def configure_for_databricks():
    """Point writable paths at an ephemeral workdir; keep refs in the app copy."""
    uploads = WORKDIR / "data" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    # Make relative "data/uploads" / "data" writes (used by several conformity
    # and validation routes) land in the writable ephemeral workdir instead of
    # the deployed (possibly read-only) app directory.
    os.chdir(WORKDIR)

    import app.config as cfg

    # Reference documents stay in the deployed copy (absolute, read-only).
    cfg.DATA_DIR = APP_DIR / "data"
    cfg.REFS_DIR = cfg.DATA_DIR / "refs"

    # Writable, ephemeral locations.
    cfg.UPLOADS_DIR = uploads
    cfg.INDEX_PATH = WORKDIR / "reference_index.json"

    return cfg


# Configure immediately on import.
configure_for_databricks()
