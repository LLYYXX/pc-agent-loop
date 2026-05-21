"""Constants for Local Semantic Overlay v3."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
MEMORY_DIR = PACKAGE_DIR.parent
REPO_ROOT = MEMORY_DIR.parent
DB_PATH = PACKAGE_DIR / "local_semantic_overlay.sqlite"
GLOBAL_MEM_PATH = MEMORY_DIR / "global_mem.txt"

DEFAULT_TEXT_HEAD_CHARS = 2400
DEFAULT_LEAF_BUDGET = 200
DEFAULT_CANDIDATE_LEAF_BUDGET = 200
DEFAULT_ANNOTATION_BUDGET = 30
DEFAULT_BUNDLE_BUDGET = 6
DEFAULT_BUNDLE_CANDIDATE_CAP = 14
DEFAULT_BUNDLE_KEY_EVIDENCE_CAP = 6
DEFAULT_BUNDLE_TEXT_HEAD_CHARS = 1200
DEFAULT_TAGGING_PACKET_SIZE = 6
DEFAULT_RECALL_LIMIT = 8
DEFAULT_RECENT_DAYS = 14
DEFAULT_LONG_MAINTAINED_MIN_SPAN_DAYS = 30
DEFAULT_AGG_RATIO_GATE = 0.15
DEFAULT_PROPAGATION_DECAY = 0.5
DEFAULT_PROPAGATION_MAX_DEPTH = 2
DEFAULT_OVERVIEW_BRIEF_MAX_CHARS = 400
BULK_TAGGING_LEAF_THRESHOLD = 12
MIN_EVIDENCE_NOTE_CHARS = 12

# Directory names skipped during scans (includes Unity build folders).
SCAN_IGNORE_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", "target", ".cache", ".next", ".nuxt", "$RECYCLE.BIN",
    "System Volume Information", "Library", "Temp", "Logs", ".vs", ".idea",
    "obj", "bin", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "coverage", ".terraform", ".gradle",
}
SCAN_IGNORE_DIRS_LOWER = {d.lower() for d in SCAN_IGNORE_DIRS}

# Path-part ignores for leaf classification (exclude "Temp" — matches Windows temp paths).
PATH_IGNORE_DIRS = {d for d in SCAN_IGNORE_DIRS if d not in {"Temp", "Logs", "Library"}}
PATH_IGNORE_DIRS_LOWER = {d.lower() for d in PATH_IGNORE_DIRS}

HARD_IGNORE_DIRS = SCAN_IGNORE_DIRS
HARD_IGNORE_DIRS_LOWER = SCAN_IGNORE_DIRS_LOWER

GENERIC_TAGS = frozenset({
    "project", "document", "research", "code", "file", "folder", "misc",
    "general", "data", "archive", "files", "folders", "directories",
    "documents", "projects", "pdf", "ppt", "doc",
})

TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg",
    ".csv", ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".sh", ".bat", ".ps1",
    ".lua", ".r", ".xml",
}

OFFICE_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".odt", ".ods", ".odp"}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
    ".mp3", ".mp4", ".avi", ".mkv", ".wav", ".flac",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".pkl", ".pt", ".pth",
    ".onnx", ".wasm",
}

MARKER_NAMES = {
    "README", "README.md", "readme.md", "index.md", "package.json", "pyproject.toml",
    "requirements.txt", "setup.py", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "CMakeLists.txt", "docker-compose.yml", "Dockerfile",
}

ORG_SUBDIR_NAMES = {
    "src", "tests", "test", "docs", "doc", "examples", "example", "lib", "libs",
    "config", "configs", "resources", "assets", "data", "raw", "processed",
    "results", "figures", "logs", "scripts", "bin", "plugins",
}

SHALLOW_CONTAINER_NAMES = {
    "documents", "downloads", "desktop", "workspaces", "workspace",
}

READABLE_STATUSES = {"readable", "extract_failed", "binary", "skipped_noise"}
SEMANTIC_STATUSES = {"seed", "tagged", "deferred", "confirmed", "rejected"}
NODE_TYPES = {"container", "organizational", "semantic_node", "directory_macro"}

ALLOWED_SEED_SOURCES = frozenset({
    "recent",
    "long_maintained",
    "user_confirmed",
    "fallback_found",
    "key_evidence",
})

SCHEDULING_SEED_SOURCE_ORDER = (
    "fallback_found",
    "user_confirmed",
    "key_evidence",
    "long_maintained",
    "recent",
)

KEY_EVIDENCE_EVIDENCE_TYPES = frozenset({
    "readme", "manifest", "pdf_head", "office_head",
})
TAG_TYPES = {"semantic", "organizational", "state", "task"}
EDGE_KINDS = {"aggregated_semantic", "organizational", "inferred_context"}
PROPAGATION_STATUSES = {"local", "candidate"}
