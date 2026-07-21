"""
tests/test_log_cost_map.py — TDD RED phase for deep log cost analysis.

Tests for:
1. log_cost_map built from aggregate buckets, sorted desc by cost
2. log_total_monthly_cost_usd = sum of all bucket costs
3. Multiple SavingsOpportunity objects emitted (one per noisy bucket)
4. Whale drill: top bucket gets sub-pattern drill via secondary facet query
5. Whale drill degrades gracefully when facet returns empty / errors
6. share_pct math
7. Savings math consistent with cost formula
8. Cap at 8 opportunities max, sorted desc
9. Per-opportunity fields: service, status, monthly_events, monthly_cost_usd,
   evidence, detection_query, why, generated_config

Run: /opt/homebrew/opt/python@3.12/bin/python3.12 -m pytest tests/test_log_cost_map.py -q
"""

import json
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import savings


# ---------------------------------------------------------------------------
# Shared fixtures (realistic 7d aggregate shape, lowercase statuses)
# ---------------------------------------------------------------------------

# Real Datadog shape: service+status buckets, 7d window
# Top bucket: aws/debug ~1.1B events/month → 256M/7d
LOGS_AGGREGATE_MULTI = {
    "data": {
        "buckets": [
            # noise: debug → candidates
            {"by": {"service": "aws", "status": "debug"},      "computes": {"c0": 256_666_667}},   # ~1.1B/mo
            {"by": {"service": "nginx", "status": "200"},      "computes": {"c0": 80_000_000}},    # ~343M/mo
            {"by": {"service": "payment", "status": "debug"},  "computes": {"c0": 35_000_000}},    # ~150M/mo
            # noise: 2xx
            {"by": {"service": "health", "status": "200"},     "computes": {"c0": 10_000_000}},    # ~43M/mo
            {"by": {"service": "auth", "status": "201"},       "computes": {"c0": 5_000_000}},     # ~21M/mo
            # NOT noise (error / info / warn)
            {"by": {"service": "api", "status": "error"},      "computes": {"c0": 2_000_000}},     # ~8.6M/mo (keep)
            {"by": {"service": "api", "status": "info"},       "computes": {"c0": 1_500_000}},     # ~6.4M/mo (keep)
            {"by": {"service": "api", "status": "warn"},       "computes": {"c0": 500_000}},       # ~2.1M/mo (keep)
            # low volume noise (below 1M threshold)
            {"by": {"service": "tiny", "status": "debug"},     "computes": {"c0": 50_000}},        # ~214k/mo < threshold
        ]
    }
}

# Only non-noise buckets (error/info/warn)
LOGS_AGGREGATE_NO_NOISE = {
    "data": {
        "buckets": [
            {"by": {"service": "api", "status": "error"}, "computes": {"c0": 5_000_000}},
            {"by": {"service": "api", "status": "warn"},  "computes": {"c0": 3_000_000}},
        ]
    }
}

LOGS_INDEXES_FIXTURE = {
    "indexes": [
        {"name": "main", "num_retention_days": 15, "daily_limit": None, "exclusion_filters": []},
    ]
}

TEST_PRICES = {
    "indexed_log_per_million": 2.0,   # easy math: 1M events = $2
    "ingested_log_per_gb": 0.10,
    "custom_metric_per_month": 0.05,
    "metric_query_per_month": 5.0,
}


# ---------------------------------------------------------------------------
# Helper: call the new scan function variant that returns log_cost_map
# We test detect_exclusion_candidates_multi and build_log_cost_map directly.
# ---------------------------------------------------------------------------

def _make_http_mock(monkeypatch, aggregate=None):
    """Monkeypatch _http_get and _http_post for savings.py.

    aggregate: override for the logs aggregate POST response
    """
    if aggregate is None:
        aggregate = LOGS_AGGREGATE_MULTI

    # Valid empty events/search response shape
    empty_events = {"data": [], "meta": {"page": {"after": None}}}
    # Valid empty timeseries response
    empty_timeseries = {"data": {"buckets": []}}

    def fake_http_get(url, headers, timeout=15):
        if "/api/v1/logs/config/indexes" in url:
            body = LOGS_INDEXES_FIXTURE
        elif "/api/v1/usage/logs" in url:
            body = {"usage": []}
        elif "/api/v2/logs/config/metrics" in url:
            body = {"data": []}
        elif "/api/v2/metrics" in url and "/volumes" in url:
            body = {"data": {"id": "x", "type": "metric_volumes", "attributes": {"indexed_volume": 100, "ingested_volume": 100}}}
        elif "/api/v2/metrics" in url:
            body = {"data": []}
        elif "/api/v2/usage/estimated_cost" in url:
            body = {"data": []}
        else:
            body = {}
        return (200, {"content-type": "application/json"}, json.dumps(body).encode())

    def fake_http_post(url, headers, body_bytes, timeout=15):
        if "/api/v2/logs/events/search" in url:
            return (200, {"content-type": "application/json"}, json.dumps(empty_events).encode())
        if "/api/v2/logs/analytics/aggregate" in url:
            return (200, {"content-type": "application/json"}, json.dumps(aggregate).encode())
        return (200, {}, b"{}")

    monkeypatch.setattr(savings, "_http_get", fake_http_get)
    monkeypatch.setattr(savings, "_http_post", fake_http_post)


