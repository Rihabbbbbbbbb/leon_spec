"""
beStandard API Client for LEON Spec Validator
═══════════════════════════════════════════════════

Integrates with the Stellantis beStandard platform (bestandard.fcagroup.com)
to resolve external standard references found in component specifications.

API Reference:
  - POST api/Login          → Authenticate, get access_token
  - GET  api/Norms/Searchs  → Search standards by code, title, type, status
  - GET  api/Norms?id=      → Full norm details (metadata, cross-refs, files)
  - GET  api/Norms/Files    → Download standard file content (chunked)

USAGE PATTERN (HIGH-LEVEL):
  1. LEON detects "[STA20]" in a requirement
  2. bestandard_client.search_by_code("STA20") → finds norm ID
  3. bestandard_client.get_norm_detail(norm_id) → title, revision, status, docsRef
  4. LEON enriches validation: "STA20 = Acoustic Vehicle Alerting System (rev 3, PUBLISHED)"
  5. OPTIONAL: bestandard_client.download_file(file_id) → ingest into dynamic RAG index
  6. LLM can now verify: "STA20 §4.2 requires 105-118 dB. Your value: 100 dB → NON-COMPLIANT"

CACHE STRATEGY:
  - Norm metadata cached for 1 hour (standards don't change frequently)
  - Token cached until expiry (expires_in from login response)
  - File content NOT cached locally (always fetch fresh for compliance)
"""

import time
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from urllib.parse import urlencode

import requests

