"""Route-tier maintenance for Local Semantic Overlay v2."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import store
from .config import ACTIVE_STALE_DAYS, MAX_ACTIVE_ROUTES, MAX_ROUTES, MAX_WARM_ROUTES, WARM_STALE_DAYS


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


SEEDED_GRACE_DAYS = 14


def _age_days(value: str | None, fallback: str | None = None) -> float:
    """Days since the given timestamp. Falls back to fallback (e.g. created_at) instead of 9999."""
    parsed = _parse_time(value)
    if not parsed:
        parsed = _parse_time(fallback)
    if not parsed:
        return 9999.0
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400


def route_score(route: dict[str, Any]) -> float:
    usage = float(route.get("usage_score") or 0)
    quality = float(route.get("quality_score") or 0)
    confidence = float(route.get("confidence") or 0)
    risk = float(route.get("risk_score") or 0)
    stale_penalty = min(_age_days(route.get("last_used"), route.get("created_at")) / 180, 2.0)
    return usage + quality + confidence - risk - stale_penalty


def maintenance_tick() -> dict[str, Any]:
    """Rebalance route tiers opportunistically."""
    store.init_db()
    routes = store.list_routes()
    demoted: list[dict[str, str]] = []
    promoted: list[dict[str, str]] = []

    for route in routes:
        tier = route["tier"]
        # fix10: seeded routes get a grace period before stale demotion
        is_seeded = route.get("usage_verification") == "seeded"
        created_age = _age_days(route.get("created_at"))
        if is_seeded and created_age < SEEDED_GRACE_DAYS:
            continue

        age = _age_days(route.get("last_used"), route.get("created_at"))
        if tier == "active" and age > ACTIVE_STALE_DAYS:
            store.set_route_tier(route["route_id"], "warm")
            demoted.append({"route_id": route["route_id"], "from": "active", "to": "warm", "reason": "stale_active"})
        elif tier == "warm" and age > WARM_STALE_DAYS and float(route.get("usage_score") or 0) <= 0:
            store.set_route_tier(route["route_id"], "cold")
            demoted.append({"route_id": route["route_id"], "from": "warm", "to": "cold", "reason": "stale_warm"})

    routes = store.list_routes()
    active = sorted([r for r in routes if r["tier"] == "active"], key=route_score, reverse=True)
    for route in active[MAX_ACTIVE_ROUTES:]:
        store.set_route_tier(route["route_id"], "warm")
        demoted.append({"route_id": route["route_id"], "from": "active", "to": "warm", "reason": "active_cap"})

    routes = store.list_routes()
    warm = sorted([r for r in routes if r["tier"] == "warm"], key=route_score, reverse=True)
    for route in warm[MAX_WARM_ROUTES:]:
        store.set_route_tier(route["route_id"], "cold")
        demoted.append({"route_id": route["route_id"], "from": "warm", "to": "cold", "reason": "warm_cap"})

    routes = store.list_routes()
    if len(routes) > MAX_ROUTES:
        cold = sorted([r for r in routes if r["tier"] == "cold"], key=route_score)
        for route in cold[:len(routes) - MAX_ROUTES]:
            store.bump_route(route["route_id"], risk_delta=0.5)
            demoted.append({"route_id": route["route_id"], "from": "cold", "to": "cold", "reason": "route_cap_risk"})

    return {"ok": True, "demoted": demoted, "promoted": promoted, "summary": store.lso_counts()}