# ===========================================================================
# 1. build_log_cost_map — unit tests
# ===========================================================================

class TestBuildLogCostMap:
    """Test the new build_log_cost_map(logs_aggregate, prices) function."""

    def test_returns_list(self):
        """build_log_cost_map returns a list."""
        result = savings.build_log_cost_map(LOGS_AGGREGATE_MULTI, TEST_PRICES)
        assert isinstance(result, list)

    def test_all_buckets_included(self):
        """Every bucket in the aggregate appears in the cost map."""
        result = savings.build_log_cost_map(LOGS_AGGREGATE_MULTI, TEST_PRICES)
        assert len(result) == len(LOGS_AGGREGATE_MULTI["data"]["buckets"])

    def test_sorted_desc_by_monthly_cost(self):
        """Cost map rows are sorted descending by monthly_cost_usd."""
        result = savings.build_log_cost_map(LOGS_AGGREGATE_MULTI, TEST_PRICES)
        costs = [r["monthly_cost_usd"] for r in result]
        assert costs == sorted(costs, reverse=True)

    def test_each_row_has_required_keys(self):
        """Each row has service, status, monthly_events, monthly_cost_usd, share_pct."""
        result = savings.build_log_cost_map(LOGS_AGGREGATE_MULTI, TEST_PRICES)
        for row in result:
            assert "service" in row, f"Missing service in row: {row}"
            assert "status" in row
            assert "monthly_events" in row
            assert "monthly_cost_usd" in row
            assert "share_pct" in row

    def test_monthly_events_extrapolated_from_7d(self):
        """monthly_events = count_7d * 30/7."""
        result = savings.build_log_cost_map(LOGS_AGGREGATE_MULTI, TEST_PRICES)
        # Top row: aws/debug, 256_666_667 * 30/7 ≈ 1_100_000_001
        top = result[0]
        assert top["service"] == "aws"
        assert top["status"] == "debug"
        expected_monthly = int(256_666_667 * 30 / 7)
        assert abs(top["monthly_events"] - expected_monthly) < 10_000

    def test_cost_formula(self):
        """monthly_cost_usd = monthly_events / 1e6 * price_per_million."""
        result = savings.build_log_cost_map(LOGS_AGGREGATE_MULTI, TEST_PRICES)
        for row in result:
            expected_cost = row["monthly_events"] / 1_000_000 * TEST_PRICES["indexed_log_per_million"]
            assert abs(row["monthly_cost_usd"] - expected_cost) < 0.01, (
                f"Cost mismatch for {row['service']}/{row['status']}: "
                f"{row['monthly_cost_usd']} vs expected {expected_cost}"
            )

    def test_share_pct_sums_to_100(self):
        """share_pct values sum to ~100%."""
        result = savings.build_log_cost_map(LOGS_AGGREGATE_MULTI, TEST_PRICES)
        total_share = sum(r["share_pct"] for r in result)
        assert abs(total_share - 100.0) < 0.5, f"share_pct sums to {total_share}, expected ~100"

    def test_share_pct_is_proportional(self):
        """Top bucket share_pct > second bucket share_pct."""
        result = savings.build_log_cost_map(LOGS_AGGREGATE_MULTI, TEST_PRICES)
        assert result[0]["share_pct"] > result[1]["share_pct"]

    def test_empty_aggregate_returns_empty_list(self):
        """Empty buckets returns empty list."""
        result = savings.build_log_cost_map({"data": {"buckets": []}}, TEST_PRICES)
        assert result == []

    def test_total_cost_is_sum_of_row_costs(self):
        """build_log_total_cost returns sum of all row costs."""
        cost_map = savings.build_log_cost_map(LOGS_AGGREGATE_MULTI, TEST_PRICES)
        total = savings.build_log_total_cost(cost_map)
        expected = sum(r["monthly_cost_usd"] for r in cost_map)
        assert abs(total - expected) < 0.01


