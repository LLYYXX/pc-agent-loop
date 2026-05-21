"""Aggregate leaf_tag_edges into directory_tag_edges and classify directory_nodes. No LLM, no runtime."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from . import store
from .config import (
    DEFAULT_AGG_RATIO_GATE,
    DEFAULT_PROPAGATION_DECAY,
    DEFAULT_PROPAGATION_MAX_DEPTH,
    MARKER_NAMES,
    ORG_SUBDIR_NAMES,
    SHALLOW_CONTAINER_NAMES,
)
from .directory_macro import compress_directories_from_key_evidence, node_has_key_evidence_compression
from .leaf_seed import _detect_org_signals


def _org_signals_for_dir(dir_path: Path) -> Any:
    existing = store.get_directory_node_by_path(dir_path)
    if existing:
        org = existing.get("org_signals")
        if isinstance(org, dict) and (
            node_has_key_evidence_compression(existing)
            or org.get("bundle_compression_basis")
            or org.get("bundle_coverage_boundary")
        ):
            return org
    return _detect_org_signals(dir_path) if dir_path.is_dir() else []


def _org_signal_present(org_signals: Any) -> bool:
    if isinstance(org_signals, dict):
        return bool(org_signals.get("organization_signals") or org_signals.get("key_evidence_leaf_ids"))
    return bool(org_signals)


def _ensure_node_chain(dir_path: Path) -> dict[str, Any]:
    parts: list[Path] = []
    p = dir_path.resolve()
    chain: list[Path] = []
    while True:
        chain.append(p)
        if p.parent == p:
            break
        p = p.parent
    chain.reverse()
    parent_id = None
    last_node = None
    for i, part in enumerate(chain):
        org = _org_signals_for_dir(part) if part.is_dir() else []
        node = store.upsert_directory_node(
            part,
            parent_node_id=parent_id,
            depth=i,
            org_signals=org,
        )
        parent_id = node["node_id"]
        last_node = node
    return last_node or store.upsert_directory_node(dir_path, depth=0)


def _is_representative_leaf(leaf: dict[str, Any]) -> bool:
    if int(leaf.get("confirm_count") or 0) > 0:
        return True
    p = Path(leaf["path"])
    if p.name in MARKER_NAMES or p.name.lower().startswith("readme") or p.name == "index.md":
        return True
    return False


def _local_gate(support: int, ratio: float, org_signals: Any, has_repr: bool) -> bool:
    has_org = _org_signal_present(org_signals)
    if support >= 2:
        return True
    if support == 1 and has_repr:
        return True
    if support == 1 and has_org and has_repr:
        return True
    if support >= 1 and has_org and ratio >= DEFAULT_AGG_RATIO_GATE:
        return True
    return False


def aggregate_directory_tags(session_id: str | None = None) -> dict[str, Any]:
    """Rebuild directory nodes and edges from leaf_tag_edges."""
    macro_result = compress_directories_from_key_evidence(session_id)

    leaves = store.list_leaves(semantic_status="tagged", limit=10000)
    if session_id:
        leaves = [l for l in leaves if l.get("session_id") == session_id]

    # Group tagged leaves by parent directory
    by_dir: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for leaf in leaves:
        parent = leaf.get("parent_directory_path") or str(Path(leaf["path"]).parent)
        by_dir[parent].append(leaf)

    # Ensure all directory nodes exist
    dir_nodes: dict[str, dict[str, Any]] = {}
    for dir_path in by_dir:
        node = _ensure_node_chain(Path(dir_path))
        dir_nodes[dir_path] = node

    # Clear old directory tag edges for affected nodes (rebuild)
    for node in dir_nodes.values():
        with store.connect() as conn:
            conn.execute("DELETE FROM directory_tag_edges WHERE node_id=?", (node["node_id"],))

    local_edges_created = 0

    for dir_path, dir_leaves in by_dir.items():
        node = dir_nodes[dir_path]
        tagged_count = len(dir_leaves)
        readable_count = len([l for l in store.list_leaves(limit=10000)
                              if (l.get("parent_directory_path") or str(Path(l["path"]).parent)) == dir_path
                              and l.get("readable_status") == "readable"])

        tag_support: dict[str, dict[str, Any]] = defaultdict(lambda: {"leaf_ids": set(), "weight": 0.0})
        for leaf in dir_leaves:
            for edge in store.list_leaf_tag_edges(leaf_id=leaf["leaf_id"]):
                tag_support[edge["tag_id"]]["leaf_ids"].add(leaf["leaf_id"])
                tag_support[edge["tag_id"]]["weight"] = max(
                    tag_support[edge["tag_id"]]["weight"], float(edge["weight"])
                )

        org_signals = node.get("org_signals") or _org_signals_for_dir(Path(dir_path))
        has_repr = any(_is_representative_leaf(l) for l in dir_leaves)

        for tag_id, info in tag_support.items():
            support = len(info["leaf_ids"])
            ratio = support / max(1, tagged_count) if tagged_count else 0.0
            if not _local_gate(support, ratio, org_signals, has_repr):
                continue
            store.upsert_directory_tag_edge(
                node["node_id"], tag_id,
                weight=info["weight"],
                edge_kind="aggregated_semantic",
                leaf_support_count=support,
                tagged_leaf_ratio=ratio,
                propagation_status="local",
            )
            local_edges_created += 1

        # Update node counts
        store.upsert_directory_node(
            dir_path,
            readable_leaf_count=readable_count,
            tagged_leaf_count=tagged_count,
            org_signals=org_signals,
        )

    # Propagation (candidate only)
    _propagate_candidates(dir_nodes)

    # Classify nodes
    _classify_nodes(dir_nodes, by_dir)

    return {
        "ok": True,
        "local_edges": local_edges_created,
        "directories": len(dir_nodes),
        "directory_macros": macro_result.get("directory_macros", 0),
        "key_evidence_compression_nodes": macro_result.get("key_evidence_compression_nodes", 0),
    }


def _propagate_candidates(dir_nodes: dict[str, dict[str, Any]]) -> None:
    """Propagate child local edges upward as candidate edges with decay."""
    nodes_by_id = {n["node_id"]: n for n in dir_nodes.values()}

    for depth in range(DEFAULT_PROPAGATION_MAX_DEPTH):
        for node in list(dir_nodes.values()):
            parent_id = node.get("parent_node_id")
            if not parent_id:
                continue
            if parent_id not in nodes_by_id:
                parent = store.get_directory_node(parent_id)
                if not parent:
                    continue
                nodes_by_id[parent_id] = parent
            parent = nodes_by_id[parent_id]
            for edge in store.list_directory_tag_edges(node_id=node["node_id"]):
                if edge["propagation_status"] != "local" and depth == 0:
                    continue
                child_weight = float(edge["weight"])
                pw = child_weight * DEFAULT_PROPAGATION_DECAY
                if pw < 0.1:
                    continue
                store.upsert_directory_tag_edge(
                    parent["node_id"], edge["tag_id"],
                    weight=pw,
                    edge_kind=edge["edge_kind"],
                    leaf_support_count=edge["leaf_support_count"],
                    tagged_leaf_ratio=edge["tagged_leaf_ratio"] * DEFAULT_PROPAGATION_DECAY,
                    propagation_status="candidate",
                    source_child_node_id=node["node_id"],
                )


def _classify_nodes(dir_nodes: dict[str, dict[str, Any]], by_dir: dict[str, list]) -> None:
    for dir_path, node in dir_nodes.items():
        existing = store.get_directory_node(node["node_id"]) or node
        name = Path(dir_path).name.lower()
        org_signals = existing.get("org_signals") or _org_signals_for_dir(Path(dir_path))
        tagged = len(by_dir.get(dir_path, []))
        local_edges = store.list_directory_tag_edges(node_id=node["node_id"])
        local_only = [e for e in local_edges if e["propagation_status"] == "local"]
        unique_tags = len({e["tag_id"] for e in local_only})
        is_macro = node_has_key_evidence_compression(existing)

        node_type = "container"
        if unique_tags >= 2:
            node_type = "semantic_node"
        elif unique_tags >= 1 and tagged >= 2:
            node_type = "semantic_node"
        elif unique_tags >= 1 and tagged >= 1:
            node_type = "organizational"
        elif is_macro:
            node_type = "directory_macro"

        if name in SHALLOW_CONTAINER_NAMES and node_type == "semantic_node" and unique_tags < 3:
            node_type = "organizational" if unique_tags >= 1 else ("directory_macro" if is_macro else "container")

        activation = min(10.0, unique_tags * 1.5 + tagged * 0.3)
        if is_macro and node_type == "directory_macro":
            activation = max(activation, float(existing.get("activation_weight") or 0))
        store.upsert_directory_node(
            dir_path,
            node_type=node_type,
            org_signals=org_signals,
            activation_weight=activation,
            compression_weight=activation * 0.8,
            sampling_weight=activation,
        )
