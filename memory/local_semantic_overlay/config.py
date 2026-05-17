"""Configuration for Local Semantic Overlay v2 (Area-Aware Annotation-First)."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
MEMORY_DIR = PACKAGE_DIR.parent
REPO_ROOT = MEMORY_DIR.parent
DB_PATH = PACKAGE_DIR / "local_semantic_overlay.sqlite"
GLOBAL_MEM_PATH = MEMORY_DIR / "global_mem.txt"

MAX_ACTIVE_ROUTES = 24
MAX_WARM_ROUTES = 160
MAX_ROUTES = 1000

ACTIVE_STALE_DAYS = 30
WARM_STALE_DAYS = 180

DEFAULT_RECALL_LIMIT = 8
DEFAULT_EVIDENCE_LIMIT = 12
DEFAULT_SEED_ROUTE_BUDGET = 40
DEFAULT_SURVEY_MAX_DEPTH = 2
DEFAULT_EVIDENCE_BUDGET = 400
DEFAULT_PACKET_SIZE = 6
DEFAULT_TEXT_HEAD_CHARS = 2400

HARD_IGNORE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    ".cache",
    ".next",
    ".nuxt",
    "$RECYCLE.BIN",
    "System Volume Information",
    "Library",      # Unity Library
    "Temp",         # Unity Temp
    "Logs",         # Unity Logs
    ".vs",
    ".idea",
    "obj",
    "bin",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
    ".terraform",
    ".gradle",
}

HARD_IGNORE_DIRS_LOWER = {d.lower() for d in HARD_IGNORE_DIRS}

GENERIC_TAGS = frozenset({
    "project",
    "document",
    "research",
    "code",
    "file",
    "folder",
    "misc",
    "general",
    "data",
    "archive",
    "files",
    "folders",
    "directories",
    "documents",
    "projects",
})

EVIDENCE_BUCKETS = (
    "manifest",
    "readme_or_index",
    "office_pdf",
    "recent",
    "long_maintained",
    "entrypoint_like",
    "deep_representative",
    "diversity",
)

MARKER_FILE_NAMES = {
    "README",
    "README.md",
    "readme.md",
    "index.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "CMakeLists.txt",
}

ENTRYPOINT_NAMES = {
    "main.py",
    "app.py",
    "server.py",
    "index.js",
    "index.ts",
    "main.go",
    "main.rs",
    "Main.java",
    "Program.cs",
    "docker-compose.yml",
    "Dockerfile",
    "Makefile",
}

TEXT_EVIDENCE_EXTENSIONS = {
    ".md", ".txt", ".rst", ".toml", ".json", ".yaml", ".yml",
    ".ini", ".cfg", ".csv", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".rb", ".php", ".sh", ".bat", ".ps1", ".lua", ".r",
}

OFFICE_PDF_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".xls", ".xlsx", ".odt", ".ods", ".odp",
}

REPRESENTATIVE_DOC_EXTENSIONS = OFFICE_PDF_EXTENSIONS | {".md", ".txt"}

ANNOTATION_DECISIONS = {"annotate", "needs_more_evidence", "defer", "ignore_noise"}

AREA_STATUSES = {
    "unseen", "profiled", "needs_more_evidence",
    "deferred", "ignored_noise", "covered", "out_of_scope",
}

ROUTE_STATUSES = {"active", "candidate", "deferred"}
ROUTE_TIERS = {"active", "warm", "cold"}
