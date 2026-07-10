"""
savings.py — ObservaBill savings-detection engine.

Python 3.11 stdlib only. No third-party dependencies.

Detects cost savings opportunities in a Datadog account and returns
structured SavingsOpportunity and ScanResult dicts for the shared
data contract consumed by the UI and /apply endpoint.

Public API
----------
scan(api_key, app_key, site, prices=None) -> ScanResult
build_apply_request(opportunity) -> {endpoint, verb, payload}
preflight_scopes(api_key, app_key, site) -> dict
derive_effective_prices(estimated_cost_json, usage_logs_json, defaults=DEFAULT_PRICES) -> dict

Detector functions (each returns SavingsOpportunity | None)
------------------------------------------------------------
detect_exclusion_candidates(logs_aggregate, logs_indexes, prices=DEFAULT_PRICES)
detect_logs_to_metrics(logs_aggregate, logs_indexes, prices=DEFAULT_PRICES)
detect_high_cardinality_metrics(metrics_volumes, prices=DEFAULT_PRICES)
detect_index_quota(usage_logs, logs_indexes, prices=DEFAULT_PRICES)

HTTP layer
----------
_http_get is module-level so tests can monkeypatch it without network access.
_http_post is module-level for the logs aggregate POST.

SECURITY: api_key and app_key are sent only in request headers.
They must never appear in exception messages, generated configs, or any
returned data structure. Keys are consumed once in _make_headers() and
nowhere else.
"""

from __future__ import annotations

import json
import math
import statistics
import urllib.parse
import urllib.request
from typing import Any

import dd_client  # reuse base_url + exceptions


# ---------------------------------------------------------------------------
# Pricing defaults (list-price estimates, user-overridable)
# ---------------------------------------------------------------------------

DEFAULT_PRICES: dict[str, float] = {
    # Logs
    "indexed_log_per_million": 1.70,      # 15-day retention, list price per million events
    "ingested_log_per_gb": 0.10,          # ingestion cost per GB
    # Metrics
    "custom_metric_per_month": 0.05,      # per custom metric timeseries per month
    # Logs-to-metrics conversion benefit
    "metric_query_per_month": 5.00,       # estimated fixed cost of a custom metric (DDM)
}

# Tags that are forbidden in generated_config recommendations (high cardinality / PII)
_FORBIDDEN_TAGS = frozenset({"user_id", "trace_id", "request_id", "ip"})

# Volume threshold: minimum monthly events (extrapolated from 7d window) to be considered
# a candidate for exclusion or conversion (1 million events/month)
_MIN_MONTHLY_EVENTS_THRESHOLD = 1_000_000

# For logs-to-metrics: a service is "low-variance" (safe to convert) if non-2xx volume
# is less than this fraction of total volume for that service
_MAX_ERROR_RATIO_FOR_L2M = 0.05  # 5% errors → safe to convert


# ---------------------------------------------------------------------------
# HTTP layer — mockable in tests
# ---------------------------------------------------------------------------

