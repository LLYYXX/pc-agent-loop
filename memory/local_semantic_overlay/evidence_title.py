"""Rule-based evidence titles for overview entries. No directory names, no raw dumps."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Filename → kind label (checked before generic evidence_type)
_FILENAME_KIND: dict[str, str] = {
    "package.json": "Package manifest",
    "pyproject.toml": "Python project",
    "requirements.txt": "Python dependencies",
    "setup.py": "Python package setup",
    "cargo.toml": "Rust crate",
    "go.mod": "Go module",
    "pom.xml": "Maven project",
    "build.gradle": "Gradle project",
    "cmakelists.txt": "CMake project",
    "dockerfile": "Docker image",
    "docker-compose.yml": "Docker compose",
    "docker-compose.yaml": "Docker compose",
    "readme.md": "README",
    "readme": "README",
    "index.md": "Documentation index",
}

# evidence_type → kind when filename is not specific enough
_EVIDENCE_TYPE_KIND: dict[str, str] = {
    "readme": "README",
    "manifest": "Package manifest",
    "pdf_head": "PDF document",
    "office_head": "Office document",
    "code_head": "Source file",
    "text_head": "Text file",
}

_SUFFIX_KIND: dict[str, str] = {
    ".md": "README",
    ".rst": "Documentation",
    ".pdf": "PDF document",
    ".docx": "Word document",
    ".doc": "Word document",
    ".pptx": "Presentation",
    ".ppt": "Presentation",
    ".xlsx": "Excel data sheet",
    ".xls": "Excel data sheet",
    ".csv": "CSV data",
    ".toml": "Config manifest",
    ".yaml": "Config manifest",
    ".yml": "Config manifest",
    ".json": "JSON manifest",
}


def _kind_label(leaf: dict[str, Any]) -> str:
    path = Path(leaf.get("path") or "")
    name_key = path.name.lower()
    if name_key in _FILENAME_KIND:
        return _FILENAME_KIND[name_key]
    et = (leaf.get("evidence_type") or "").lower()
    if et in _EVIDENCE_TYPE_KIND:
        return _EVIDENCE_TYPE_KIND[et]
    suf = path.suffix.lower()
    if suf in _SUFFIX_KIND:
        return _SUFFIX_KIND[suf]
    if name_key.startswith("readme"):
        return "README"
    return "Key file"


def _clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^#+\s*", "", line)
    line = re.sub(r"^[-*]\s+", "", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


_OOXML_DUMP_RE = re.compile(
    r"PK\x03\x04|\[Content_Types\]\.xml|_rels/|xl/workbook\.xml|theme/theme",
    re.IGNORECASE,
)


def looks_like_raw_dump(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if _OOXML_DUMP_RE.search(t[:500]):
        return True
    if t.startswith("{") and "}" in t[:500]:
        return True
    if t.startswith("[") and "]" in t[:300]:
        return True
    if t.startswith("<?xml") or "<w:" in t[:80]:
        return True
    if len(t) > 120 and t.count('"') > 8:
        return True
    return False


def sanitize_display_text(text: str) -> str:
    """Drop raw office/binary artifact text; return stripped safe excerpt or empty."""
    t = (text or "").strip()
    if not t or looks_like_raw_dump(t):
        return ""
    return t


def _looks_like_raw_dump(text: str) -> bool:
    return looks_like_raw_dump(text)


_FRAMEWORK_HINT_KEYS = (
    "torch", "pytorch", "tensorflow", "react", "vue", "angular", "next",
    "express", "django", "flask", "fastapi", "pandas", "numpy", "scikit-learn",
)


def _framework_hints_from_deps(data: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(key)
        if isinstance(deps, dict):
            for dep_name in deps:
                low = dep_name.lower().split("/")[-1]
                if low in _FRAMEWORK_HINT_KEYS and low not in hints:
                    hints.append(low)
        if len(hints) >= 2:
            break
    return hints[:2]


def _subject_from_json(text: str, filename: str) -> str | None:
    try:
        data = json.loads(text[:8000])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    desc = data.get("description")
    hints = _framework_hints_from_deps(data)
    hint_str = f" ({', '.join(hints)})" if hints else ""
    if isinstance(name, str) and name.strip():
        subj = name.strip()
        if isinstance(desc, str) and desc.strip() and len(desc) < 60:
            return f"{subj} — {desc.strip()}{hint_str}"[:80]
        return f"{subj}{hint_str}"[:80]
    if isinstance(desc, str) and desc.strip():
        return f"{desc.strip()}{hint_str}"[:80]
    project = data.get("project", {})
    if isinstance(project, dict):
        pname = project.get("name")
        if isinstance(pname, str) and pname.strip():
            return f"{pname.strip()}{hint_str}"[:80]
    if hints:
        return ", ".join(hints)[:80]
    return None


def _subject_from_pom(text: str) -> str | None:
    aid = re.search(r"<artifactId>\s*([^<]+)\s*</artifactId>", text[:12000], re.I)
    name = re.search(r"<name>\s*([^<]+)\s*</name>", text[:12000], re.I)
    parts: list[str] = []
    if aid:
        parts.append(aid.group(1).strip())
    if name and (not parts or name.group(1).strip() != parts[0]):
        parts.append(name.group(1).strip()[:40])
    dep_ids = re.findall(r"<artifactId>\s*([^<]+)\s*</artifactId>", text[:12000], re.I)
    for did in dep_ids[1:4]:
        low = did.strip().lower()
        if low in _FRAMEWORK_HINT_KEYS and low not in [p.lower() for p in parts]:
            parts.append(low)
            if len(parts) >= 3:
                break
    if parts:
        return " / ".join(parts)[:80]
    return None


def _subject_from_xml_config(text: str) -> str | None:
    if _looks_like_raw_dump(text[:200]):
        m = re.search(r"<(\w[\w:-]*)", text[:500])
        if m:
            root = m.group(1).split(":")[-1]
            attrs = re.findall(r'\b([a-zA-Z_][\w-]*)\s*=', text[:400])[:2]
            if attrs:
                return f"{root} ({', '.join(attrs)})"[:80]
            return root[:80]
        return None
    return _subject_from_lines(text)


def _subject_from_yaml_toml(text: str) -> str | None:
    keys: list[str] = []
    for raw in text.splitlines()[:20]:
        m = re.match(r"^([a-zA-Z_][\w.-]*)\s*[:=]", raw.strip())
        if m:
            k = m.group(1)
            if k not in keys and k not in ("true", "false", "null"):
                keys.append(k)
        if len(keys) >= 2:
            break
    if keys:
        return ", ".join(keys)[:80]
    return _subject_from_lines(text)


def _subject_from_spreadsheet(text: str) -> str | None:
    tokens: list[str] = []
    for raw in text.split()[:40]:
        w = raw.strip().strip(",;")
        if not w or len(w) < 2 or _looks_like_raw_dump(w):
            continue
        if w.lower() in tokens:
            continue
        tokens.append(w[:30])
        if len(tokens) >= 4:
            break
    if tokens:
        return ", ".join(tokens)[:80]
    return _subject_from_lines(text)


def _subject_from_pyproject(text: str) -> str | None:
    m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', text[:4000])
    if m:
        return m.group(1).strip()
    m = re.search(r'description\s*=\s*["\']([^"\']+)["\']', text[:4000])
    if m and len(m.group(1)) < 80:
        return m.group(1).strip()
    return None


def _subject_from_lines(text: str) -> str | None:
    for raw in text.splitlines()[:12]:
        line = _clean_line(raw)
        if not line or len(line) < 4:
            continue
        if _looks_like_raw_dump(line):
            continue
        if line.lower().startswith(("import ", "from ", "const ", "function ", "class ", "def ", "#!")):
            continue
        return line[:80]
    return None


def extract_evidence_subject(leaf: dict[str, Any]) -> str | None:
    """Human-readable subject from leaf content — never the parent directory name."""
    text = (leaf.get("text_head") or "").strip()
    if not text:
        note = (leaf.get("evidence_note") or "").strip()
        if note and not note.startswith("key_evidence:") and not _looks_like_raw_dump(note):
            return note[:80]
        return None

    path = Path(leaf.get("path") or "")
    name = path.name.lower()

    if name.endswith(".json") or name == "package.json":
        subj = _subject_from_json(text, name)
        if subj and not _looks_like_raw_dump(subj):
            return subj[:80]

    if name == "pom.xml":
        subj = _subject_from_pom(text)
        if subj:
            return subj[:80]

    if name in ("pyproject.toml",) or path.suffix.lower() == ".toml":
        subj = _subject_from_pyproject(text) or _subject_from_yaml_toml(text)
        if subj:
            return subj[:80]

    if path.suffix.lower() in {".yaml", ".yml", ".ini", ".cfg"}:
        subj = _subject_from_yaml_toml(text)
        if subj:
            return subj[:80]

    if path.suffix.lower() == ".xml":
        subj = _subject_from_xml_config(text)
        if subj:
            return subj[:80]

    if name.startswith("readme") or path.suffix.lower() in {".md", ".rst"}:
        subj = _subject_from_lines(text)
        if subj:
            return subj[:80]

    if path.suffix.lower() in {".xlsx", ".xls", ".csv"}:
        subj = _subject_from_spreadsheet(text)
        if subj:
            return subj[:80]

    if path.suffix.lower() in {".pdf", ".docx", ".doc", ".pptx", ".ppt"}:
        subj = _subject_from_lines(text)
        if subj:
            return subj[:80]

    subj = _subject_from_lines(text)
    if subj:
        return subj[:80]
    return None


def build_evidence_title(leaf: dict[str, Any]) -> str:
    kind = _kind_label(leaf)
    subject = extract_evidence_subject(leaf)
    if subject and not _looks_like_raw_dump(subject):
        return f"{kind}: {subject}"
    return kind


_LEAF_PRIORITY = (
    "readme",
    "manifest",
    "pdf_head",
    "office_head",
    "code_head",
    "text_head",
)


def pick_primary_evidence_leaf(leaves: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not leaves:
        return None

    def score(leaf: dict[str, Any]) -> tuple[int, int]:
        et = (leaf.get("evidence_type") or "").lower()
        try:
            pri = _LEAF_PRIORITY.index(et)
        except ValueError:
            pri = len(_LEAF_PRIORITY)
        has_text = 1 if (leaf.get("text_head") or "").strip() else 0
        return (pri, -has_text)

    return min(leaves, key=score)


def build_node_overview_title(
    evidence_leaves: list[dict[str, Any]],
    tag_labels: list[str] | None = None,
) -> str:
    primary = pick_primary_evidence_leaf(evidence_leaves)
    if primary:
        return build_evidence_title(primary)
    if tag_labels:
        return f"Tagged area: {', '.join(tag_labels[:3])}"
    return "Evidence-backed area"
