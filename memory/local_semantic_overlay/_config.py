"""Mechanical config — environment hygiene, not semantic rules.

Shared constants for read / search / store / build / runtime.
Nothing here may generate tags, nodes, or semantic judgments.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# --- path utilities ---

def norm_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(str(path).strip().strip('"').strip("'")))


def path_eq(a: str, b: str) -> bool:
    if os.name == "nt":
        return norm_path(a).lower() == norm_path(b).lower()
    return norm_path(a) == norm_path(b)


def anchor_of(path: str) -> str:
    return norm_path(os.path.dirname(path))


# --- evidence extraction ---

HEAD_MAX = 1600
MIN_READABLE_CHARS = 10

# --- file classification ---

IGNORE_DIRS = frozenset({
    ".git", ".svn", ".hg", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", "target", ".cache", ".next", "$RECYCLE.BIN",
    "System Volume Information",
})
IGNORE_DIRS_LOWER = frozenset(x.lower() for x in IGNORE_DIRS)

TEXT_EXT = frozenset({
    ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".xml",
})

CODE_EXT = frozenset({".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs"})

OFFICE_EXT = frozenset({".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"})

BINARY_EXT = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
    ".mp3", ".mp4", ".avi", ".mkv", ".wav", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".pkl", ".wasm",
})

MARKER_NAMES = frozenset({
    "README", "README.md", "readme.md", "index.md", "package.json", "pyproject.toml",
    "requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "Dockerfile",
})
MARKER_NAMES_LOWER = frozenset(x.lower() for x in MARKER_NAMES)

RAW_ARTIFACT_RE = re.compile(
    r"PK\x03\x04|\[Content_Types\]\.xml|_rels/|theme/theme|themeManager\.xml|"
    r"xl/workbook\.xml|Root Entry|SummaryInformation|WordDocument|%PDF-1\.",
    re.I,
)

# --- search adapter ---

GLOBAL_MEM_PATH = Path(__file__).resolve().parent.parent / "global_mem.txt"
DEFAULT_TIMEOUT = 30.0
PROBE_TIMEOUT = 8.0
ES_ENV_KEYS = ("EVERYTHING_ES_EXE", "GA_ES_EXE", "FILE_INDEX_ES_EXE")
