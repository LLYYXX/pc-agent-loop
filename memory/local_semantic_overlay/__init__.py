"""Public API for Local Semantic Overlay v3."""

from .feedback import finish_file_query, record_correction
from .diagnostics import build_cost_metrics, build_ingestion_diagnostics, format_agent_report, format_diagnostics_summary
from .directory_macro import build_macro_node_reports, node_has_key_evidence_compression
from .evidence_title import build_evidence_title, build_node_overview_title
from .directory_macro import compress_directories_from_key_evidence
from .evidence_bundle import (
    apply_bundle_annotations,
    bundle_annotation_schema,
    next_evidence_bundle,
    preview_next_evidence_bundle,
)
from .ingestion import (
    audit_phase_a,
    begin_ingestion,
    finish_ingestion,
    mark_compress_done,
    run_ingestion_pipeline,
    run_ingestion_step,
)
from .leaf_tagging import apply_leaf_tags, apply_session_leaf_tags, leaf_tag_schema, next_tagging_packet
from .overview_build import build_overview
from .directory_agg import aggregate_directory_tags
from .leaf_seed import discover_leaf_seeds, register_fallback_seed
from .runtime import lso_summary, query_map, run_file_query, system_overview
from .search_substrate import (
    ensure_search_ready,
    last_search_diagnostic,
    resolve_es_exe,
    search_files_detailed,
    search_files_paths,
    search_files_rows,
)
from .store import init_db

__all__ = [
    "apply_bundle_annotations",
    "apply_leaf_tags",
    "apply_session_leaf_tags",
    "aggregate_directory_tags",
    "audit_phase_a",
    "begin_ingestion",
    "build_cost_metrics",
    "build_evidence_title",
    "build_ingestion_diagnostics",
    "build_macro_node_reports",
    "build_node_overview_title",
    "build_overview",
    "bundle_annotation_schema",
    "compress_directories_from_key_evidence",
    "discover_leaf_seeds",
    "ensure_search_ready",
    "finish_file_query",
    "finish_ingestion",
    "format_agent_report",
    "format_diagnostics_summary",
    "init_db",
    "last_search_diagnostic",
    "leaf_tag_schema",
    "lso_summary",
    "mark_compress_done",
    "next_evidence_bundle",
    "preview_next_evidence_bundle",
    "next_tagging_packet",
    "node_has_key_evidence_compression",
    "query_map",
    "record_correction",
    "register_fallback_seed",
    "resolve_es_exe",
    "run_file_query",
    "run_ingestion_pipeline",
    "run_ingestion_step",
    "search_files_detailed",
    "search_files_paths",
    "search_files_rows",
    "system_overview",
]