from app.config import (
    BESTANDARD_BASE_URL,
    BESTANDARD_CLIENT_ID,
    BESTANDARD_CLIENT_SECRET,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class NormInfo:
    """Lightweight norm/standard metadata (from search results)."""
    id: str
    code: str
    title: str
    revision: str = ""
    status: str = ""  # DRAFT, PUBLISHED, CANCELLED, etc.
    publication_date: str = ""
    review_date: str = ""
    last_modification_date: str = ""


@dataclass
class NormFile:
    """A file attached to a norm (translation, original, etc.)."""
    id: str
    file_name: str
    ext: str  # "pdf", "docx", etc.
    is_published: bool = False
    is_original: bool = False


@dataclass
class Translation:
    """A language translation of a norm with its attached files."""
    lang: str
    title: str
    files: List[NormFile] = field(default_factory=list)


@dataclass
class NormDetail:
    """Full norm details (from GET api/Norms?id=)."""
    id: str
    code: str
    title: str
    title_it: str = ""
    title_fr: str = ""
    revision: str = ""
    status: str = ""
    publication_date: str = ""
    review_date: str = ""
    last_modification_date: str = ""
    author_dept: str = ""
    coauthor_dept: str = ""
    class_fca: str = ""
    doc_type_fca: str = ""
    send_to_supplier: bool = False
    is_global: bool = False
    is_cluster: bool = False
    is_harmonized: bool = False
    is_international: bool = False
    is_cancelled: bool = False
    is_lesson_learned: bool = False
    docs_in_use: List[NormInfo] = field(default_factory=list)
    docs_ref: List[NormInfo] = field(default_factory=list)
    url_bst: str = ""
    translations: List[Translation] = field(default_factory=list)

    @property
    def all_files(self) -> List[NormFile]:
        """Flatten all files across all translations."""
        result = []
        for t in self.translations:
            result.extend(t.files)
        return result

    @property
    def published_files(self) -> List[NormFile]:
        """Only published files (downloadable)."""
        return [f for f in self.all_files if f.is_published]

    @property
    def is_active(self) -> bool:
        """A standard is 'active' if not cancelled and published."""
        return not self.is_cancelled and self.status.upper() in ("PUBLISHED", "PUBLIÉ", "APPROVED")


@dataclass
class FileChunk:
    """A chunk of a downloaded file."""
    file_size: int = 0
    chunk_size: int = 0
    idx_chunk: int = 0
    has_next: bool = False
    content: bytes = b''  # Base64-decoded binary content


@dataclass
class ResolvedStandard:
    """The result of resolving an external reference like [STA20]."""
    code: str                          # The original code, e.g. "STA20"
    norm: Optional[NormDetail] = None  # Full norm detail if found
    found: bool = False                # Was the standard found in beStandard?
    error: str = ""                    # Error message if lookup failed
    verification_status: str = ""      # "active", "cancelled", "draft", "unknown"


# ═══════════════════════════════════════════════════════════════════
# CLIENT
# ═══════════════════════════════════════════════════════════════════

class BeStandardClient:
    """
    Authenticated client for the Stellantis beStandard API.

    Handles:
      - OAuth2 token acquisition and automatic refresh
      - Searching standards by code, title, type, status
      - Fetching full norm details with cross-references
      - Downloading standard file content (chunked)

    Thread-safe: token is cached per-instance; use one instance per app lifecycle.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        self.base_url = (base_url or BESTANDARD_BASE_URL).rstrip('/')
        self.client_id = client_id or BESTANDARD_CLIENT_ID
        self.client_secret = client_secret or BESTANDARD_CLIENT_SECRET

        # Token cache
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0  # epoch timestamp

        # Metadata cache: code → NormDetail (1-hour TTL)
        self._detail_cache: Dict[str, Tuple[NormDetail, float]] = {}
        self._detail_cache_ttl: float = 3600.0  # 1 hour

        # HTTP session (connection reuse)
        self._session: Optional[requests.Session] = None

    @property
    def is_configured(self) -> bool:
        """Check if beStandard credentials are available."""
        return bool(self.client_id and self.client_secret)

    # ── Session management ──────────────────────────────────────

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "Accept": "application/json",
                "User-Agent": "LEON-SpecValidator/2.0 (Stellantis)",
            })
        return self._session

    # ── Authentication ──────────────────────────────────────────

    def _is_token_valid(self) -> bool:
        """Check if the cached token is still valid (with 60s buffer)."""
        return (
            self._access_token is not None
            and time.time() < (self._token_expires_at - 60)
        )

    def login(self) -> str:
        """
        Authenticate with beStandard and cache the access token.

        Returns:
            The access_token string.

        Raises:
            RuntimeError: If authentication fails.
        """
        if not self.is_configured:
            raise RuntimeError(
                "beStandard credentials not configured. "
                "Set BESTANDARD_CLIENT_ID and BESTANDARD_CLIENT_SECRET in .env"
            )

        url = f"{self.base_url}/api/Login"
        params = {
            "clientId": self.client_id,
            "secret": self.client_secret,
        }

        try:
            resp = self._get_session().get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise RuntimeError(f"beStandard login failed: {e}")

        self._access_token = data.get("access_token", "")
        expires_in = data.get("expires_in", 3600)
        self._token_expires_at = time.time() + float(expires_in)

        if not self._access_token:
            raise RuntimeError("beStandard login returned empty access_token")

        logger.info(
            "beStandard: authenticated (token expires in %ss)",
            expires_in
        )
        return self._access_token

    def _ensure_authenticated(self) -> str:
        """Get a valid token, logging in if necessary."""
        if not self._is_token_valid():
            return self.login()
        return self._access_token

    # ── API calls ───────────────────────────────────────────────

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict:
        """
        Authenticated GET to beStandard API.

        Args:
            path: API path (e.g., "/api/Norms/Searchs")
            params: Query parameters dict

        Returns:
            Parsed JSON response

        Raises:
            RuntimeError: On HTTP or auth errors
        """
        token = self._ensure_authenticated()
        url = f"{self.base_url}{path}"

        headers = {"Authorization": f"Bearer {token}"}
        # Flatten list params for ASP.NET model binding:
        # codes[0]=X&codes[1]=Y instead of codes=X&codes=Y
        flat_params = {}
        if params:
            for key, value in params.items():
                if isinstance(value, list):
                    for i, item in enumerate(value):
                        flat_params[f"{key}[{i}]"] = str(item)
                else:
                    flat_params[key] = value

        try:
            resp = self._get_session().get(
                url, params=flat_params, headers=headers, timeout=60
            )
            # If 401, token may have expired early — retry once
            if resp.status_code == 401:
                logger.info("beStandard: token expired, re-authenticating...")
                self.login()
                headers["Authorization"] = f"Bearer {self._access_token}"
                resp = self._get_session().get(
                    url, params=flat_params, headers=headers, timeout=60
                )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise RuntimeError(f"beStandard API error ({path}): {e}")

    # ── Search ──────────────────────────────────────────────────

    def search(
        self,
        query: str = "",
        codes: Optional[List[str]] = None,
        document_types: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
        last_update: Optional[str] = None,
    ) -> List[NormInfo]:
        """
        Search beStandard for norms/standards.

        Args:
            query: Generic search query (title, keywords)
            codes: Filter by standard codes, e.g. ["STA20", "N41"]
            document_types: Filter by document type
            statuses: Filter by status ("PUBLISHED", "DRAFT", "CANCELLED")
            last_update: Filter by last update date (ISO format)

        Returns:
            List of NormInfo matching the search criteria
        """
        if not self.is_configured:
            return []

        params: Dict[str, Any] = {"q": query}
        if codes:
            params["codes"] = codes
        if document_types:
            params["documentTypes"] = document_types
        if statuses:
            params["status"] = statuses
        if last_update:
            params["lastUpdate"] = last_update

        try:
            data = self._get("/api/Norms/Searchs", params)
        except RuntimeError as e:
            logger.warning("beStandard search failed: %s", e)
            return []

        if not isinstance(data, list):
            return []

        results = []
        for item in data:
            results.append(NormInfo(
                id=str(item.get("id", "")),
                code=str(item.get("code", "")),
                title=str(item.get("title", "")),
                revision=str(item.get("revision", "")),
                status=str(item.get("status", "")),
                publication_date=str(item.get("pubblicationDate", "")),
                review_date=str(item.get("reviewDate", "")),
                last_modification_date=str(item.get("lastModificationDate", "")),
            ))
        return results

    def search_by_code(self, code: str) -> List[NormInfo]:
        """
        Find a standard by its exact code (e.g., "STA20", "N41").

        This is the PRIMARY method for resolving [STA20]-style references
        found in component specifications.

        Args:
            code: The standard code to look up

        Returns:
            List of matching norms (usually 0 or 1)
        """
        return self.search(codes=[code])

    # ── Detail ──────────────────────────────────────────────────

    def get_norm_detail(self, norm_id: str, use_cache: bool = True) -> Optional[NormDetail]:
        """
        Get full details of a norm by its beStandard ID.

        Results are cached for 1 hour to avoid redundant API calls.

        Args:
            norm_id: The beStandard norm ID (from search results)
            use_cache: If True, use cached result if available

        Returns:
            NormDetail object, or None if not found
        """
        if not self.is_configured:
            return None

        # Check cache
        if use_cache and norm_id in self._detail_cache:
            detail, cached_at = self._detail_cache[norm_id]
            if time.time() - cached_at < self._detail_cache_ttl:
                return detail

        try:
            data = self._get("/api/Norms", {"id": norm_id})
        except RuntimeError as e:
            logger.warning("beStandard get_norm_detail(%s) failed: %s", norm_id, e)
            return None

        if not data or not isinstance(data, dict):
            return None

        # Parse translations and files
        translations = []
        for t_data in data.get("translations", []) or []:
            files = []
            for f_data in t_data.get("files", []) or []:
                files.append(NormFile(
                    id=str(f_data.get("id", "")),
                    file_name=str(f_data.get("fileName", "")),
                    ext=str(f_data.get("ext", "")),
                    is_published=bool(f_data.get("isPublished", False)),
                    is_original=bool(f_data.get("isOriginal", False)),
                ))
            translations.append(Translation(
                lang=str(t_data.get("lang", "")),
                title=str(t_data.get("title", "")),
                files=files,
            ))

        # Parse docsInUse / docsRef
        def _parse_norm_list(raw_list) -> List[NormInfo]:
            result = []
            for item in (raw_list or []):
                result.append(NormInfo(
                    id=str(item.get("id", "")),
                    code=str(item.get("code", "")),
                    title=str(item.get("title", "")),
                    revision=str(item.get("revision", "")),
                    status=str(item.get("status", "")),
                    publication_date=str(item.get("pubblicationDate", "")),
                    review_date=str(item.get("reviewDate", "")),
                    last_modification_date=str(item.get("lastModificationDate", "")),
                ))
            return result

        detail = NormDetail(
            id=str(data.get("id", "")),
            code=str(data.get("code", "")),
            title=str(data.get("title", "")),
            title_it=str(data.get("title_it", "")),
            title_fr=str(data.get("title_fr", "")),
            revision=str(data.get("revision", "")),
            status=str(data.get("status", "")),
            publication_date=str(data.get("pubblicationDate", "")),
            review_date=str(data.get("reviewDate", "")),
            last_modification_date=str(data.get("lastModificationDate", "")),
            author_dept=str(data.get("authorDept", "")),
            coauthor_dept=str(data.get("coauthorDept", "")),
            class_fca=str(data.get("classFCA", "")),
            doc_type_fca=str(data.get("docTypeFCA", "")),
            send_to_supplier=bool(data.get("sendToSupplier", False)),
            is_global=bool(data.get("global", False)),
            is_cluster=bool(data.get("cluster", False)),
            is_harmonized=bool(data.get("harmonized", False)),
            is_international=bool(data.get("international", False)),
            is_cancelled=bool(data.get("cancelled", False)),
            is_lesson_learned=bool(data.get("lessonLearned", False)),
            docs_in_use=_parse_norm_list(data.get("docsInUse")),
            docs_ref=_parse_norm_list(data.get("docsRef")),
            url_bst=str(data.get("urlBst", "")),
            translations=translations,
        )

        # Cache
        self._detail_cache[norm_id] = (detail, time.time())
        return detail

    # ── File download ───────────────────────────────────────────

    def download_file(self, file_id: str, max_chunks: int = 50) -> bytes:
        """
        Download a standard file (PDF, DOCX, etc.) in chunks and
        reassemble the complete binary content.

        Args:
            file_id: The file ID (from NormDetail.translations[].files[].id)
            max_chunks: Safety limit — maximum number of chunks to fetch

        Returns:
            Complete file content as bytes

        Raises:
            RuntimeError: If download fails
        """
        if not self.is_configured:
            raise RuntimeError("beStandard not configured")

        all_content = bytearray()
        idx_chunk = 0

        while idx_chunk < max_chunks:
            try:
                data = self._get("/api/Norms/Files", {
                    "idFile": file_id,
                    "idxChunk": idx_chunk,
                })
            except RuntimeError as e:
                raise RuntimeError(f"Failed to download file chunk {idx_chunk}: {e}")

            chunk_size = int(data.get("chunkSize", 0))
            has_next = bool(data.get("hasNext", False))
            content_b64 = data.get("content", "")

            if not content_b64:
                break

            # Decode base64 content
            import base64
            chunk_bytes = base64.b64decode(content_b64)
            all_content.extend(chunk_bytes)

            if not has_next or chunk_size == 0:
                break

            idx_chunk += 1

        logger.info(
            "beStandard: downloaded file %s — %d bytes in %d chunks",
            file_id, len(all_content), idx_chunk + 1
        )
        return bytes(all_content)

    def download_first_published_file(self, norm: NormDetail) -> Optional[bytes]:
        """
        Convenience: download the first published file attached to a norm.

        Args:
            norm: A NormDetail object

        Returns:
            File content as bytes, or None if no published files exist
        """
        published = norm.published_files
        if not published:
            logger.warning("No published files for norm %s (%s)", norm.code, norm.id)
            return None

        return self.download_file(published[0].id)

    # ── High-level resolution ───────────────────────────────────

    def resolve_standard(self, code: str) -> ResolvedStandard:
        """
        Resolve a standard code reference (e.g., "STA20") to its full details.

        This is the MAIN entry point for LEON's validation pipeline.
        When a requirement references [STA20], call this method to get
        the full norm metadata.

        Args:
            code: Standard code, e.g. "STA20", "N41", "ISO_26262"

        Returns:
            ResolvedStandard with full norm detail if found
        """
        if not self.is_configured:
            return ResolvedStandard(
                code=code,
                found=False,
                error="beStandard not configured",
                verification_status="unknown",
            )

        try:
            results = self.search_by_code(code)
        except Exception as e:
            return ResolvedStandard(
                code=code,
                found=False,
                error=str(e),
                verification_status="unknown",
            )

        if not results:
            return ResolvedStandard(
                code=code,
                found=False,
                error=f"Code '{code}' not found in beStandard",
                verification_status="unknown",
            )

        # Get the first match (exact code match preferred)
        best_match = results[0]
        for r in results:
            if r.code.upper() == code.upper():
                best_match = r
                break

        # Fetch full detail
        try:
            detail = self.get_norm_detail(best_match.id)
        except Exception as e:
            return ResolvedStandard(
                code=code,
                norm=None,
                found=True,
                error=f"Found code but failed to fetch detail: {e}",
                verification_status="found_no_detail",
            )

        if detail is None:
            return ResolvedStandard(
                code=code,
                found=True,
                error="Detail fetch returned None",
                verification_status="found_no_detail",
            )

        # Determine verification status
        if detail.is_cancelled:
            vstatus = "cancelled"
        elif detail.status.upper() in ("DRAFT", "DRAFTING", "IN_REVIEW"):
            vstatus = "draft"
        elif detail.is_active:
            vstatus = "active"
        else:
            vstatus = "unknown_status"

        return ResolvedStandard(
            code=code,
            norm=detail,
            found=True,
            verification_status=vstatus,
        )

    def resolve_multiple(self, codes: List[str]) -> Dict[str, ResolvedStandard]:
        """
        Resolve multiple standard codes at once.

        Args:
            codes: List of standard codes, e.g. ["STA20", "N41", "N42"]

        Returns:
            Dict mapping code → ResolvedStandard
        """
        results = {}
        for code in codes:
            results[code] = self.resolve_standard(code)
        return results


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

# Module-level singleton — created on first use
_client: Optional[BeStandardClient] = None


def get_bestandard_client() -> BeStandardClient:
    """Get or create the singleton beStandard client."""
    global _client
    if _client is None:
        _client = BeStandardClient()
    return _client


def reset_client():
    """Reset the singleton client (useful for testing)."""
    global _client
    _client = None