# ===========================================================================
# 2. (removed) detect_exclusion_candidates_multi was deleted in v2 refactor.
#    The shallow per-service/status detector was replaced by logs_intel.py
#    content-sampling which produces pattern_exclusion opportunities.
# ===========================================================================


# ===========================================================================
# 3. (removed) TestWhaleDrill — the v1 whale-drill (secondary facet query on
#    the top exclusion_filter bucket) was removed in the v2 refactor.
#    Content intelligence (logs_intel.py) mines log templates directly instead.
# ===========================================================================


# ===========================================================================
# 4. scan() returns log_cost_map and log_total_monthly_cost_usd
# ===========================================================================

class TestScanLogCostMap:
    """Integration: scan() result includes log_cost_map and log_total_monthly_cost_usd."""

    def test_scan_returns_log_cost_map(self, monkeypatch):
        """scan() result has log_cost_map key."""
        _make_http_mock(monkeypatch)
        result = savings.scan(
            api_key="test_key",
            app_key="test_app_key",
            site="us1",
            prices=TEST_PRICES,
        )
        assert "log_cost_map" in result, f"Missing log_cost_map. Keys: {list(result.keys())}"

    def test_scan_log_cost_map_is_list(self, monkeypatch):
        _make_http_mock(monkeypatch)
        result = savings.scan(api_key="k", app_key="a", site="us1", prices=TEST_PRICES)
        assert isinstance(result["log_cost_map"], list)

    def test_scan_log_cost_map_sorted_desc_by_cost(self, monkeypatch):
        _make_http_mock(monkeypatch)
        result = savings.scan(api_key="k", app_key="a", site="us1", prices=TEST_PRICES)
        costs = [r["monthly_cost_usd"] for r in result["log_cost_map"]]
        assert costs == sorted(costs, reverse=True)

    def test_scan_returns_log_total_monthly_cost_usd(self, monkeypatch):
        _make_http_mock(monkeypatch)
        result = savings.scan(api_key="k", app_key="a", site="us1", prices=TEST_PRICES)
        assert "log_total_monthly_cost_usd" in result, (
            f"Missing log_total_monthly_cost_usd. Keys: {list(result.keys())}"
        )
        assert isinstance(result["log_total_monthly_cost_usd"], float)

    def test_scan_log_total_equals_sum_of_map_costs(self, monkeypatch):
        _make_http_mock(monkeypatch)
        result = savings.scan(api_key="k", app_key="a", site="us1", prices=TEST_PRICES)
        total = result["log_total_monthly_cost_usd"]
        expected = sum(r["monthly_cost_usd"] for r in result["log_cost_map"])
        assert abs(total - expected) < 0.01

    def test_scan_returns_opportunities_list(self, monkeypatch):
        """scan() returns an opportunities list (may be empty when content sampling returns nothing)."""
        _make_http_mock(monkeypatch)
        result = savings.scan(
            api_key="k",
            app_key="a",
            site="us1",
            prices=TEST_PRICES,
        )
        # opportunities is always a list; content sampling with empty events returns []
        assert isinstance(result["opportunities"], list)
        # The new v2 keys must also be present
        assert "anomalies" in result
        assert "field_bloat" in result
        assert "lines_examined" in result
        assert "sampled" in result

    def test_scan_log_cost_map_empty_when_no_aggregate(self, monkeypatch):
        """When logs aggregate returns empty, log_cost_map is empty list."""
        def fake_http_get(url, headers, timeout=15):
            if "/api/v1/logs/config/indexes" in url:
                body = LOGS_INDEXES_FIXTURE
            else:
                body = {}
            return (200, {}, json.dumps(body).encode())

        def fake_http_post(url, headers, body_bytes, timeout=15):
            return (200, {}, json.dumps({"data": {"buckets": []}}).encode())

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)

        result = savings.scan(api_key="k", app_key="a", site="us1", prices=TEST_PRICES)
        assert result["log_cost_map"] == []
        assert result["log_total_monthly_cost_usd"] == 0.0


# ===========================================================================
# 5. UI: render_log_cost_map in ui.py
# ===========================================================================

