"""
Configuration centralisée pour le projet Leon Spec Validator.
Charge les variables d'environnement depuis .env et expose les constantes.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Charger .env depuis la racine du projet
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# --- Azure OpenAI ---
AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_LLM_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_LLM_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

# --- Projet ---
PROJECT_ENDPOINT: str = os.getenv("PROJECT_ENDPOINT", "")

# --- Chemins ---
ROOT_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = ROOT_DIR / "data"
REFS_DIR: Path = DATA_DIR / "refs"
UPLOADS_DIR: Path = DATA_DIR / "uploads"
INDEX_PATH: Path = DATA_DIR / "reference_index.json"

# --- Chunking ---
DEFAULT_CHUNK_SIZE: int = 5          # Nombre de paragraphes par chunk (stratégie paragraphs)
DEFAULT_CHUNK_STRATEGY: str = "section_aware"  # Stratégie par défaut: paragraphs, sections, section_aware
MAX_CHUNK_TOKENS: int = 6000         # Limite tokens par chunk (sécurité)

# --- Azure AI Search ---
# Hybrid vector+full-text search (replaces local TF-IDF keyword retrieval).
# Set these to enable production-grade semantic search with no cold-start penalty.
# Leave empty to keep using local keyword retrieval as fallback.
AZURE_SEARCH_ENDPOINT: str = os.getenv("AZURE_SEARCH_ENDPOINT", "")
AZURE_SEARCH_API_KEY: str = os.getenv("AZURE_SEARCH_API_KEY", "")
AZURE_SEARCH_INDEX_NAME: str = os.getenv("AZURE_SEARCH_INDEX_NAME", "leon-specs-index")
AZURE_SEARCH_SEMANTIC_CONFIG: str = os.getenv("AZURE_SEARCH_SEMANTIC_CONFIG", "default")

# --- Embedding ---
EMBEDDING_MODEL_MAX_INPUT: int = 8191  # Tokens max par input pour text-embedding-3-large

# --- Validation ---
SIMILARITY_THRESHOLD: float = 0.75   # Seuil cosine similarity pour matching pertinent
TOP_K_CHUNKS: int = 5                # Nombre de chunks référents à récupérer

# --- beStandard Integration ---
# Stellantis corporate standards platform (bestandard.fcagroup.com)
# Used to resolve external standard references like [STA20], [N41], [N42]
# found in component specifications.
BESTANDARD_BASE_URL: str = os.getenv(
    "BESTANDARD_BASE_URL",
    "https://bestandard.fcagroup.com"
)
BESTANDARD_CLIENT_ID: str = os.getenv("BESTANDARD_CLIENT_ID", "")
BESTANDARD_CLIENT_SECRET: str = os.getenv("BESTANDARD_CLIENT_SECRET", "")
# If True, automatically resolve standard references during validation
BESTANDARD_AUTO_RESOLVE: bool = os.getenv("BESTANDARD_AUTO_RESOLVE", "true").lower() == "true"
# If True, download and ingest standard content into a dynamic RAG index
# (enables deep content verification against actual standard text)
BESTANDARD_DEEP_VERIFY: bool = os.getenv("BESTANDARD_DEEP_VERIFY", "false").lower() == "true"
