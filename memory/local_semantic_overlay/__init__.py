"""LSO — ablation-boundary package."""

from .read import looks_like_raw_dump, read_leaf, sanitize_display
from .search import ensure_search_ready, search_paths, search_rows
from .select import EvidenceFlags, select_for_read
from .overlay import (
    OverlayFlags, apply_aggregation, apply_compression, apply_leaf_tags, apply_node,
    apply_recheck, enforce_active_budget, ensure_leaf, gather_aggregation_candidates,
    load, prepare_aggregation_task, prepare_compression_task, prepare_leaf_tag_task,
    prepare_recheck_task, record_feedback, save, shallow_preview,
)
from .navigate import NavigateFlags, query, record_hit, recheck_cold_node