class TestRenderLogCostMap:
    """Test the new render_log_cost_map function in ui.py."""

    def test_render_log_cost_map_function_exists(self):
        """ui.py must export render_log_cost_map."""
        import ui
        assert hasattr(ui, "render_log_cost_map"), "ui.render_log_cost_map not found"
        assert callable(ui.render_log_cost_map)

    def test_render_returns_string(self):
        """render_log_cost_map returns a non-empty HTML string."""
        import ui
        scan = {
            "log_cost_map": [
                {"service": "aws", "status": "debug", "monthly_events": 1_100_000_000,
                 "monthly_cost_usd": 2200.0, "share_pct": 55.0},
                {"service": "nginx", "status": "200", "monthly_events": 343_000_000,
                 "monthly_cost_usd": 686.0, "share_pct": 17.2},
            ],
            "log_total_monthly_cost_usd": 4000.0,
        }
        result = ui.render_log_cost_map(scan)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_render_shows_total_cost(self):
        """render_log_cost_map HTML includes the total cost value."""
        import ui
        scan = {
            "log_cost_map": [
                {"service": "aws", "status": "debug", "monthly_events": 1_100_000_000,
                 "monthly_cost_usd": 4000.0, "share_pct": 100.0},
            ],
            "log_total_monthly_cost_usd": 4000.0,
        }
        result = ui.render_log_cost_map(scan)
        # Should show the dollar amount somewhere
        assert "4,000" in result or "4000" in result

    def test_render_shows_service_names(self):
        """render_log_cost_map HTML includes service names."""
        import ui
        scan = {
            "log_cost_map": [
                {"service": "aws", "status": "debug", "monthly_events": 1_100_000_000,
                 "monthly_cost_usd": 2200.0, "share_pct": 55.0},
                {"service": "nginx", "status": "200", "monthly_events": 343_000_000,
                 "monthly_cost_usd": 686.0, "share_pct": 17.2},
            ],
            "log_total_monthly_cost_usd": 2886.0,
        }
        result = ui.render_log_cost_map(scan)
        assert "aws" in result
        assert "nginx" in result

    def test_render_empty_returns_something(self):
        """render_log_cost_map with empty map returns a placeholder (not blank)."""
        import ui
        scan = {"log_cost_map": [], "log_total_monthly_cost_usd": 0.0}
        result = ui.render_log_cost_map(scan)
        assert isinstance(result, str)
        # Should return at least a wrapper div

    def test_render_escapes_html_special_chars(self):
        """Service names with special chars are HTML-escaped."""
        import ui
        scan = {
            "log_cost_map": [
                {"service": "<script>alert('xss')</script>", "status": "debug",
                 "monthly_events": 5_000_000, "monthly_cost_usd": 10.0, "share_pct": 100.0},
            ],
            "log_total_monthly_cost_usd": 10.0,
        }
        result = ui.render_log_cost_map(scan)
        # The raw script tag must not appear verbatim
        assert "<script>" not in result

    def test_render_dashboard_includes_log_cost_map(self):
        """render_dashboard output includes Log Cost Map section when log_cost_map present."""
        import ui
        scan = {
            "total_monthly_waste_usd": 1000.0,
            "currency": "USD",
            "region": "us",
            "opportunities": [],
            "sparkline": [],
            "notes": [],
            "scope_check": {},
            "price_source": "list",
            "log_cost_map": [
                {"service": "aws", "status": "debug", "monthly_events": 500_000_000,
                 "monthly_cost_usd": 1000.0, "share_pct": 100.0},
            ],
            "log_total_monthly_cost_usd": 1000.0,
        }
        result = ui.render_dashboard(scan)
        # Should include the log cost map header text
        assert "Log Cost Map" in result


# ===========================================================================
# 6. Backward-compatibility: scan() ScanResult contract intact
# ===========================================================================

class TestBackwardCompatibility:
    """Core ScanResult contract keys remain after v2 refactor."""

    def test_scan_result_still_has_total_monthly_waste(self, monkeypatch):
        """scan() result still has total_monthly_waste_usd key (not broken)."""
        _make_http_mock(monkeypatch)
        result = savings.scan(api_key="k", app_key="a", site="us1", prices=TEST_PRICES)
        assert "total_monthly_waste_usd" in result
        assert "opportunities" in result
        assert isinstance(result["opportunities"], list)

    def test_scan_result_has_v2_keys(self, monkeypatch):
        """scan() result has the new v2 top-level keys."""
        _make_http_mock(monkeypatch)
        result = savings.scan(api_key="k", app_key="a", site="us1", prices=TEST_PRICES)
        for key in ("anomalies", "field_bloat", "lines_examined", "sampled"):
            assert key in result, f"scan() result missing v2 key: {key}"
        assert isinstance(result["anomalies"], list)
        assert isinstance(result["field_bloat"], list)
        assert isinstance(result["lines_examined"], int)
        assert isinstance(result["sampled"], bool)
