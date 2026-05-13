"""Configuration for Local Semantic Overlay."""

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
DEFAULT_SURVEY_MAX_CLUSTERS = 240
DEFAULT_SURVEY_MAX_DIRS = 2500
DEFAULT_TEXT_HEAD_CHARS = 2400

GENERIC_ROUTE_TAGS = {
    "file",
    "files",
    "folder",
    "folders",
    "directory",
    "directories",
    "document",
    "documents",
    "project",
    "projects",
    "misc",
    "general",
    "archive",
    "code",
    "python-code",
    "web-code",
    "frontend-code",
    "backend-code",
    "java-code",
    "cpp-code",
    "data-files",
    "presentations",
}

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
    "$RECYCLE.BIN",
    "System Volume Information",
}

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

TEXT_EVIDENCE_EXTENSIONS = {
    ".md",
    ".txt",
    ".rst",
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".csv",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
}

REPRESENTATIVE_DOC_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".md",
    ".txt",
}