def _http_get(url: str, headers: dict[str, str], timeout: int = 15) -> tuple[int, dict, bytes]:
    """HTTP GET with proxy bypass (direct, not through SSM tunnel).

    Returns (status_code, response_headers, body_bytes).
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return (resp.status, dict(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        return (exc.code, dict(exc.headers), exc.read())


def _http_post(url: str, headers: dict[str, str], body: bytes, timeout: int = 15) -> tuple[int, dict, bytes]:
    """HTTP POST with proxy bypass.

    Returns (status_code, response_headers, body_bytes).
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return (resp.status, dict(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        return (exc.code, dict(exc.headers), exc.read())


# ---------------------------------------------------------------------------
# Private request helpers
# ---------------------------------------------------------------------------

def _make_headers(api_key: str, app_key: str) -> dict[str, str]:
    """Build auth headers. Keys must never leave this function into other data."""
    return {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
        "Content-Type": "application/json",
    }


def _raise_for_status(status: int, path: str) -> None:
    """Map HTTP error codes to dd_client typed exceptions.

    SECURITY: path is a URL path string only — api_key/app_key are never
    included in exception messages.
    """
    if status == 401:
        raise dd_client.AuthError(f"HTTP 401 Unauthorized from {path!r}")
    if status == 403:
        raise dd_client.PermissionError(f"HTTP 403 Forbidden from {path!r}")
    if status == 429:
        raise dd_client.RateLimitError(f"HTTP 429 Rate Limited on {path!r}")
    if status != 200:
        raise dd_client.DatadogError(f"HTTP {status} error from {path!r}")


def _get(path: str, params: dict[str, Any], api_key: str, app_key: str, site: str) -> dict:
    """GET helper: build URL, call _http_get, raise on error, return parsed JSON."""
    base = dd_client.base_url(site)
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = f"{base}{path}{qs}"
    status, _, body = _http_get(url, _make_headers(api_key, app_key))
    _raise_for_status(status, path)
    return json.loads(body)


def _post(path: str, payload: dict, api_key: str, app_key: str, site: str) -> dict:
    """POST helper: build URL, call _http_post, raise on error, return parsed JSON."""
    base = dd_client.base_url(site)
    url = f"{base}{path}"
    body = json.dumps(payload).encode()
    status, _, resp_body = _http_post(url, _make_headers(api_key, app_key), body)
    _raise_for_status(status, path)
    return json.loads(resp_body)


# ---------------------------------------------------------------------------
# Data fetch helpers
# ---------------------------------------------------------------------------

def _fetch_logs_aggregate(api_key: str, app_key: str, site: str) -> dict:
    """POST /api/v2/logs/analytics/aggregate — 7-day window, group_by service+status."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    payload = {
        "compute": [{"aggregation": "count", "type": "total", "metric": "count"}],
        "filter": {
            "from": seven_days_ago.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "indexes": ["*"],
        },
        "group_by": [
            {"facet": "service", "limit": 50, "sort": {"order": "desc", "type": "measure", "aggregation": "count"}},
            {"facet": "status", "limit": 10},
        ],
        "options": {"timezone": "UTC"},
    }
    return _post("/api/v2/logs/analytics/aggregate", payload, api_key, app_key, site)


def _fetch_usage_logs(api_key: str, app_key: str, site: str) -> dict:
    """GET /api/v1/usage/logs — last 30 days."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)
    return _get(
        "/api/v1/usage/logs",
        {
            "start_hr": thirty_days_ago.strftime("%Y-%m-%dT%H"),
            "end_hr": now.strftime("%Y-%m-%dT%H"),
        },
        api_key, app_key, site,
    )


def _fetch_logs_indexes(api_key: str, app_key: str, site: str) -> dict:
    """GET /api/v1/logs/config/indexes."""
    return _get("/api/v1/logs/config/indexes", {}, api_key, app_key, site)


def _fetch_metrics_list(api_key: str, app_key: str, site: str) -> dict:
    """GET /api/v2/metrics — list custom metrics."""
    return _get("/api/v2/metrics", {"filter[tags_cardinality]": "true"}, api_key, app_key, site)


def _fetch_metric_volumes(metric_name: str, api_key: str, app_key: str, site: str) -> dict:
    """GET /api/v2/metrics/{name}/volumes."""
    return _get(f"/api/v2/metrics/{metric_name}/volumes", {}, api_key, app_key, site)


def _fetch_usage_timeseries(api_key: str, app_key: str, site: str) -> dict:
    """GET /api/v1/usage/timeseries — custom metric count (for cost proxy)."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    two_days_ago = now - timedelta(days=2)
    return _get(
        "/api/v1/usage/timeseries",
        {
            "start_hr": two_days_ago.strftime("%Y-%m-%dT%H"),
            "end_hr": now.strftime("%Y-%m-%dT%H"),
        },
        api_key, app_key, site,
    )


# ---------------------------------------------------------------------------
# Shared math helpers
# ---------------------------------------------------------------------------

def _extrapolate_to_monthly(events_in_7d: int) -> float:
    """Scale a 7-day event count to a 30-day month."""
    return events_in_7d * 30.0 / 7.0


def _savings_pct_str(savings: float, total: float) -> str:
    """Format savings as a percentage string (e.g. '23%')."""
    if total <= 0:
        return "0%"
    return f"{round(savings / total * 100)}%"


def _make_id(lever: str, discriminator: str) -> str:
    """Generate a stable, URL-safe opportunity ID."""
    safe = discriminator.replace("/", "_").replace(".", "_").replace(" ", "_")
    return f"{lever}:{safe}"


# ---------------------------------------------------------------------------
# Scope labels and unlock descriptions
# ---------------------------------------------------------------------------

_SCOPE_UNLOCKS: dict[str, str] = {
    "logs_read": "log-exclusion + logs→metrics savings (detectors 1 and 2)",
    "metrics_read": "high-cardinality metric savings (detector 3)",
    "billing_read": "exact $ totals from your real bill via derive_effective_prices",
    "usage_read": "index quota recommendations (detector 4) + blended-rate derivation",
}


# ---------------------------------------------------------------------------
# preflight_scopes — probe endpoints to infer key permissions
# ---------------------------------------------------------------------------

def preflight_scopes(api_key: str, app_key: str, site: str) -> dict:
    """Probe cheap read-only endpoints and infer which scopes the key has.

    Returns
    -------
    {
        "logs_read": bool,
        "metrics_read": bool,
        "billing_read": bool,
        "usage_read": bool,
        "missing": [scope_name, ...],
        "unlocks": {scope_name: description},
    }

    A 200 => scope present; 403 => absent; any other error => absent (safe default).
    Keys are NEVER included in the returned dict.
    """
    base = dd_client.base_url(site)
    headers = _make_headers(api_key, app_key)

    def _probe(path: str) -> bool:
        """Return True if the endpoint returns 200, False otherwise."""
        url = f"{base}{path}"
        try:
            status, _, _ = _http_get(url, headers)
            return status == 200
        except Exception:
            return False

    logs_read = _probe("/api/v1/logs/config/indexes")
    metrics_read = _probe("/api/v2/metrics?page[limit]=1")
    # billing_read and usage_read share the same estimated_cost endpoint
    billing_ok = _probe("/api/v2/usage/estimated_cost")
    billing_read = billing_ok
    usage_read = billing_ok

    missing = []
    if not logs_read:
        missing.append("logs_read")
    if not metrics_read:
        missing.append("metrics_read")
    if not billing_read:
        missing.append("billing_read")
    if not usage_read:
        missing.append("usage_read")

    return {
        "logs_read": logs_read,
        "metrics_read": metrics_read,
        "billing_read": billing_read,
        "usage_read": usage_read,
        "missing": missing,
        "unlocks": dict(_SCOPE_UNLOCKS),
    }


# ---------------------------------------------------------------------------
# derive_effective_prices — compute blended indexed-log rate from real bill
# ---------------------------------------------------------------------------

def derive_effective_prices(
    estimated_cost_json: dict,
    usage_logs_json: dict,
    defaults: dict[str, float] = DEFAULT_PRICES,
) -> dict:
    """Compute real blended indexed-log rate from actual billing data.

    Parameters
    ----------
    estimated_cost_json : response from GET /api/v2/usage/estimated_cost
    usage_logs_json     : response from GET /api/v1/usage/logs
    defaults            : base price dict to merge into (default: DEFAULT_PRICES)

    Returns
    -------
    {
        "prices": {...},       # all DEFAULT_PRICES keys; indexed_log_per_million may be overridden
        "source": "derived" | "list",
        "blended_note": str,
    }
    """
    prices = dict(defaults)

    # --- Extract total indexed-log cost from estimated_cost_json ---
    indexed_log_total = 0.0
    for row in estimated_cost_json.get("data", []):
        for charge in row.get("attributes", {}).get("charges", []):
            if "log" in charge.get("product_name", "").lower():
                indexed_log_total += float(charge.get("cost", 0.0))

    # --- Extract total indexed events (millions) from usage_logs_json ---
    total_indexed_events = sum(
        int(row.get("indexed_events_count", 0))
        for row in usage_logs_json.get("usage", [])
    )
    total_indexed_millions = total_indexed_events / 1_000_000

    # --- Derive blended rate if both components are usable ---
    if indexed_log_total > 0 and total_indexed_millions > 0:
        blended_rate = indexed_log_total / total_indexed_millions
        prices["indexed_log_per_million"] = blended_rate
        source = "derived"
        blended_note = (
            f"Blended rate ${blended_rate:.4f}/million derived from "
            f"${indexed_log_total:.2f} indexed-log charges / "
            f"{total_indexed_millions:.1f}M events (30d)"
        )
    else:
        source = "list"
        blended_note = (
            "Using list-price defaults (estimated_cost or usage volume unavailable). "
            f"Default indexed_log_per_million=${defaults.get('indexed_log_per_million', 0):.2f}."
        )

    return {
        "prices": prices,
        "source": source,
        "blended_note": blended_note,
    }


# ---------------------------------------------------------------------------
# Detector 1 — Exclusion filter candidates
# ---------------------------------------------------------------------------

def _is_noise_status(status) -> bool:
    """True for low-value, high-volume log statuses safe to exclude/sample.

    Case-INSENSITIVE (real Datadog `status` facet values are lowercase, e.g.
    "debug", "200"). Matches HTTP 2xx success codes (access-log noise) and the
    DEBUG level. Deliberately does NOT match "info"/"warn"/"error" — we never
    recommend dropping potentially-useful logs by default.
    """
    s = str(status).strip().lower()
    return s == "debug" or s.startswith("2")


def detect_exclusion_candidates(
    logs_aggregate: dict,
    logs_indexes: dict,
    prices: dict[str, float] = DEFAULT_PRICES,
) -> "dict | None":
    """Detect high-volume 200/DEBUG services that should be excluded from indexing.

    Parameters
    ----------
    logs_aggregate : response from POST /api/v2/logs/analytics/aggregate
    logs_indexes   : response from GET /api/v1/logs/config/indexes
    prices         : pricing dict (defaults to DEFAULT_PRICES)

    Returns
    -------
    SavingsOpportunity dict or None if no candidates meet the threshold.
    """
    price_per_million = prices["indexed_log_per_million"]
    buckets = logs_aggregate.get("data", {}).get("buckets", [])

    # Aggregate monthly event counts per (service, status) pair
    candidates: list[tuple[str, str, float]] = []  # (service, status, monthly_events)
    for bucket in buckets:
        by = bucket.get("by", {})
        service = by.get("service", "unknown")
        status = str(by.get("status", ""))
        count_7d = int(bucket.get("computes", {}).get("c0", 0))
        monthly = _extrapolate_to_monthly(count_7d)

        # Candidate: high-volume 200 or DEBUG logs
        if _is_noise_status(status) and monthly >= _MIN_MONTHLY_EVENTS_THRESHOLD:
            candidates.append((service, status, monthly))

    if not candidates:
        return None

    # Sort by volume descending, take top
    candidates.sort(key=lambda x: x[2], reverse=True)
    top_service, top_status, top_monthly = candidates[0]

    # Total monthly events across all candidates
    total_monthly_events = sum(c[2] for c in candidates)
    monthly_savings_usd = total_monthly_events / 1_000_000 * price_per_million

    # Pick a target index (first without exclusion_filters covering this pattern)
    indexes = logs_indexes.get("indexes", [])
    target_index = indexes[0]["name"] if indexes else "main"

    # Build evidence list (top cost drivers)
    evidence = [
        {
            "label": f"{svc} [{st}]",
            "volume": f"{monthly / 1_000_000:.1f}M events/month",
            "cost_usd": round(monthly / 1_000_000 * price_per_million, 2),
        }
        for svc, st, monthly in candidates[:5]
    ]

    # Candidate exclusion filter queries
    filter_queries = [
        f"service:{svc} status:{st}"
        for svc, st, _ in candidates[:3]
    ]
    primary_query = filter_queries[0] if filter_queries else f"status:{top_status}"

    generated_config = {
        "endpoint": f"/api/v1/logs/config/indexes/{target_index}",
        "verb": "PUT",
        "payload": {
            "exclusion_filters": [
                {
                    "name": f"auto-exclude-{top_service}-{top_status}".lower().replace(".", "-"),
                    "filter": {"query": primary_query, "sample_rate": 1.0},
                    "is_enabled": True,
                }
            ]
        },
    }

    # Estimate total indexed cost for savings_pct denominator
    total_indexed_cost = total_monthly_events / 1_000_000 * price_per_million

    _detection_query = (
        'POST /api/v2/logs/analytics/aggregate '
        'group_by service,status query:"status:200 OR status:DEBUG" '
        f'(top candidate: service:{top_service} status:{top_status})'
    )
    _why = (
        f"{len(candidates)} service/status pair(s) produce "
        f"{total_monthly_events / 1_000_000:.1f}M indexable events/month at "
        f"${price_per_million:.2f}/million = ${monthly_savings_usd:.2f}/month; "
        f"top offender is {top_service} [{top_status}] — safe to exclude because "
        "200/DEBUG logs carry no actionable signal."
    )

    return {
        "id": _make_id("exclusion_filter", f"{top_service}-{top_status}"),
        "lever": "exclusion_filter",
        "category": "logs",
        "title": f"Exclude high-volume {top_status} logs for {top_service}",
        "summary": (
            f"Found {len(candidates)} service/status combination(s) generating "
            f"{total_monthly_events / 1_000_000:.1f}M indexable events/month "
            f"that can be safely excluded. Top candidate: {top_service} [{top_status}]."
        ),
        "monthly_savings_usd": round(monthly_savings_usd, 2),
        "savings_pct": _savings_pct_str(monthly_savings_usd, total_indexed_cost),
        "effort": "low",
        "confidence": "high",
        "evidence": evidence,
        "generated_config": generated_config,
        "needs_write_scope": True,
        "detection_query": _detection_query,
        "why": _why,
    }


# ---------------------------------------------------------------------------
# Detector 2 — Logs-to-metrics conversion
# ---------------------------------------------------------------------------

def detect_logs_to_metrics(
    logs_aggregate: dict,
    logs_indexes: dict,
    prices: dict[str, float] = DEFAULT_PRICES,
) -> "dict | None":
    """Detect high-volume, low-variance 200-only services suitable for log-based metrics.

    A service is safe to convert if:
    - 200-status events >> non-200 events (error ratio < _MAX_ERROR_RATIO_FOR_L2M)
    - Monthly volume exceeds threshold (worth the conversion effort)
    - None of the group_by keys are forbidden (user_id, trace_id, request_id, ip)

    Parameters
    ----------
    logs_aggregate : response from POST /api/v2/logs/analytics/aggregate
    logs_indexes   : response from GET /api/v1/logs/config/indexes
    prices         : pricing dict

    Returns
    -------
    SavingsOpportunity dict or None.
    """
    price_per_million = prices["indexed_log_per_million"]
    metric_cost = prices["custom_metric_per_month"]

    buckets = logs_aggregate.get("data", {}).get("buckets", [])

    # Build per-service volume breakdown: {service: {status: count_7d}}
    service_volumes: dict[str, dict[str, float]] = {}
    for bucket in buckets:
        by = bucket.get("by", {})
        service = by.get("service", "unknown")
        status = str(by.get("status", ""))
        count_7d = int(bucket.get("computes", {}).get("c0", 0))
        service_volumes.setdefault(service, {})[status] = (
            service_volumes.get(service, {}).get(status, 0) + count_7d
        )

    # Find candidates: high-volume, nearly all 200
    l2m_candidates: list[tuple[str, float]] = []  # (service, monthly_200_events)
    for service, status_counts in service_volumes.items():
        total_7d = sum(status_counts.values())
        count_200_7d = sum(c for st, c in status_counts.items() if str(st).strip().startswith("2"))
        if total_7d == 0:
            continue
        error_ratio = (total_7d - count_200_7d) / total_7d
        monthly_200 = _extrapolate_to_monthly(count_200_7d)
        if error_ratio < _MAX_ERROR_RATIO_FOR_L2M and monthly_200 >= _MIN_MONTHLY_EVENTS_THRESHOLD:
            l2m_candidates.append((service, monthly_200))

    if not l2m_candidates:
        return None

    # Take the highest-volume candidate
    l2m_candidates.sort(key=lambda x: x[1], reverse=True)
    top_service, top_monthly = l2m_candidates[0]

    # Savings = indexed_cost - metric_cost
    indexed_cost = top_monthly / 1_000_000 * price_per_million
    # Replacing with a log-based metric costs ~metric_cost/month (fixed)
    net_savings = max(0.0, indexed_cost - metric_cost)

    if net_savings <= 0:
        return None

    # Safe group_by keys (no forbidden tags)
    safe_group_by = ["service", "env", "status_code"]

    generated_config = {
        "endpoint": "/api/v1/logs-metrics",
        "verb": "POST",
        "payload": {
            "data": {
                "id": f"logs.{top_service.replace('-', '_').replace('.', '_')}.request_count",
                "type": "logs_metrics",
                "attributes": {
                    "compute": {"aggregation_type": "count"},
                    "filter": {"query": f"service:{top_service} status:200", "indexes": ["*"]},
                    "group_by": [{"path": tag, "tag_name": tag} for tag in safe_group_by],
                },
            }
        },
    }

    evidence = [
        {
            "label": f"{svc} [200 only]",
            "volume": f"{monthly / 1_000_000:.1f}M events/month",
            "cost_usd": round(monthly / 1_000_000 * price_per_million, 2),
        }
        for svc, monthly in l2m_candidates[:5]
    ]

    _detection_query = (
        f'POST /api/v2/logs/analytics/aggregate '
        f'group_by service,status query:"service:{top_service} status:200" '
        f'(error_ratio<{_MAX_ERROR_RATIO_FOR_L2M*100:.0f}%, monthly>{_MIN_MONTHLY_EVENTS_THRESHOLD/1e6:.0f}M)'
    )
    _why = (
        f"{top_service} has {top_monthly / 1_000_000:.1f}M 200-status events/month "
        f"with <{_MAX_ERROR_RATIO_FOR_L2M * 100:.0f}% errors; "
        f"indexed cost ${indexed_cost:.2f}/month vs log-based metric ~${metric_cost:.2f}/month "
        f"= ${net_savings:.2f}/month net savings by converting to a count metric."
    )

    return {
        "id": _make_id("logs_to_metrics", top_service),
        "lever": "logs_to_metrics",
        "category": "logs",
        "title": f"Convert {top_service} 200-logs to log-based metric",
        "summary": (
            f"{top_service} generates {top_monthly / 1_000_000:.1f}M indexed events/month "
            f"with <{_MAX_ERROR_RATIO_FOR_L2M * 100:.0f}% errors. "
            f"A log-based metric captures the same signal at ~${metric_cost:.2f}/month."
        ),
        "monthly_savings_usd": round(net_savings, 2),
        "savings_pct": _savings_pct_str(net_savings, indexed_cost),
        "effort": "medium",
        "confidence": "medium",
        "evidence": evidence,
        "generated_config": generated_config,
        "needs_write_scope": True,
        "detection_query": _detection_query,
        "why": _why,
    }


# ---------------------------------------------------------------------------
# Detector 3 — High-cardinality metrics
# ---------------------------------------------------------------------------

def detect_high_cardinality_metrics(
    metrics_volumes: dict[str, dict],
    prices: dict[str, float] = DEFAULT_PRICES,
) -> "dict | None":
    """Detect custom metrics with forbidden high-cardinality tags.

    Forbidden tags: user_id, trace_id, request_id, ip.
    These explode cardinality and should be dropped from tag configurations.

    Parameters
    ----------
    metrics_volumes : {metric_name: volumes_response_dict} from GET /api/v2/metrics/{name}/volumes
    prices          : pricing dict

    Returns
    -------
    SavingsOpportunity dict or None.
    """
    price_per_metric = prices["custom_metric_per_month"]

    # Find metrics with forbidden tags
    offenders: list[tuple[str, int, list[str]]] = []  # (name, indexed_volume, forbidden_found)
    for metric_name, vol_response in metrics_volumes.items():
        attrs = vol_response.get("data", {}).get("attributes", {})
        tag_configs = attrs.get("tag_configurations", [])
        all_tags: set[str] = set()
        for tc in tag_configs:
            for tag in tc.get("tag_keys", []):
                all_tags.add(tag)
        # Also check the tags dict directly
        tags_dict = attrs.get("tags", {})
        all_tags.update(tags_dict.keys())

        forbidden_found = [t for t in all_tags if t in _FORBIDDEN_TAGS]
        if forbidden_found:
            indexed_volume = int(attrs.get("indexed_volume", 0))
            offenders.append((metric_name, indexed_volume, forbidden_found))

    if not offenders:
        return None

    # Sort by indexed_volume descending
    offenders.sort(key=lambda x: x[1], reverse=True)
    top_metric, top_volume, top_forbidden = offenders[0]

    # Safe tags = all tags minus forbidden ones
    attrs = metrics_volumes[top_metric].get("data", {}).get("attributes", {})
    all_tags_for_metric: set[str] = set()
    for tc in attrs.get("tag_configurations", []):
        all_tags_for_metric.update(tc.get("tag_keys", []))
    all_tags_for_metric.update(attrs.get("tags", {}).keys())
    safe_tags = sorted(t for t in all_tags_for_metric if t not in _FORBIDDEN_TAGS)

    # Savings: current cardinality cost minus reduced cardinality cost
    # Each forbidden tag potentially multiplies cardinality by N unique values
    # Conservatively: dropping forbidden tags reduces cardinality by 80%
    cardinality_reduction = 0.80
    current_cost = top_volume * price_per_metric
    reduced_volume = int(top_volume * (1 - cardinality_reduction))
    savings = (top_volume - reduced_volume) * price_per_metric

    generated_config = {
        "endpoint": f"/api/v1/metric/{top_metric}/tag_configurations",
        "verb": "PATCH",
        "payload": {
            "data": {
                "type": "manage_tags",
                "id": top_metric,
                "attributes": {
                    "tags": safe_tags,  # safe tags only — no forbidden ones
                },
            }
        },
    }

    evidence = [
        {
            "label": f"{name} (drops: {', '.join(fb)})",
            "volume": f"{vol:,} timeseries",
            "cost_usd": round(vol * price_per_metric, 2),
        }
        for name, vol, fb in offenders[:5]
    ]

    _detection_query = (
        f'GET /api/v2/metrics/{top_metric}/volumes '
        f'(tag_configurations inspected for forbidden tags: {sorted(_FORBIDDEN_TAGS)!r})'
    )
    _why = (
        f"{top_metric} uses forbidden tag(s) {top_forbidden!r} that inflate cardinality "
        f"to {top_volume:,} timeseries at ${price_per_metric:.4f}/timeseries = "
        f"${current_cost:.2f}/month; dropping them reduces volume by "
        f"~{cardinality_reduction * 100:.0f}% saving ${savings:.2f}/month."
    )

    return {
        "id": _make_id("high_cardinality_metric", top_metric),
        "lever": "high_cardinality_metric",
        "category": "metrics",
        "title": f"Drop high-cardinality tags from {top_metric}",
        "summary": (
            f"{top_metric} uses forbidden tag(s) {top_forbidden!r} that "
            f"explode cardinality to {top_volume:,} timeseries. "
            f"Removing them reduces volume by ~{cardinality_reduction * 100:.0f}%."
        ),
        "monthly_savings_usd": round(savings, 2),
        "savings_pct": _savings_pct_str(savings, current_cost),
        "effort": "high",
        "confidence": "medium",
        "evidence": evidence,
        "generated_config": generated_config,
        "needs_write_scope": True,
        "detection_query": _detection_query,
        "why": _why,
    }


# ---------------------------------------------------------------------------
# Detector 4 — Index daily quota
# ---------------------------------------------------------------------------

def detect_index_quota(
    usage_logs: dict,
    logs_indexes: dict,
    prices: dict[str, float] = DEFAULT_PRICES,
) -> "dict | None":
    """Recommend daily_limit for log indexes that have no quota set.

    Uses 30-day usage history to compute avg + stddev.
    Recommended quota = avg * 1.2 (20% headroom).

    Parameters
    ----------
    usage_logs   : response from GET /api/v1/usage/logs
    logs_indexes : response from GET /api/v1/logs/config/indexes
    prices       : pricing dict

    Returns
    -------
    SavingsOpportunity dict or None if all indexes already have a limit.
    """
    price_per_million = prices["indexed_log_per_million"]

    usage = usage_logs.get("usage", [])
    if not usage:
        return None

    daily_counts = [int(row.get("indexed_events_count", 0)) for row in usage]
    if not daily_counts:
        return None

    avg_daily = statistics.mean(daily_counts)
    recommended_quota = int(avg_daily * 1.2)

    # Find indexes without a daily_limit
    indexes = logs_indexes.get("indexes", [])
    target_indexes = [idx for idx in indexes if idx.get("daily_limit") is None]

    if not target_indexes:
        return None

    target = target_indexes[0]
    target_name = target["name"]

    # Savings: prevent overages. Estimate potential overage cost.
    # Overage days: days where count > recommended_quota
    overage_events = sum(
        max(0, count - recommended_quota) for count in daily_counts
    )
    monthly_savings_usd = overage_events / 1_000_000 * price_per_million

    # Stddev for confidence
    try:
        stddev = statistics.stdev(daily_counts) if len(daily_counts) > 1 else 0.0
    except statistics.StatisticsError:
        stddev = 0.0

    generated_config = {
        "endpoint": f"/api/v1/logs/config/indexes/{target_name}",
        "verb": "PUT",
        "payload": {
            "daily_limit": recommended_quota,
        },
    }

    evidence = [
        {
            "label": "30-day average daily events",
            "volume": f"{avg_daily / 1_000_000:.2f}M/day",
            "cost_usd": round(avg_daily / 1_000_000 * price_per_million, 4),
        },
        {
            "label": "Recommended daily quota (avg × 1.2)",
            "volume": f"{recommended_quota / 1_000_000:.2f}M/day",
            "cost_usd": round(recommended_quota / 1_000_000 * price_per_million, 4),
        },
    ]

    _detection_query = (
        f'GET /api/v1/usage/logs (30d) + GET /api/v1/logs/config/indexes '
        f'(index "{target_name}" has daily_limit=None; '
        f'avg={avg_daily/1e6:.2f}M/day stddev={stddev/1e6:.2f}M/day)'
    )
    _why = (
        f"Index '{target_name}' has no daily_limit; 30-day avg is "
        f"{avg_daily / 1_000_000:.2f}M events/day (stddev {stddev / 1_000_000:.2f}M). "
        f"Recommended quota {recommended_quota / 1_000_000:.2f}M events/day (avg × 1.2) "
        f"would have prevented {overage_events / 1e6:.2f}M overage events = "
        f"${monthly_savings_usd:.2f} in potential overages this period."
    )

    return {
        "id": _make_id("index_quota", target_name),
        "lever": "index_quota",
        "category": "logs",
        "title": f"Set daily quota on index '{target_name}' to prevent overages",
        "summary": (
            f"Index '{target_name}' has no daily_limit. "
            f"Based on 30-day history (avg {avg_daily / 1_000_000:.2f}M events/day, "
            f"stddev {stddev / 1_000_000:.2f}M), recommended quota is "
            f"{recommended_quota / 1_000_000:.2f}M events/day (avg × 1.2)."
        ),
        "monthly_savings_usd": round(monthly_savings_usd, 2),
        "savings_pct": "overage-prevention",
        "effort": "low",
        "confidence": "high",
        "evidence": evidence,
        "generated_config": generated_config,
        "needs_write_scope": True,
        "detection_query": _detection_query,
        "why": _why,
    }


# ---------------------------------------------------------------------------
# Top-level scan()
# ---------------------------------------------------------------------------

def scan(
    api_key: str,
    app_key: str,
    site: str = "us1",
    prices: "dict[str, float] | None" = None,
) -> dict:
    """Run all detectors and return a ScanResult.

    Fetches required data from Datadog read-only endpoints, runs all four
    detectors, drops Nones, ranks by savings (desc), and sums total waste.

    Parameters
    ----------
    api_key  : Datadog API key (sent only in headers, never logged)
    app_key  : Datadog application key (sent only in headers, never logged)
    site     : Datadog site (e.g. "us1", "eu")
    prices   : optional pricing override. If None, derives prices from billing
               data (best-effort). Pass a dict to use custom prices.

    Returns
    -------
    ScanResult dict with additional keys: scope_check, price_source.
    """
    notes: list[str] = []

    # --- Step 1: Preflight scope check ---
    scope_check: dict = {}
    try:
        scope_check = preflight_scopes(api_key, app_key, site)
    except Exception as exc:
        scope_check = {
            "logs_read": False,
            "metrics_read": False,
            "billing_read": False,
            "usage_read": False,
            "missing": ["logs_read", "metrics_read", "billing_read", "usage_read"],
            "unlocks": dict(_SCOPE_UNLOCKS),
        }
        notes.append(f"Preflight scope check failed: {type(exc).__name__}")

    # --- Step 2: Determine prices ---
    price_source: str
    if prices is not None:
        # Caller supplied explicit prices — use them, no derivation
        effective_prices = prices
        price_source = "custom"
    else:
        # Try to derive from real billing data (best-effort)
        estimated_cost_json: dict = {}
        usage_for_pricing: dict = {}
        try:
            estimated_cost_json = _get("/api/v2/usage/estimated_cost", {}, api_key, app_key, site)
        except dd_client.DatadogError:
            pass  # graceful: derive_effective_prices will fall back to list

        try:
            usage_for_pricing = _fetch_usage_logs(api_key, app_key, site)
        except dd_client.DatadogError:
            pass

        price_result = derive_effective_prices(estimated_cost_json, usage_for_pricing)
        effective_prices = price_result["prices"]
        price_source = price_result["source"]

    # --- Fetch logs data ---
    logs_aggregate: dict = {}
    usage_logs: dict = {}
    logs_indexes: dict = {}

    try:
        logs_aggregate = _fetch_logs_aggregate(api_key, app_key, site)
    except dd_client.DatadogError as exc:
        notes.append(f"Logs aggregate fetch failed: {type(exc).__name__}")

    try:
        usage_logs = _fetch_usage_logs(api_key, app_key, site)
    except dd_client.DatadogError as exc:
        notes.append(f"Usage/logs fetch failed: {type(exc).__name__}")

    try:
        logs_indexes = _fetch_logs_indexes(api_key, app_key, site)
    except dd_client.DatadogError as exc:
        notes.append(f"Logs indexes fetch failed: {type(exc).__name__}")

    # --- Fetch metrics data ---
    metrics_volumes: dict[str, dict] = {}
    try:
        metrics_list = _fetch_metrics_list(api_key, app_key, site)
        metric_names = [m["id"] for m in metrics_list.get("data", []) if "id" in m]
        for metric_name in metric_names[:20]:  # cap at 20 to avoid rate limits
            try:
                metrics_volumes[metric_name] = _fetch_metric_volumes(metric_name, api_key, app_key, site)
            except dd_client.DatadogError:
                pass
    except dd_client.DatadogError as exc:
        notes.append(f"Metrics list fetch failed: {type(exc).__name__}")

    # --- Run detectors ---
    opportunities: list[dict] = []

    opp = detect_exclusion_candidates(logs_aggregate, logs_indexes, prices=effective_prices)
    if opp is not None:
        opportunities.append(opp)

    opp = detect_logs_to_metrics(logs_aggregate, logs_indexes, prices=effective_prices)
    if opp is not None:
        opportunities.append(opp)

    opp = detect_high_cardinality_metrics(metrics_volumes, prices=effective_prices)
    if opp is not None:
        opportunities.append(opp)

    opp = detect_index_quota(usage_logs, logs_indexes, prices=effective_prices)
    if opp is not None:
        opportunities.append(opp)

    # --- Rank by savings descending ---
    opportunities.sort(key=lambda o: o["monthly_savings_usd"], reverse=True)

    total_waste = round(sum(o["monthly_savings_usd"] for o in opportunities), 2)

    # --- Build sparkline from daily log volumes ---
    sparkline: list[float] = []
    for row in usage_logs.get("usage", []):
        count = float(row.get("indexed_events_count", 0))
        sparkline.append(count)

    # Region from site
    _site_to_region = {
        "us1": "us", "us3": "us", "us5": "us",
        "eu": "eu",
        "ap1": "ap", "ap2": "ap",
        "uk1": "uk",
    }
    region = _site_to_region.get(site, site)

    return {
        "total_monthly_waste_usd": total_waste,
        "currency": "USD",
        "region": region,
        "opportunities": opportunities,
        "sparkline": sparkline,
        "notes": notes,
        "scope_check": scope_check,
        "price_source": price_source,
    }


# ---------------------------------------------------------------------------
# build_apply_request
# ---------------------------------------------------------------------------

def build_apply_request(opportunity: dict) -> dict:
    """Extract the write spec from a SavingsOpportunity for the /apply endpoint.

    This function does NOT execute the write. It returns the generated_config
    dict so the /apply endpoint can dispatch it with proper write credentials.

    Parameters
    ----------
    opportunity : SavingsOpportunity dict

    Returns
    -------
    dict with keys: endpoint (str), verb (str), payload (dict)
    """
    gc = opportunity["generated_config"]
    return {
        "endpoint": gc["endpoint"],
        "verb": gc["verb"],
        "payload": gc["payload"],
    }
