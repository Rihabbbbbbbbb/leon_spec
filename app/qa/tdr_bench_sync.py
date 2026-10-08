"""Share TDR benchmark jobs across Azure Function instances.

The benchmark runs in one process, but Azure answers the next poll, the result
request, and a page click from whichever instance is free. Local `/tmp` is not
visible to the others, so those calls return "Unknown job" and evidence pages
come back empty. When `AzureWebJobsStorage` (or `AZURE_STORAGE_CONNECTION_STRING`)
is set, job state, the result, the extracted pages, and the original files are
copied to blob storage and read back on demand.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_JOB_ID = re.compile(r"[a-f0-9]{12}")
_CONTAINER = "aeris-tdr-bench"


def _safe_rel(relative: str) -> str:
    rel = (relative or "").replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError(f"Invalid job path: {relative}")
    return "/".join(parts)


def _safe_id(job_id: str) -> str:
    if not _JOB_ID.fullmatch(job_id or ""):
        raise ValueError(f"Invalid job id: {job_id}")
    return job_id


class DirectoryJobSync:
    """Test and single-host stand-in: one directory visible to every manager."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str, relative: str) -> Path:
        return self.root / _safe_id(job_id) / _safe_rel(relative)

    def push_bytes(self, job_id: str, relative: str, data: bytes) -> None:
        path = self._path(job_id, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def read_bytes(self, job_id: str, relative: str) -> bytes | None:
        path = self._path(job_id, relative)
        if not path.is_file():
            return None
        return path.read_bytes()

    def list_relative(self, job_id: str, prefix: str) -> list[str]:
        prefix_norm = _safe_rel(prefix.rstrip("/")) if prefix else ""
        base = self.root / _safe_id(job_id)
        if not base.is_dir():
            return []
        found = []
        for path in base.rglob("*"):
            if not path.is_file() or path.name.endswith(".tmp"):
                continue
            rel = path.relative_to(base).as_posix()
            if not prefix_norm or rel == prefix_norm or rel.startswith(prefix_norm + "/"):
                found.append(rel)
        return found

    def list_job_ids(self) -> list[str]:
        ids = []
        for child in self.root.iterdir():
            if child.is_dir() and _JOB_ID.fullmatch(child.name) and (child / "state.json").is_file():
                ids.append(child.name)
        return ids

    def delete_relative(self, job_id: str, relative: str) -> None:
        path = self._path(job_id, relative)
        path.unlink(missing_ok=True)

    def delete_job(self, job_id: str) -> None:
        shutil.rmtree(self.root / _safe_id(job_id), ignore_errors=True)


class BlobJobSync:
    """Azure Blob mirror. Connection is opened on first use."""

    def __init__(self, connection_string: str | None = None, container: str = _CONTAINER,
                 account_url: str | None = None):
        if not connection_string and not account_url:
            raise ValueError("Blob storage connection string or account URL is required")
        self.connection_string = connection_string
        self.account_url = account_url
        self.container_name = container
        self._container = None

    def _service(self):
        from azure.storage.blob import BlobServiceClient

        if self.connection_string:
            return BlobServiceClient.from_connection_string(self.connection_string)
        from azure.identity import DefaultAzureCredential
        return BlobServiceClient(account_url=self.account_url, credential=DefaultAzureCredential())

    def _client(self):
        if self._container is not None:
            return self._container
        from azure.core.exceptions import ResourceExistsError

        service = self._service()
        try:
            self._container = service.create_container(self.container_name)
        except ResourceExistsError:
            self._container = service.get_container_client(self.container_name)
        return self._container

    def _blob_name(self, job_id: str, relative: str) -> str:
        return f"jobs/{_safe_id(job_id)}/{_safe_rel(relative)}"

    def push_bytes(self, job_id: str, relative: str, data: bytes) -> None:
        blob = self._client().get_blob_client(self._blob_name(job_id, relative))
        blob.upload_blob(data, overwrite=True)

    def read_bytes(self, job_id: str, relative: str) -> bytes | None:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._client().get_blob_client(self._blob_name(job_id, relative))
        try:
            return blob.download_blob().readall()
        except ResourceNotFoundError:
            return None

    def list_relative(self, job_id: str, prefix: str) -> list[str]:
        job_id = _safe_id(job_id)
        prefix = _safe_rel(prefix).rstrip("/") if prefix else ""
        blob_prefix = f"jobs/{job_id}/{prefix}" if prefix else f"jobs/{job_id}/"
        root = f"jobs/{job_id}/"
        found = []
        for blob in self._client().list_blobs(name_starts_with=blob_prefix):
            name = blob.name
            if not name.startswith(root):
                continue
            rel = name[len(root):]
            if rel and not rel.endswith("/"):
                found.append(rel)
        return found

    def list_job_ids(self) -> list[str]:
        ids = set()
        for blob in self._client().list_blobs(name_starts_with="jobs/"):
            parts = blob.name.split("/")
            if len(parts) >= 3 and parts[2] == "state.json" and _JOB_ID.fullmatch(parts[1]):
                ids.add(parts[1])
        return sorted(ids)

    def delete_relative(self, job_id: str, relative: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._client().get_blob_client(self._blob_name(job_id, relative))
        try:
            blob.delete_blob()
        except ResourceNotFoundError:
            return

    def delete_job(self, job_id: str) -> None:
        client = self._client()
        prefix = f"jobs/{_safe_id(job_id)}/"
        for blob in list(client.list_blobs(name_starts_with=prefix)):
            client.delete_blob(blob.name)


def blob_sync_from_env() -> BlobJobSync | None:
    """Return a blob mirror when Azure storage is configured, else None."""
    container = (os.getenv("TDR_BENCH_BLOB_CONTAINER") or _CONTAINER).strip() or _CONTAINER
    conn = (os.getenv("AZURE_STORAGE_CONNECTION_STRING") or os.getenv("AzureWebJobsStorage") or "").strip()
    account = (os.getenv("AzureWebJobsStorage__accountName") or os.getenv("AZURE_STORAGE_ACCOUNT") or "").strip()
    try:
        if conn and not conn.lower().startswith("usedevelopmentstorage"):
            sync = BlobJobSync(connection_string=conn, container=container)
        elif account:
            sync = BlobJobSync(account_url=f"https://{account}.blob.core.windows.net", container=container)
        else:
            return None
        # Touch the container now so a bad credential fails while the manager
        # starts, not halfway through a supplier upload.
        sync._client()
        logger.info("TDR benchmark jobs will be shared in blob container %s", container)
        return sync
    except Exception:
        logger.warning("TDR benchmark blob sharing is unavailable; jobs stay on this instance only", exc_info=True)
        return None
