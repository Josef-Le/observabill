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

def _http_get(url: str, headers: dict[str, str], timeout: int = 20) -> tuple[int, dict, bytes]:
    """HTTP GET with proxy bypass (direct, not through SSM tunnel).

    Returns (status_code, response_headers, body_bytes). A network/socket timeout
    is surfaced as a synthetic 504 so callers map it to a typed DatadogError
    (never an unhandled exception that would crash a scan).
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return (resp.status, dict(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        return (exc.code, dict(exc.headers), exc.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        return (504, {}, b"")


def _http_post(url: str, headers: dict[str, str], body: bytes, timeout: int = 30) -> tuple[int, dict, bytes]:
    """HTTP POST with proxy bypass.

    Returns (status_code, response_headers, body_bytes). Socket/URL timeouts →
    synthetic 504 (mapped to DatadogError by _raise_for_status).
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return (resp.status, dict(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        return (exc.code, dict(exc.headers), exc.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        return (504, {}, b"")


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


def _fetch_logs_timeseries(api_key: str, app_key: str, site: str, days: int = 21) -> dict:
    """POST /api/v2/logs/analytics/aggregate as a per-day timeseries grouped by service.

    Used for volume-anomaly detection (spikes + newly-emerged noisy patterns).
    Single-facet group_by only (multi-dim group_by 400s on this API).
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    payload = {
        "compute": [{"aggregation": "count", "type": "timeseries", "interval": "1d", "metric": "count"}],
        "filter": {
            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "indexes": ["*"],
        },
        "group_by": [
            {"facet": "service", "limit": 20,
             "sort": {"order": "desc", "type": "measure", "aggregation": "count"}},
        ],
        "options": {"timezone": "UTC"},
    }
    return _post("/api/v2/logs/analytics/aggregate", payload, api_key, app_key, site)


def _fetch_pattern_timeseries(
    phrase_query: str,
    api_key: str,
    app_key: str,
    site: str,
    days: int = 17,
) -> dict:
    """POST /api/v2/logs/analytics/aggregate with a phrase filter, no grouping.

    Returns per-day timeseries for volume-anomaly detection on a specific pattern.
    Similar to _fetch_logs_timeseries but filters to a specific phrase query and
    has empty group_by (account-wide, not per-service).
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    payload = {
        "compute": [{"aggregation": "count", "type": "timeseries", "interval": "1d", "metric": "count"}],
        "filter": {
            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "indexes": ["*"],
            "query": phrase_query,
        },
        "group_by": [],
        "options": {"timezone": "UTC"},
    }
    return _post("/api/v2/logs/analytics/aggregate", payload, api_key, app_key, site)


def _validate_exclusion_query(
    query: str,
    expected_24h_events: int,
    api_key: str,
    app_key: str,
    site: str,
) -> dict:
    """Validate an exclusion query by POSTing to aggregate endpoint for 24h count.

    Parameters
    ----------
    query : exclusion filter query to validate
    expected_24h_events : expected count for sanity-check ratio
    api_key, app_key, site : auth

    Returns
    -------
    {
        "actual": int (observed count over last 24h),
        "expected": int (input param),
        "ratio": float (rounded to 2 decimals),
        "confidence": "high" (0.5 <= ratio <= 2.0) | "low" | "unknown" (on error),
    }
    """
    from datetime import datetime, timezone, timedelta
    try:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=1)
        payload = {
            "compute": [{"aggregation": "count", "type": "total", "metric": "count"}],
            "filter": {
                "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "indexes": ["*"],
                "query": query,
            },
            "group_by": [],
            "options": {"timezone": "UTC"},
        }
        resp = _post("/api/v2/logs/analytics/aggregate", payload, api_key, app_key, site)
        actual = int(resp.get("data", {}).get("buckets", [{}])[0].get("computes", {}).get("c0", 0))
    except dd_client.DatadogError:
        # On error, return safe defaults with unknown confidence
        return {
            "actual": 0,
            "expected": expected_24h_events,
            "ratio": 1.0,
            "confidence": "unknown",
        }

    # Compute ratio, guarding divide-by-zero
    if expected_24h_events <= 0:
        ratio = 1.0
    else:
        ratio = actual / expected_24h_events

    # Determine confidence: high if 0.5 <= ratio <= 2.0, else low
    if 0.5 <= ratio <= 2.0:
        confidence = "high"
    else:
        confidence = "low"

    return {
        "actual": actual,
        "expected": expected_24h_events,
        "ratio": round(ratio, 2),
        "confidence": confidence,
    }


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
    return _get("/api/v2/metrics", {}, api_key, app_key, site)


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
        "logs_read_data": bool,
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

    def _probe_post(path: str, payload: dict) -> bool:
        """Return True if POST to endpoint returns 200, False otherwise."""
        url = f"{base}{path}"
        try:
            body = json.dumps(payload).encode()
            status, _, _ = _http_post(url, headers, body)
            return status == 200
        except Exception:
            return False

    logs_read = _probe("/api/v1/logs/config/indexes")
    # NOTE: /api/v2/metrics rejects page[limit]/filter[tags_cardinality] with 400 (not 403),
    # which a naive probe misreads as "scope absent". Probe the plain endpoint: 200 => metrics_read.
    metrics_read = _probe("/api/v2/metrics")
    # billing_read and usage_read share the same estimated_cost endpoint
    billing_ok = _probe("/api/v2/usage/estimated_cost")
    billing_read = billing_ok
    usage_read = billing_ok

    # Probe logs_read_data: POST /api/v2/logs/events/search with minimal payload
    logs_read_data_payload = {
        "filter": {"query": "*", "from": "now-15m", "to": "now"},
        "page": {"limit": 1},
    }
    logs_read_data = _probe_post("/api/v2/logs/events/search", logs_read_data_payload)

    missing = []
    if not logs_read:
        missing.append("logs_read")
    if not metrics_read:
        missing.append("metrics_read")
    if not billing_read:
        missing.append("billing_read")
    if not usage_read:
        missing.append("usage_read")
    if not logs_read_data:
        missing.append("logs_read_data")

    return {
        "logs_read": logs_read,
        "metrics_read": metrics_read,
        "billing_read": billing_read,
        "usage_read": usage_read,
        "logs_read_data": logs_read_data,
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
# Log Cost Map helpers
# ---------------------------------------------------------------------------

def build_log_cost_map(
    logs_aggregate: dict,
    prices: dict[str, float],
) -> list[dict]:
    """Build a cost map from logs aggregate buckets.

    Returns a list of dicts sorted desc by monthly_cost_usd, each:
    {service, status, monthly_events, monthly_cost_usd, share_pct}
    Includes ALL buckets (not just noise candidates).
    """
    price_per_million = prices["indexed_log_per_million"]
    buckets = logs_aggregate.get("data", {}).get("buckets", [])

    rows = []
    for bucket in buckets:
        by = bucket.get("by", {})
        service = str(by.get("service", "unknown"))
        status = str(by.get("status", ""))
        count_7d = int(bucket.get("computes", {}).get("c0", 0))
        monthly = _extrapolate_to_monthly(count_7d)
        cost = monthly / 1_000_000 * price_per_million
        rows.append({
            "service": service,
            "status": status,
            "monthly_events": int(monthly),
            "monthly_cost_usd": round(cost, 2),
            "share_pct": 0.0,  # computed below
        })

    # Sort desc by cost
    rows.sort(key=lambda r: r["monthly_cost_usd"], reverse=True)

    # Compute share_pct
    total_cost = sum(r["monthly_cost_usd"] for r in rows)
    for row in rows:
        if total_cost > 0:
            row["share_pct"] = round(row["monthly_cost_usd"] / total_cost * 100, 2)
        else:
            row["share_pct"] = 0.0

    return rows


def build_log_total_cost(cost_map: list[dict]) -> float:
    """Return sum of all monthly_cost_usd values in a cost map."""
    return round(sum(r["monthly_cost_usd"] for r in cost_map), 2)


# ---------------------------------------------------------------------------
# Detector 3 — High-cardinality metrics (Metrics-without-Limits waste)
# ---------------------------------------------------------------------------

# Minimum ingested timeseries to be worth acting on
_MIN_INGESTED_TIMESERIES = 10_000

# Flag metrics where indexed/ingested < this ratio (querying < 50% of ingested = waste)
_MAX_INDEXED_RATIO = 0.50


def detect_high_cardinality_metrics(
    metrics_volumes: dict[str, dict],
    prices: dict[str, float] = DEFAULT_PRICES,
) -> "dict | None":
    """Detect custom metrics where ingested_volume >> indexed_volume (Metrics-without-Limits waste).

    Uses the REAL /api/v2/metrics/{name}/volumes response shape:
      {data: {type: "metric_volumes", id: "<name>",
              attributes: {indexed_volume: <int>, ingested_volume: <int>}}}

    ingested_volume = total timeseries produced (what you pay to ingest)
    indexed_volume  = timeseries actually queried/stored for alerting/dashboards

    When ingested >> indexed you are paying to ingest cardinality you never query.
    Metrics-without-Limits lets you configure which tag combinations to keep (keep
    only the indexed set), eliminating the unused cardinality cost.

    Flags a metric if:
      - ingested_volume >= _MIN_INGESTED_TIMESERIES (10k — small metrics aren't worth it)
      - indexed_volume / ingested_volume < _MAX_INDEXED_RATIO (< 50% queried = waste)

    Ranks flagged metrics by unused = ingested - indexed (descending).

    monthly_savings_usd = sum(unused) * custom_metric_per_month
      (unused timeseries you can stop ingesting via Metrics-without-Limits)

    Parameters
    ----------
    metrics_volumes : {metric_name: /volumes response dict}
                      from GET /api/v2/metrics/{name}/volumes
    prices          : pricing dict

    Returns
    -------
    SavingsOpportunity dict or None.
    """
    price_per_metric = prices["custom_metric_per_month"]

    # Find metrics with high waste: ingested >> indexed
    offenders: list[tuple[str, int, int, int]] = []  # (name, ingested, indexed, unused)
    for metric_name, vol_response in metrics_volumes.items():
        attrs = vol_response.get("data", {}).get("attributes", {})
        ingested_raw = attrs.get("ingested_volume")
        indexed_raw = attrs.get("indexed_volume")
        # Real API may return None for metrics without Metrics-without-Limits configured
        if ingested_raw is None or indexed_raw is None:
            continue
        ingested = int(ingested_raw)
        indexed = int(indexed_raw)

        if ingested < _MIN_INGESTED_TIMESERIES:
            continue  # too small to bother

        ratio = indexed / ingested if ingested > 0 else 1.0
        if ratio >= _MAX_INDEXED_RATIO:
            continue  # already querying >= 50% — not flagged

        unused = max(0, ingested - indexed)
        offenders.append((metric_name, ingested, indexed, unused))

    if not offenders:
        return None

    # Rank by unused timeseries descending (highest waste first)
    offenders.sort(key=lambda x: x[3], reverse=True)
    top_metric, top_ingested, top_indexed, top_unused = offenders[0]

    # Total unused across all flagged metrics (for total savings)
    total_unused = sum(x[3] for x in offenders)
    monthly_savings_usd = total_unused * price_per_metric
    ingested_cost = top_ingested * price_per_metric  # cost basis for savings_pct

    # generated_config: Metrics-without-Limits tag configuration
    # Real endpoint: POST /api/v2/metrics/{metric}/tags  (v2, documented)
    generated_config = {
        "endpoint": f"/api/v2/metrics/{top_metric}/tags",
        "verb": "POST",
        "payload": {
            "data": {
                "type": "manage_tags",
                "id": top_metric,
                "attributes": {
                    # Keep only the tags already being queried (indexed set).
                    # The actual tag list should be determined from your dashboards/monitors.
                    # This config tells Datadog to limit ingested cardinality to queried tags only.
                    "tags": [],  # populated by user from their monitor/dashboard tag set
                    "metric_type": "gauge",
                },
            }
        },
    }

    evidence = [
        {
            "label": name,
            "volume": f"{ingested:,} ingested / {indexed:,} queried",
            "cost_usd": round(unused * price_per_metric, 2),
        }
        for name, ingested, indexed, unused in offenders[:5]
    ]

    _detection_query = (
        f"GET /api/v2/metrics/{{name}}/volumes  -> "
        f"flag ingested_volume>>indexed_volume "
        f"(top: {top_metric}: {top_ingested:,} ingested / {top_indexed:,} queried)"
    )

    indexed_ratio_pct = (top_indexed / top_ingested * 100) if top_ingested > 0 else 0
    _why = (
        f"{len(offenders)} metric(s) ingest cardinality they never index/query. "
        f"Top offender: {top_metric} ingests {top_ingested:,} timeseries but only "
        f"indexes {top_indexed:,} ({indexed_ratio_pct:.1f}%) — "
        f"{top_unused:,} ingested-but-never-indexed timeseries at "
        f"${price_per_metric:.4f}/timeseries = "
        f"${top_unused * price_per_metric:.2f}/month wasted. "
        f"Metrics-without-Limits lets you configure which tag combinations to keep, "
        f"eliminating the ingested-but-never-indexed cost."
    )

    return {
        "id": _make_id("high_cardinality_metric", top_metric),
        "lever": "high_cardinality_metric",
        "category": "metrics",
        "title": f"Reduce ingested cardinality on {top_metric} via Metrics-without-Limits",
        "summary": (
            f"{len(offenders)} metric(s) ingest cardinality they never query. "
            f"Top: {top_metric} ingests {top_ingested:,} timeseries, "
            f"queries only {top_indexed:,} ({indexed_ratio_pct:.1f}%). "
            f"Metrics-without-Limits eliminates the {top_unused:,} unused timeseries."
        ),
        "monthly_savings_usd": round(monthly_savings_usd, 2),
        "savings_pct": _savings_pct_str(monthly_savings_usd, total_unused * price_per_metric),
        "effort": "medium",
        "confidence": "high",
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
    progress_cb: "None | callable" = None,
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
    progress_cb : optional callback(stage: str, pct: float) for progress tracking

    Returns
    -------
    ScanResult dict with additional keys: scope_check, price_source.
    """
    def _report(stage: str, pct: int | float):
        """Call progress_cb safely, swallowing exceptions."""
        if progress_cb:
            try:
                progress_cb(stage, pct)
            except Exception:
                pass

    notes: list[str] = []
    _report("Checking API access", 8)

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

    _report("Reading log volume", 22)

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

    # --- Fetch metrics data (bounded; log-content intelligence is the primary lever) ---
    import time as _time
    metrics_volumes: dict[str, dict] = {}
    _metrics_deadline = _time.time() + 20   # hard cap: don't let the metrics loop dominate the scan
    try:
        metrics_list = _fetch_metrics_list(api_key, app_key, site)
        metric_names = [m["id"] for m in metrics_list.get("data", []) if "id" in m]
        for i, metric_name in enumerate(metric_names[:30]):  # cap at 30 metrics
            if _time.time() > _metrics_deadline:
                break
            try:
                metrics_volumes[metric_name] = _fetch_metric_volumes(metric_name, api_key, app_key, site)
            except dd_client.RateLimitError:
                # Back off and retry once on 429
                _time.sleep(2)
                try:
                    metrics_volumes[metric_name] = _fetch_metric_volumes(metric_name, api_key, app_key, site)
                except dd_client.DatadogError:
                    pass
            except dd_client.DatadogError:
                pass
    except dd_client.DatadogError as exc:
        notes.append(f"Metrics list fetch failed: {type(exc).__name__}")

    # --- Build log cost map (ALL buckets — demoted to context, not the product) ---
    log_cost_map: list[dict] = build_log_cost_map(logs_aggregate, effective_prices)
    log_total_monthly_cost_usd: float = build_log_total_cost(log_cost_map)

    # === v3 CORE: POOLED, PATTERN-FIRST log intelligence ===================================
    import logs_intel
    from datetime import datetime, timezone, timedelta
    import time as _time

    pattern_opps: list[dict] = []
    pattern_leaderboard: list[dict] = []
    similar_families: list[dict] = []
    surges: list[dict] = []
    field_bloat: list[dict] = []
    lines_examined = 0
    sampled = False
    bill_share_pct = 0.0

    price_per_million = effective_prices["indexed_log_per_million"]
    indexes = logs_indexes.get("indexes", [])
    target_index = indexes[0]["name"] if indexes else "main"

    # The team's own log-based metric catalog — patterns they already care about.
    metric_catalog: list[str] = []
    try:
        metric_catalog = logs_intel.fetch_metric_catalog(api_key, app_key, site)
    except Exception as exc:
        notes.append(f"Metric catalog fetch failed: {type(exc).__name__}")

    # Compute account-wide monthly events for leaderboard ranking
    account_monthly_events = sum(r["monthly_events"] for r in log_cost_map)
    total_bill_cost = log_total_monthly_cost_usd

    # Gate on logs_read_data scope
    scope_gate = not scope_check.get("logs_read_data", False)

    _report("Sampling your live logs", 38)

    # POOLED SAMPLING: single sample across entire account (unless scope_gate)
    if not scope_gate and log_cost_map:
        now = datetime.now(timezone.utc)
        frm = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            events = logs_intel.sample_logs(
                query="*", frm=frm, to=to,
                api_key=api_key, app_key=app_key, site=site, cap=8000,
            )
            lines_examined = len(events)
            sampled = lines_examined > 0

            if sampled:
                # Orchestrate: mine patterns, classify, rank opportunities
                _report("Mining message templates", 62)
                res = logs_intel.analyze_patterns(
                    events,
                    sample_size=lines_examined,
                    account_monthly_events=account_monthly_events,
                    price_per_million=price_per_million,
                    total_bill_cost=total_bill_cost,
                    metric_catalog=metric_catalog,
                    target_index=target_index,
                )
                pattern_opps = res.get("opportunities", [])
                pattern_leaderboard = res.get("leaderboard", [])
                similar_families = res.get("families", [])
                field_bloat = logs_intel.detect_field_bloat(events, price_per_million=price_per_million)

                # TASK C: Compute bill_share_pct from leaderboard top-20 cumulative_pct
                # This reflects REAL leaderboard coverage, not just noise opps
                if pattern_leaderboard:
                    # Get the cumulative_pct from the last row (highest cumulative) in top 20
                    bill_share_pct = round(pattern_leaderboard[:20][-1].get("cumulative_pct", 0.0), 1)
                else:
                    bill_share_pct = 0.0
        except dd_client.DatadogError as exc:
            notes.append(f"Content sampling failed: {type(exc).__name__} "
                         "(needs logs_read_data scope on the API key)")
    else:
        # Scope gate active or no log_cost_map — skip pattern analysis
        pattern_opps = []
        pattern_leaderboard = []
        similar_families = []
        field_bloat = []
        bill_share_pct = 0.0

    _report("Detecting surges", 80)

    # SURGES: combine per-service anomalies + per-pattern surges
    # (a) Per-service timeseries anomalies
    per_service_anomalies: list[dict] = []
    try:
        ts_days = 21
        ts_resp = _fetch_logs_timeseries(api_key, app_key, site, days=ts_days)
        series = logs_intel.parse_timeseries(ts_resp)
        base = datetime.now(timezone.utc) - timedelta(days=ts_days - 1)
        dates = [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(ts_days)]
        per_service_anomalies = logs_intel.detect_volume_anomalies(
            series, dates=dates, price_per_million=price_per_million,
        )
    except dd_client.DatadogError as exc:
        notes.append(f"Anomaly timeseries fetch failed: {type(exc).__name__}")

    # (b) Per-pattern surges: top 8 leaderboard noise patterns
    per_pattern_surges: list[dict] = []
    _pattern_surge_deadline = _time.time() + 15  # 15s budget for pattern surge fetches
    for row in pattern_leaderboard[:8]:
        if _time.time() > _pattern_surge_deadline:
            break
        if not row.get("classification", {}).get("is_noise", False):
            continue
        try:
            # Build phrase query from template's literal words
            template = row.get("template", "")
            words = [w for w in template.split() if not (w.startswith("<") and w.endswith(">"))]
            phrase = " ".join(words[:4])
            if not phrase:
                continue
            phrase_query = f'"{phrase}"'

            ts_resp = _fetch_pattern_timeseries(phrase_query, api_key, app_key, site, days=17)
            series = logs_intel.parse_timeseries(ts_resp)
            if not series:
                continue
            base = datetime.now(timezone.utc) - timedelta(days=16)
            dates = [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(17)]
            anomalies = logs_intel.detect_volume_anomalies(
                series, dates=dates, price_per_million=price_per_million,
            )
            # Tag with template
            for anom in anomalies:
                anom["template"] = row.get("template", "")[:60]
                per_pattern_surges.append(anom)
        except Exception:
            # Wrap per-pattern call; no crash on individual failure
            continue

    # Merge and rank surges
    surges = per_service_anomalies + per_pattern_surges
    surges.sort(key=lambda a: a.get("severity", 0), reverse=True)
    surges = surges[:12]  # cap at 12

    _report("Analyzing fields & metrics", 92)

    # Build opportunities list: patterns + field-bloat + high-cardinality + index-quota
    opportunities: list[dict] = list(pattern_opps)

    # Add field-bloat opportunities
    for fb in field_bloat[:5]:
        opportunities.append(_field_bloat_to_opportunity(fb, target_index))

    # Detector 3: high-cardinality metric waste
    opp = detect_high_cardinality_metrics(metrics_volumes, prices=effective_prices)
    if opp is not None:
        opportunities.append(opp)

    # Detector 4: index daily-quota / overage prevention
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

    _report("Done", 100)

    return {
        "total_monthly_waste_usd": total_waste,
        "currency": "USD",
        "region": region,
        "opportunities": opportunities,
        "sparkline": sparkline,
        "notes": notes,
        "scope_check": scope_check,
        "price_source": price_source,
        "log_cost_map": log_cost_map,
        "log_total_monthly_cost_usd": log_total_monthly_cost_usd,
        # v3 pattern-first surfaces:
        "patterns": pattern_opps,
        "pattern_leaderboard": pattern_leaderboard,
        "similar_families": similar_families,
        "surges": surges,
        "bill_share_pct": bill_share_pct,
        "scope_gate": scope_gate,
        "lines_examined": lines_examined,
        "sampled": sampled,
        # Back-compat alias for anomalies
        "anomalies": surges,
        # Legacy field-bloat surface
        "field_bloat": field_bloat,
    }


def _field_bloat_to_opportunity(fb: dict, target_index: str) -> dict:
    """Wrap a field-bloat finding as a ScanResult SavingsOpportunity.

    Field bloat reduces ingested GB (not indexed events), so the $ figure is a
    conservative context estimate; the actionable output is the pipeline remap.
    """
    field = fb["field_name"]
    kind = fb["kind"]
    rec = fb["recommendation"]
    est = round(fb.get("impact", 0) / 50.0, 2)  # heuristic monthly $ proxy for ranking
    verb = "Trim" if kind == "large_field" else "Reduce cardinality of"
    return {
        "id": _make_id("field_bloat", field),
        "lever": "field_bloat",
        "category": "logs",
        "service": "",
        "title": f"{verb} bloated field `{field}`",
        "summary": fb["why_safe"],
        "monthly_savings_usd": est,
        "monthly_cost_usd": est,
        "savings_pct": "ingest-reduction",
        "effort": "medium",
        "confidence": "medium",
        "evidence": [{
            "label": field,
            "volume": (f"{int(fb['avg_bytes'])} bytes avg · {fb['frequency']*100:.0f}% of events"
                       if kind == "large_field"
                       else f"{fb['cardinality']} distinct values ({fb['cardinality_ratio']*100:.0f}% unique)"),
            "cost_usd": est,
        }],
        "generated_config": {
            "endpoint": "/api/v1/logs/config/pipelines",
            "verb": "POST",
            "payload": {
                "name": f"observabill-trim-{field}".replace(".", "-")[:80],
                "filter": {"query": "*"},
                "processors": [{
                    "type": "attribute-remapper" if kind == "high_cardinality" else "string-builder-processor",
                    "sources": [field],
                    "is_enabled": True,
                    "note": ("hash or drop this attribute to cut cardinality"
                             if kind == "high_cardinality" else "remove/shorten this large attribute"),
                }],
            },
        },
        "needs_write_scope": True,
        "detection_query": f"sampled events → field `{field}` flagged as {kind}",
        "why": fb["why_safe"],
        "why_safe": fb["why_safe"],
        "recommended_action": rec,
        "field_name": field,
        "cardinality": fb.get("cardinality", 0),
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
