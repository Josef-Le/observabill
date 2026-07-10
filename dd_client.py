"""
dd_client.py — Read-only Datadog Cost API client.

Python 3.11 stdlib only. No third-party dependencies.

Public functions
----------------
get_estimated_cost(api_key, app_key, site)
get_projected_cost(api_key, app_key, site)
get_historical_cost(api_key, app_key, site, start_month, end_month=None)
get_monthly_cost_attribution(api_key, app_key, site, start_month, tag_breakdown_keys=None, fields="*")

Parsing helpers (pure, no I/O)
-------------------------------
parse_cost_by_product(json_dict)  -> list[dict]
grand_total(by_product_list)      -> float
parse_tag_attribution(json_dict, dims)  -> list[dict] | dict
summarize(estimated_json, projected_json, prev_historical_json) -> dict

HTTP layer
----------
_http_get is module-level so tests can monkeypatch it without network access.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DatadogError(Exception):
    """Base exception for all Datadog API errors."""


class AuthError(DatadogError):
    """HTTP 401 — invalid or missing credentials."""


class PermissionError(DatadogError):
    """HTTP 403 — credentials valid but insufficient permissions."""


class RateLimitError(DatadogError):
    """HTTP 429 — rate limit exceeded.

    Attributes
    ----------
    reset_seconds : int | None
        Seconds until the rate limit resets, from X-RateLimit-Reset header.
    """

    def __init__(self, message: str, reset_seconds: int | None = None) -> None:
        super().__init__(message)
        self.reset_seconds = reset_seconds


# ---------------------------------------------------------------------------
# Site → base URL map
# ---------------------------------------------------------------------------

_SITE_MAP: dict[str, str] = {
    "us1": "https://api.datadoghq.com",
    "us3": "https://api.us3.datadoghq.com",
    "us5": "https://api.us5.datadoghq.com",
    "eu":  "https://api.datadoghq.eu",
    "ap1": "https://api.ap1.datadoghq.com",
    "ap2": "https://api.ap2.datadoghq.com",
    "uk1": "https://api.uk1.datadoghq.com",
}


def base_url(site: str) -> str:
    """Return the base API URL for the given Datadog site identifier.

    Parameters
    ----------
    site : str
        One of: us1, us3, us5, eu, ap1, ap2, uk1.

    Raises
    ------
    ValueError
        If *site* is not a recognised identifier.
    """
    try:
        return _SITE_MAP[site]
    except KeyError:
        raise ValueError(f"Unknown Datadog site: {site!r}. Valid sites: {sorted(_SITE_MAP)}")


# ---------------------------------------------------------------------------
# HTTP layer — mockable without network
# ---------------------------------------------------------------------------

def _http_get(url: str, headers: dict[str, str], timeout: int = 15) -> tuple[int, dict, bytes]:
    """Perform an HTTP GET with proxy bypass.

    Returns
    -------
    (status_code, response_headers, body_bytes)

    The real implementation uses urllib with an explicit empty ProxyHandler so
    that environment-level HTTP_PROXY / HTTPS_PROXY settings are ignored —
    Datadog API calls must go direct, not through the SSM tunnel.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return (resp.status, dict(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        return (exc.code, dict(exc.headers), exc.read())


# ---------------------------------------------------------------------------
# Private request helper
# ---------------------------------------------------------------------------

def _request(
    path: str,
    params: dict[str, Any],
    api_key: str,
    app_key: str,
    site: str,
) -> dict:
    """Build URL, call _http_get, map HTTP status to typed exceptions.

    SECURITY: api_key and app_key are sent only in headers, never embedded in
    the URL or in exception messages.

    Parameters
    ----------
    path   : str  — e.g. "/api/v2/usage/estimated_cost"
    params : dict — query-string parameters (values must be str/int/float)
    api_key, app_key, site : credentials / routing

    Returns
    -------
    Parsed JSON dict on HTTP 200.

    Raises
    ------
    AuthError        on 401
    PermissionError  on 403
    RateLimitError   on 429 (with .reset_seconds from X-RateLimit-Reset)
    DatadogError     on any other non-200 status
    """
    _base = base_url(site)
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = f"{_base}{path}{qs}"

    # Auth goes in headers only — never in the URL.
    headers = {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
    }

    status, resp_headers, body = _http_get(url, headers)

    if status == 200:
        return json.loads(body)

    # Map error codes to typed exceptions.
    # CRITICAL: exception messages must never include api_key or app_key.
    if status == 401:
        raise AuthError(f"HTTP 401 Unauthorized from {path!r}")

    if status == 403:
        raise PermissionError(f"HTTP 403 Forbidden from {path!r}")

    if status == 429:
        reset_raw = resp_headers.get("X-RateLimit-Reset") or resp_headers.get("x-ratelimit-reset")
        reset_seconds: int | None = None
        if reset_raw is not None:
            try:
                reset_seconds = int(reset_raw)
            except (ValueError, TypeError):
                pass
        raise RateLimitError(f"HTTP 429 Rate Limited on {path!r}", reset_seconds=reset_seconds)

    raise DatadogError(f"HTTP {status} error from {path!r}")


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def get_estimated_cost(api_key: str, app_key: str, site: str = "us1") -> dict:
    """GET /api/v2/usage/estimated_cost — returns raw JSON dict."""
    return _request("/api/v2/usage/estimated_cost", {}, api_key, app_key, site)


def get_projected_cost(api_key: str, app_key: str, site: str = "us1") -> dict:
    """GET /api/v2/usage/projected_cost — returns raw JSON dict."""
    return _request("/api/v2/usage/projected_cost", {}, api_key, app_key, site)


def get_historical_cost(
    api_key: str,
    app_key: str,
    site: str = "us1",
    *,
    start_month: str,
    end_month: str | None = None,
) -> dict:
    """GET /api/v2/usage/historical_cost.

    Parameters
    ----------
    start_month : str  — "YYYY-MM-DD"
    end_month   : str | None — "YYYY-MM-DD" (optional)
    """
    params: dict[str, str] = {"start_month": start_month}
    if end_month is not None:
        params["end_month"] = end_month
    return _request("/api/v2/usage/historical_cost", params, api_key, app_key, site)


def get_monthly_cost_attribution(
    api_key: str,
    app_key: str,
    site: str = "us1",
    *,
    start_month: str,
    tag_breakdown_keys: str | None = None,
    fields: str = "*",
) -> dict:
    """GET /api/v2/usage/monthly_cost_attribution.

    Parameters
    ----------
    start_month       : str        — "YYYY-MM"
    tag_breakdown_keys: str | None — comma-separated tag keys (optional)
    fields            : str        — cost dimensions to return (default "*")
    """
    params: dict[str, str] = {
        "start_month": start_month,
        "fields": fields,
    }
    if tag_breakdown_keys is not None:
        params["tag_breakdown_keys"] = tag_breakdown_keys
    return _request("/api/v2/usage/monthly_cost_attribution", params, api_key, app_key, site)


# ---------------------------------------------------------------------------
# Parsing helpers — pure functions, no I/O
# ---------------------------------------------------------------------------

def parse_cost_by_product(json_dict: dict) -> list[dict]:
    """Aggregate charges by product_name across all orgs in the response.

    Works with estimated_cost, historical_cost, and projected_cost responses.

    Returns
    -------
    list of {product_name, committed, on_demand, total}
    where total = committed + on_demand + any adjustment charges.
    """
    # Accumulate per product: {name: {committed, on_demand, adjustment}}
    accumulator: dict[str, dict[str, float]] = {}

    for row in json_dict.get("data", []):
        charges = row.get("attributes", {}).get("charges", [])
        for charge in charges:
            name = charge.get("product_name", "")
            cost = float(charge.get("cost", 0.0))
            ctype = charge.get("charge_type", "")

            if name not in accumulator:
                accumulator[name] = {"committed": 0.0, "on_demand": 0.0, "adjustment": 0.0}

            if ctype == "committed":
                accumulator[name]["committed"] += cost
            elif ctype == "on_demand":
                accumulator[name]["on_demand"] += cost
            elif ctype == "adjustment":
                accumulator[name]["adjustment"] += cost
            # Unknown charge types are summed into adjustment-like bucket via the else clause.
            # Only three canonical types are tracked; unknown types are silently ignored
            # rather than corrupting totals.

    result = []
    for name, buckets in accumulator.items():
        committed = buckets["committed"]
        on_demand = buckets["on_demand"]
        adjustment = buckets["adjustment"]
        result.append(
            {
                "product_name": name,
                "committed": committed,
                "on_demand": on_demand,
                "total": committed + on_demand + adjustment,
            }
        )
    return result


def grand_total(by_product_list: list[dict]) -> float:
    """Sum the total field across all product rows.

    Parameters
    ----------
    by_product_list : output of parse_cost_by_product()

    Returns
    -------
    float — aggregate cost across all products.
    """
    return float(sum(row["total"] for row in by_product_list))


def parse_tag_attribution(json_dict: dict, dims: list[str]) -> list[dict] | dict:
    """Parse a monthly_cost_attribution response.

    Parameters
    ----------
    json_dict : raw API response dict
    dims      : list of cost dimension keys to extract (e.g. ["infra_host_total_cost"])

    Returns
    -------
    If Usage Attribution is configured (at least one row has non-null tags):
        list of {tags: {...}, costs: {dim: amount}}

    If Usage Attribution is NOT configured (all rows have tags=null):
        {"configured": False, "rows": []}
        — the caller should surface an "enable Usage Attribution" hint.
    """
    rows = json_dict.get("data", [])

    # Check whether attribution is configured (any row with non-null tags).
    any_configured = any(
        row.get("attributes", {}).get("tags") is not None
        for row in rows
    )

    if not any_configured:
        return {"configured": False, "rows": []}

    result = []
    for row in rows:
        attrs = row.get("attributes", {})
        tags = attrs.get("tags")
        costs = {dim: float(attrs.get(dim, 0.0)) for dim in dims}
        result.append({"tags": tags, "costs": costs})
    return result


def summarize(
    estimated_json: dict,
    projected_json: dict,
    prev_historical_json: dict,
) -> dict:
    """Produce a unified cost summary from three API responses.

    Parameters
    ----------
    estimated_json      : response from get_estimated_cost()
    projected_json      : response from get_projected_cost()
    prev_historical_json: response from get_historical_cost() for the previous month

    Returns
    -------
    dict with keys:
        total                : float  — current estimated total
        by_product           : list   — output of parse_cost_by_product(estimated_json)
        on_demand_overage    : float  — sum of on_demand costs across all products
        projected_end_of_month: float — grand total of projected_json
        prev_month_total     : float  — grand total of prev_historical_json
        delta_pct            : float  — percentage change vs prev month (can be negative)
        spike                : bool   — True if delta_pct > 25 OR on_demand > 15% of total
    """
    by_product = parse_cost_by_product(estimated_json)
    total = grand_total(by_product)

    on_demand_overage = float(sum(row["on_demand"] for row in by_product))

    projected_products = parse_cost_by_product(projected_json)
    projected_end_of_month = grand_total(projected_products)

    prev_products = parse_cost_by_product(prev_historical_json)
    prev_month_total = grand_total(prev_products)

    if prev_month_total != 0.0:
        delta_pct = (total - prev_month_total) / prev_month_total * 100.0
    else:
        delta_pct = 0.0

    overage_ratio = (on_demand_overage / total) if total > 0.0 else 0.0
    spike = delta_pct > 25.0 or overage_ratio > 0.15

    return {
        "total": total,
        "by_product": by_product,
        "on_demand_overage": on_demand_overage,
        "projected_end_of_month": projected_end_of_month,
        "prev_month_total": prev_month_total,
        "delta_pct": delta_pct,
        "spike": spike,
    }


# ---------------------------------------------------------------------------
# Write helper — used by /apply endpoint
# ---------------------------------------------------------------------------

def _http_write(
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes,
    timeout: int = 15,
) -> tuple[int, dict, bytes]:
    """Perform an HTTP write (PUT/PATCH/POST) with proxy bypass.

    Module-level so tests can monkeypatch it without network access.

    Returns
    -------
    (status_code, response_headers, body_bytes)
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with opener.open(req, timeout=timeout) as resp:
            return (resp.status, dict(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        return (exc.code, dict(exc.headers), exc.read())


def write(
    path: str,
    verb: str,
    payload: dict,
    api_key: str,
    app_key: str,
    site: str = "us1",
) -> dict:
    """Execute a write (PUT/PATCH/POST) against the Datadog API.

    Parameters
    ----------
    path    : str  — API path, e.g. "/api/v1/logs/config/indexes/main"
    verb    : str  — HTTP verb: "PUT", "PATCH", or "POST"
    payload : dict — request body (serialised to JSON)
    api_key : str  — Datadog API key (headers only, never logged)
    app_key : str  — Datadog application key (headers only, never logged)
    site    : str  — Datadog site identifier (default "us1")

    Returns
    -------
    Parsed JSON dict on success (2xx).

    Raises
    ------
    ValueError       if verb is not PUT, PATCH, or POST
    AuthError        on 401
    PermissionError  on 403
    RateLimitError   on 429
    DatadogError     on other non-2xx

    SECURITY: api_key and app_key are sent only in headers, never embedded
    in the URL, body, or exception messages.
    """
    allowed_verbs = {"PUT", "PATCH", "POST"}
    v = verb.upper()
    if v not in allowed_verbs:
        raise ValueError(f"write() only supports {allowed_verbs}; got {verb!r}")

    _base = base_url(site)
    url = f"{_base}{path}"

    headers = {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
        "Content-Type": "application/json",
    }
    body_bytes = json.dumps(payload).encode("utf-8")

    status, resp_headers, resp_body = _http_write(url, v, headers, body_bytes)

    if 200 <= status < 300:
        try:
            return json.loads(resp_body)
        except Exception:
            return {}

    # Map error codes — NEVER include api_key/app_key in messages
    if status == 401:
        raise AuthError(f"HTTP 401 Unauthorized from {path!r}")
    if status == 403:
        raise PermissionError(f"HTTP 403 Forbidden from {path!r}")
    if status == 429:
        reset_raw = resp_headers.get("X-RateLimit-Reset") or resp_headers.get("x-ratelimit-reset")
        reset_seconds: int | None = None
        if reset_raw is not None:
            try:
                reset_seconds = int(reset_raw)
            except (ValueError, TypeError):
                pass
        raise RateLimitError(f"HTTP 429 Rate Limited on {path!r}", reset_seconds=reset_seconds)

    raise DatadogError(f"HTTP {status} error from {path!r}")
