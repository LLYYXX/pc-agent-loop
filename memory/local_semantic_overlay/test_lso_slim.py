"""LSO Slim structure tests."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from local_semantic_overlay import (
    apply_tags, begin_build, build_overview, discover_seeds, finish_build,
    finish_file_query, load, prepare_bundle, query_map, read_leaf, run_file_query,
    save, system_overview,
)
from local_semantic_overlay.read import looks_like_raw_dump, sanitize_display
from local_semantic_overlay import store


def test_raw_dump_gate():
    assert looks_like_raw_dump("PK\x03\x04[Content_Types].xml")
    assert not sanitize_display("PK\x03\x04 junk")


def test_normal_text_passes():
    t = "# Hello\n\nThis is a normal readme with enough readable content here."
    assert not looks_like_raw_dump(t)
    assert sanitize_display(t).startswith("#")


def test_read_leaf_binary(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG")
    r = read_leaf(str(p))
    assert r["read_status"] == "binary"


def test_primary_not_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OVERLAYS", tmp_path / "ov")
    (tmp_path / "readme.md").write_text("seed file for primary resolution test with enough text.")
    data = begin_build(str(tmp_path), reset=True)["data"]
    discover_seeds(data)
    b = prepare_bundle(data)
    assert b
    r = apply_tags(data, b, {"leaf_annotations": [], "defer_leaf_ids": []})
    assert not r["ok"] and r["error"] == "primary_not_resolved"


def test_candidate_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OVERLAYS", tmp_path / "ov")
    data = begin_build(str(tmp_path), reset=True)["data"]
    f = tmp_path / "readme.md"
    f.write_text("alpha beta gamma delta project readme content here for evidence.")
    discover_seeds(data)
    b = prepare_bundle(data)
    pid = b["primary"]["leaf_id"]
    r = apply_tags(data, b, {
        "leaf_annotations": [{"leaf_id": pid, "tags": [{"tag": "开题材料", "evidence_note": "readme mentions project scope and goals"}]}],
        "defer_leaf_ids": [],
    })
    assert r["ok"]


def test_generic_tag_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OVERLAYS", tmp_path / "ov")
    data = begin_build(str(tmp_path), reset=True)["data"]
    f = tmp_path / "readme.md"
    f.write_text("content for tagging test with enough readable text in file.")
    discover_seeds(data)
    b = prepare_bundle(data)
    pid = b["primary"]["leaf_id"]
    r = apply_tags(data, b, {
        "leaf_annotations": [{"leaf_id": pid, "tags": [{"tag": "file", "evidence_note": "this is a generic bad tag note"}]}],
        "defer_leaf_ids": [],
    })
    assert not r["ok"] and r["error"] == "generic_tag"


def test_bundle_sets_disjoint(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OVERLAYS", tmp_path / "ov")
    data = begin_build(str(tmp_path), reset=True)["data"]
    (tmp_path / "a.md").write_text("aaa " * 20)
    (tmp_path / "b.md").write_text("bbb " * 20)
    discover_seeds(data)
    b = prepare_bundle(data)
    ids = {b["primary"]["leaf_id"]}
    ids.update(c["leaf_id"] for c in b["candidates"])
    ids.update(k["leaf_id"] for k in b["key_evidence"])
    assert len(ids) == 1 + len(b["candidates"]) + len(b["key_evidence"])


def test_overview_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OVERLAYS", tmp_path / "ov")
    data = begin_build(str(tmp_path), reset=True)["data"]
    (tmp_path / "readme.md").write_text("overview test content with sufficient readable evidence text.")
    discover_seeds(data)
    b = prepare_bundle(data)
    pid = b["primary"]["leaf_id"]
    apply_tags(data, b, {
        "leaf_annotations": [{"leaf_id": pid, "tags": [{"tag": "测试主题", "evidence_note": "readme body describes test topic clearly"}]}],
        "defer_leaf_ids": [],
    })
    o = build_overview(data)
    assert o["ok"] and o.get("partial") is True
    rep = finish_build(data)
    assert rep["partial_report"]["status"] == "partial"


def test_query_map_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OVERLAYS", tmp_path / "ov")
    scope = str(tmp_path)
    data = begin_build(scope, reset=True)["data"]
    (tmp_path / "readme.md").write_text("quant screening notebook content here.")
    discover_seeds(data)
    b = prepare_bundle(data)
    apply_tags(data, b, {
        "leaf_annotations": [{"leaf_id": b["primary"]["leaf_id"], "tags": [
            {"tag": "量化筛选", "evidence_note": "mentions quant screening in readme body"}
        ]}],
        "defer_leaf_ids": [],
    })
    build_overview(data)
    q = query_map("量化", scope=scope)
    assert q["ok"] and (q["semantic_hits"] or q["leaf_hits"])


def test_fallback_not_disguised(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OVERLAYS", tmp_path / "ov")
    scope = str(tmp_path)
    r = run_file_query("zzz_nonexistent_query_xyz", scope=scope, limit=10)
    assert r["ok"]
    if r.get("fallback_used"):
        for h in r.get("fallback_hits") or []:
            assert h.get("source") == "fallback"
        for h in r.get("semantic_hits") or []:
            assert h.get("source") != "fallback"


def test_feedback_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OVERLAYS", tmp_path / "ov")
    scope = str(tmp_path)
    p = tmp_path / "found_target.md"
    p.write_text("found target file with readable evidence for feedback loop testing.")
    r = finish_file_query("q", scope=scope, found=[str(p)])
    assert r["ok"] and r.get("added_seeds", 0) >= 1
    data = load(scope)
    assert any(l.get("source") == "fallback_found" for l in data["leaves"].values())


def test_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OVERLAYS", tmp_path / "ov")
    scope = str(tmp_path / "scope_dir")
    Path(scope).mkdir()
    d = begin_build(scope, reset=True)["data"]
    save(d)
    d2 = load(scope)
    assert d2["meta"]["scope"] == store.norm_path(scope)


def test_total_lines_under_1000():
    root = Path(__file__).resolve().parent
    total = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in root.glob("*.py") if not p.name.startswith("test_"))
    assert total < 1000
