"""LSO public facade: audited Agent-facing contract only."""

from .search import ensure_search_ready, search_rows
from .select import EvidenceFlags, select_for_read
from .overlay import (BuildSession, OverlayFlags, apply_aggregation, apply_compression, build_audit,
                      prepare_aggregation_task, prepare_compression_task, prepare_leaf_tag_task,
                      propose_leaf_tags)
from .maintenance import apply_recheck, enforce_active_budget, record_feedback
from .navigate import NavigateFlags, query, record_hit, recheck_cold_node
