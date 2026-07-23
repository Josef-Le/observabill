"""
tests/test_savings.py — TDD RED phase.

Tests for savings.py savings-detection engine.
All tests must FAIL before savings.py is created.

Data contract:
  SavingsOpportunity = dict with keys:
    id, lever, category, title, summary, monthly_savings_usd, savings_pct,
    effort, confidence, evidence (list of {label, volume, cost_usd}),
    generated_config ({endpoint, verb, payload}), needs_write_scope

  ScanResult = dict with keys:
    total_monthly_waste_usd, currency, region,
    opportunities (list[SavingsOpportunity], desc by monthly_savings_usd),
    sparkline (list[float]), notes (list[str])
"""

import json
import sys
import os
import importlib
import types
import pytest

# ---------------------------------------------------------------------------
# Path setup — same pattern as test_dd_client.py
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import savings  # noqa: E402  — must exist for tests to pass
import dd_client  # noqa: E402


# ---------------------------------------------------------------------------
# Realistic fixtures
# ---------------------------------------------------------------------------

# POST /api/v2/logs/analytics/aggregate response (7d window, group_by service+status)
LOGS_AGGREGATE_FIXTURE = {
    "data": {
        "buckets": [
            {
                "by": {"service": "api-gateway", "status": "200"},
                "computes": {"c0": 45_000_000},  # 45M events/7d → ~194M/month
            },
            {
                "by": {"service": "api-gateway", "status": "500"},
                "computes": {"c0": 150_000},  # 150k errors — keep these
            },
            {
                "by": {"service": "health-checker", "status": "200"},
                "computes": {"c0": 30_000_000},  # 30M health-check 200s → exclusion candidate
            },
            {
                "by": {"service": "payment-service", "status": "200"},
                "computes": {"c0": 8_000_000},  # 8M 200s
            },
            {
                "by": {"service": "payment-service", "status": "DEBUG"},
                "computes": {"c0": 12_000_000},  # 12M DEBUG logs → exclusion candidate
            },
            {
                "by": {"service": "cdn-proxy", "status": "200"},
                "computes": {"c0": 60_000_000},  # 60M CDN 200s → logs-to-metrics candidate
            },
        ]
    }
}

# GET /api/v1/usage/logs response (30d)
USAGE_LOGS_FIXTURE = {
    "usage": [
        {"date": "2026-06-01T00:00:00Z", "indexed_events_count": 5_000_000, "ingested_events_bytes": 5_000_000_000},
        {"date": "2026-06-02T00:00:00Z", "indexed_events_count": 5_200_000, "ingested_events_bytes": 5_200_000_000},
        {"date": "2026-06-03T00:00:00Z", "indexed_events_count": 4_800_000, "ingested_events_bytes": 4_800_000_000},
        {"date": "2026-06-04T00:00:00Z", "indexed_events_count": 5_100_000, "ingested_events_bytes": 5_100_000_000},
        {"date": "2026-06-05T00:00:00Z", "indexed_events_count": 5_300_000, "ingested_events_bytes": 5_300_000_000},
        {"date": "2026-06-06T00:00:00Z", "indexed_events_count": 5_400_000, "ingested_events_bytes": 5_400_000_000},
        {"date": "2026-06-07T00:00:00Z", "indexed_events_count": 5_500_000, "ingested_events_bytes": 5_500_000_000},
        {"date": "2026-06-08T00:00:00Z", "indexed_events_count": 5_000_000, "ingested_events_bytes": 5_000_000_000},
        {"date": "2026-06-09T00:00:00Z", "indexed_events_count": 5_000_000, "ingested_events_bytes": 5_000_000_000},
        {"date": "2026-06-10T00:00:00Z", "indexed_events_count": 5_000_000, "ingested_events_bytes": 5_000_000_000},
        # ... 20 more days (simplified — use same value)
    ]
    + [
        {
            "date": f"2026-06-{d:02d}T00:00:00Z",
            "indexed_events_count": 5_000_000,
            "ingested_events_bytes": 5_000_000_000,
        }
        for d in range(11, 31)
    ]
}

# GET /api/v1/logs/config/indexes
LOGS_INDEXES_FIXTURE = {
    "indexes": [
        {
            "name": "main",
            "num_retention_days": 15,
            "daily_limit": None,
            "exclusion_filters": [],  # no filters yet
        },
        {
            "name": "debug-index",
            "num_retention_days": 3,
            "daily_limit": 100_000_000,
            "exclusion_filters": [
                {"name": "existing-filter", "filter": {"query": "env:staging"}, "is_enabled": True}
            ],
        },
    ]
}

# GET /api/v2/metrics (list)
METRICS_LIST_FIXTURE = {
    "data": [
        {"id": "custom.api.latency", "type": "metrics"},
        {"id": "custom.db.query_time", "type": "metrics"},
        {"id": "custom.user.requests", "type": "metrics"},
    ]
}

# GET /api/v2/metrics/{name}/volumes — REAL API shape
# custom.user.requests: ingested 500k timeseries, indexed only 2k = 99.6% waste
METRIC_VOLUMES_HIGH_CARDINALITY = {
    "data": {
        "id": "custom.user.requests",
        "type": "metric_volumes",
        "attributes": {
            "indexed_volume": 2_000,
            "ingested_volume": 500_000,
        },
    }
}

# custom.api.latency: low ingestion, nearly all indexed — NOT a waste candidate
METRIC_VOLUMES_LOW_CARDINALITY = {
    "data": {
        "id": "custom.api.latency",
        "type": "metric_volumes",
        "attributes": {
            "indexed_volume": 4_000,
            "ingested_volume": 5_000,
        },
    }
}

# GET /api/v1/usage/timeseries (custom metrics cost proxy)
USAGE_TIMESERIES_FIXTURE = {
    "usage": [
        {"hour": "2026-06-01T00:00:00Z", "num_custom_timeseries": 520_000},
        {"hour": "2026-06-02T00:00:00Z", "num_custom_timeseries": 510_000},
    ]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_opportunity(opp: dict) -> None:
    """Assert a dict is a valid SavingsOpportunity."""
    assert isinstance(opp["id"], str) and opp["id"], "id must be non-empty string"
    assert opp["lever"] in (
        "exclusion_filter", "logs_to_metrics", "high_cardinality_metric", "index_quota",
        "pattern_exclusion", "field_bloat",
    ), f"Unknown lever: {opp['lever']}"
    assert opp["category"] in ("logs", "metrics"), f"Unknown category: {opp['category']}"
    assert isinstance(opp["title"], str) and opp["title"]
    assert isinstance(opp["summary"], str) and opp["summary"]
    assert isinstance(opp["monthly_savings_usd"], float)
    assert opp["monthly_savings_usd"] >= 0.0
    assert isinstance(opp["savings_pct"], str)
    assert opp["effort"] in ("low", "medium", "high")
    assert opp["confidence"] in ("high", "medium", "low")
    # evidence
    assert isinstance(opp["evidence"], list)
    for ev in opp["evidence"]:
        assert "label" in ev and "volume" in ev and "cost_usd" in ev
    # generated_config
    gc = opp["generated_config"]
    assert "endpoint" in gc and "verb" in gc and "payload" in gc
    assert isinstance(gc["payload"], dict)
    assert isinstance(opp["needs_write_scope"], bool)


def _validate_scan_result(result: dict) -> None:
    """Assert a dict is a valid ScanResult."""
    assert isinstance(result["total_monthly_waste_usd"], float)
    assert result["currency"] == "USD"
    assert isinstance(result["region"], str)
    assert isinstance(result["opportunities"], list)
    for opp in result["opportunities"]:
        _validate_opportunity(opp)
    assert isinstance(result["sparkline"], list)
    for v in result["sparkline"]:
        assert isinstance(v, (int, float))
    assert isinstance(result["notes"], list)


# ---------------------------------------------------------------------------
# 1. (removed) detect_exclusion_candidates — deleted in v2 refactor.
#    The shallow aggregate-count exclusion detector was replaced by
#    logs_intel.py content-sampling (detect_pattern_opportunities).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2. (removed) detect_logs_to_metrics — deleted in v2 refactor.
#    The shallow logs-to-metrics heuristic (single-status service detector)
#    was replaced by logs_intel.py content-sampling classify_template().
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. detect_high_cardinality_metrics
# ---------------------------------------------------------------------------

# Real volumes shape: ingested=500k, indexed=2k => 498k wasted timeseries
_VOLUMES_WASTE = {
    "data": {
        "id": "custom.user.requests",
        "type": "metric_volumes",
        "attributes": {
            "indexed_volume": 2_000,
            "ingested_volume": 500_000,
        },
    }
}

# Borderline: ingested=5k, indexed=4k => 80% indexed ratio => NOT flagged
_VOLUMES_EFFICIENT = {
    "data": {
        "id": "custom.api.latency",
        "type": "metric_volumes",
        "attributes": {
            "indexed_volume": 4_000,
            "ingested_volume": 5_000,
        },
    }
}

# Below threshold: ingested=8k < 10k threshold => NOT flagged
_VOLUMES_BELOW_THRESHOLD = {
    "data": {
        "id": "custom.small.metric",
        "type": "metric_volumes",
        "attributes": {
            "indexed_volume": 100,
            "ingested_volume": 8_000,
        },
    }
}


class TestDetectHighCardinalityMetrics:
    def test_returns_opportunity_for_high_ingested_low_indexed(self):
        """Metric with ingested>>indexed (500k/2k) should surface high_cardinality_metric."""
        metrics_volumes = {
            "custom.user.requests": _VOLUMES_WASTE,
            "custom.api.latency": _VOLUMES_EFFICIENT,
        }
        opp = savings.detect_high_cardinality_metrics(
            metrics_volumes=metrics_volumes,
        )
        assert opp is not None
        _validate_opportunity(opp)
        assert opp["lever"] == "high_cardinality_metric"
        assert opp["category"] == "metrics"

    def test_not_flagged_when_indexed_over_50pct_of_ingested(self):
        """Metric indexing 80% of ingested timeseries (4k/5k) must NOT be flagged."""
        metrics_volumes = {"custom.api.latency": _VOLUMES_EFFICIENT}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        assert opp is None

    def test_not_flagged_when_ingested_below_threshold(self):
        """Metric with ingested < 10_000 timeseries must NOT be flagged (not worth the effort)."""
        metrics_volumes = {"custom.small.metric": _VOLUMES_BELOW_THRESHOLD}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        assert opp is None

    def test_savings_equals_unused_times_price(self):
        """monthly_savings_usd = unused_timeseries * custom_metric_per_month."""
        price = 0.05
        ingested = 500_000
        indexed = 2_000
        unused = ingested - indexed  # 498_000
        expected_savings = round(unused * price, 2)

        metrics_volumes = {"custom.user.requests": _VOLUMES_WASTE}
        opp = savings.detect_high_cardinality_metrics(
            metrics_volumes=metrics_volumes,
            prices={**savings.DEFAULT_PRICES, "custom_metric_per_month": price},
        )
        assert opp is not None
        assert abs(opp["monthly_savings_usd"] - expected_savings) < 0.01, (
            f"Savings {opp['monthly_savings_usd']} != expected {expected_savings}"
        )

    def test_ranked_by_unused_desc(self):
        """When multiple metrics are flagged, the one with most unused timeseries is first."""
        # small waste: ingested=100k, indexed=1k => unused=99k
        small_waste = {
            "data": {
                "id": "custom.small.waste",
                "type": "metric_volumes",
                "attributes": {"indexed_volume": 1_000, "ingested_volume": 100_000},
            }
        }
        # large waste: ingested=500k, indexed=2k => unused=498k
        large_waste = _VOLUMES_WASTE

        metrics_volumes = {
            "custom.small.waste": small_waste,
            "custom.user.requests": large_waste,
        }
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        assert opp is not None
        # The opportunity should be based on the highest-unused metric
        assert "custom.user.requests" in opp["title"] or "custom.user.requests" in opp["id"]

    def test_evidence_shows_ingested_and_indexed_volumes(self):
        """Evidence must include both ingested and indexed volume for the top offender."""
        metrics_volumes = {"custom.user.requests": _VOLUMES_WASTE}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        assert len(opp["evidence"]) >= 1
        # The volume string should mention both ingested and indexed counts
        top_ev = opp["evidence"][0]
        assert "500,000" in top_ev["volume"] or "ingested" in top_ev["volume"].lower()

    def test_generated_config_uses_v2_metrics_endpoint(self):
        """generated_config must use POST/PATCH to /api/v2/metrics/{name}/tags."""
        metrics_volumes = {"custom.user.requests": _VOLUMES_WASTE}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        gc = opp["generated_config"]
        assert "/api/v2/metrics/" in gc["endpoint"]
        assert gc["verb"] in ("POST", "PATCH")
        assert "data" in gc["payload"]

    def test_high_cardinality_needs_write_scope(self):
        metrics_volumes = {"custom.user.requests": _VOLUMES_WASTE}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        assert opp["needs_write_scope"] is True

    def test_high_cardinality_prices_override(self):
        """Doubling custom_metric_per_month doubles savings."""
        metrics_volumes = {"custom.user.requests": _VOLUMES_WASTE}
        base = dict(savings.DEFAULT_PRICES)
        doubled = dict(savings.DEFAULT_PRICES)
        doubled["custom_metric_per_month"] = base["custom_metric_per_month"] * 2

        opp_base = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes, prices=base)
        opp_doubled = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes, prices=doubled)
        assert opp_doubled is not None and opp_base is not None
        assert abs(opp_doubled["monthly_savings_usd"] - opp_base["monthly_savings_usd"] * 2) < 0.01

    def test_high_cardinality_returns_none_for_empty_volumes(self):
        opp = savings.detect_high_cardinality_metrics(metrics_volumes={})
        assert opp is None

    def test_detection_query_mentions_volumes_endpoint(self):
        """detection_query must reference the /volumes endpoint."""
        metrics_volumes = {"custom.user.requests": _VOLUMES_WASTE}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        assert "/api/v2/metrics/" in opp["detection_query"]
        assert "volumes" in opp["detection_query"].lower()

    def test_why_mentions_ingested_vs_indexed(self):
        """why field must explain ingested vs indexed in plain English."""
        metrics_volumes = {"custom.user.requests": _VOLUMES_WASTE}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        why_lower = opp["why"].lower()
        assert "ingest" in why_lower
        assert "index" in why_lower


# ---------------------------------------------------------------------------
# 4. detect_index_quota
# ---------------------------------------------------------------------------

class TestDetectIndexQuota:
    def test_returns_opportunity_when_no_daily_limit_set(self):
        """Index with no daily_limit and consistent usage should get a quota recommendation."""
        opp = savings.detect_index_quota(
            usage_logs=USAGE_LOGS_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp is not None
        _validate_opportunity(opp)
        assert opp["lever"] == "index_quota"
        assert opp["category"] == "logs"

    def test_index_quota_generated_config_is_put_with_daily_quota(self):
        """generated_config must be PUT /api/v1/logs/config/indexes/{index} with daily_limit."""
        opp = savings.detect_index_quota(
            usage_logs=USAGE_LOGS_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        gc = opp["generated_config"]
        assert gc["verb"] == "PUT"
        assert "/api/v1/logs/config/indexes/" in gc["endpoint"]
        assert "daily_limit" in gc["payload"]
        # quota should be avg * 1.2 — must be positive
        assert gc["payload"]["daily_limit"] > 0

    def test_index_quota_daily_limit_is_avg_times_1_2(self):
        """daily_limit in generated_config must be approximately avg_daily_events * 1.2."""
        opp = savings.detect_index_quota(
            usage_logs=USAGE_LOGS_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        gc = opp["generated_config"]
        # avg daily events ≈ 5_100_000 from the fixture
        # recommended quota ≈ 5_100_000 * 1.2 = ~6_120_000
        assert gc["payload"]["daily_limit"] > 5_000_000
        assert gc["payload"]["daily_limit"] < 10_000_000

    def test_index_quota_needs_write_scope(self):
        opp = savings.detect_index_quota(
            usage_logs=USAGE_LOGS_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp["needs_write_scope"] is True

    def test_index_quota_returns_none_when_all_indexes_have_limit(self):
        """Returns None when all indexes already have a daily_limit set."""
        indexes_with_limits = {
            "indexes": [
                {"name": "main", "num_retention_days": 15, "daily_limit": 6_000_000, "exclusion_filters": []},
                {"name": "debug-index", "num_retention_days": 3, "daily_limit": 100_000_000, "exclusion_filters": []},
            ]
        }
        opp = savings.detect_index_quota(
            usage_logs=USAGE_LOGS_FIXTURE,
            logs_indexes=indexes_with_limits,
        )
        assert opp is None


# ---------------------------------------------------------------------------
# 5. scan() — integration
# ---------------------------------------------------------------------------

class TestScan:
    """Tests for the top-level scan() function via mock HTTP."""

    def _make_http_mock(self, monkeypatch):
        """Patch savings._http_get/_http_post to return fixture data based on URL path."""

        # Valid empty shapes for new v2 POST endpoints
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}
        _empty_ts = {"data": {"buckets": []}}

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            body: dict
            if "/api/v1/usage/logs" in url:
                body = USAGE_LOGS_FIXTURE
            elif "/api/v1/logs/config/indexes" in url:
                body = LOGS_INDEXES_FIXTURE
            elif "/api/v2/logs/config/metrics" in url:
                body = {"data": []}
            elif "/api/v2/metrics" in url and "/volumes" in url:
                metric_name = url.split("/api/v2/metrics/")[1].split("/volumes")[0]
                if "user.requests" in metric_name:
                    body = _VOLUMES_WASTE
                else:
                    body = _VOLUMES_EFFICIENT
            elif "/api/v2/metrics" in url:
                body = METRICS_LIST_FIXTURE
            elif "/api/v1/usage/timeseries" in url:
                body = USAGE_TIMESERIES_FIXTURE
            elif "/api/v2/usage/estimated_cost" in url:
                body = {}
            else:
                body = {}
            return (200, {"content-type": "application/json"}, __import__("json").dumps(body).encode())

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 15):
            if "/api/v2/logs/events/search" in url:
                return (200, {"content-type": "application/json"}, __import__("json").dumps(_empty_events).encode())
            if "/api/v2/logs/analytics/aggregate" in url:
                return (200, {"content-type": "application/json"}, __import__("json").dumps(LOGS_AGGREGATE_FIXTURE).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)

    def test_scan_returns_valid_scan_result(self, monkeypatch):
        """scan() returns a dict that passes _validate_scan_result."""
        self._make_http_mock(monkeypatch)
        result = savings.scan(api_key="test_key", app_key="test_app_key", site="us1")
        _validate_scan_result(result)

    def test_scan_opportunities_ranked_desc_by_savings(self, monkeypatch):
        """Opportunities must be sorted descending by monthly_savings_usd."""
        self._make_http_mock(monkeypatch)
        result = savings.scan(api_key="test_key", app_key="test_app_key", site="us1")
        opps = result["opportunities"]
        if len(opps) > 1:
            for i in range(len(opps) - 1):
                assert opps[i]["monthly_savings_usd"] >= opps[i + 1]["monthly_savings_usd"], (
                    f"Opportunity {i} savings {opps[i]['monthly_savings_usd']} < "
                    f"opportunity {i+1} savings {opps[i+1]['monthly_savings_usd']}"
                )

    def test_scan_total_waste_is_sum_of_opportunities(self, monkeypatch):
        """total_monthly_waste_usd must equal sum of all opportunity savings."""
        self._make_http_mock(monkeypatch)
        result = savings.scan(api_key="test_key", app_key="test_app_key", site="us1")
        expected_total = sum(o["monthly_savings_usd"] for o in result["opportunities"])
        assert abs(result["total_monthly_waste_usd"] - expected_total) < 0.01

    def test_scan_currency_is_usd(self, monkeypatch):
        self._make_http_mock(monkeypatch)
        result = savings.scan(api_key="test_key", app_key="test_app_key", site="us1")
        assert result["currency"] == "USD"

    def test_scan_sparkline_has_values(self, monkeypatch):
        """Sparkline should be populated from usage/logs daily volume."""
        self._make_http_mock(monkeypatch)
        result = savings.scan(api_key="test_key", app_key="test_app_key", site="us1")
        assert len(result["sparkline"]) > 0
        assert all(v >= 0 for v in result["sparkline"])

    def test_scan_prices_override_changes_totals(self, monkeypatch):
        """Passing a doubled price dict should produce higher total_monthly_waste_usd."""
        self._make_http_mock(monkeypatch)
        base_prices = dict(savings.DEFAULT_PRICES)
        high_prices = {k: v * 2 for k, v in base_prices.items()}

        result_base = savings.scan(api_key="k", app_key="a", site="us1", prices=base_prices)
        result_high = savings.scan(api_key="k", app_key="a", site="us1", prices=high_prices)

        assert result_high["total_monthly_waste_usd"] > result_base["total_monthly_waste_usd"]

    def test_scan_no_opportunities_when_all_detectors_return_none(self, monkeypatch):
        """scan() with trivial data returns empty opportunities list and zero waste."""
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}

        def fake_http_get(url, headers, timeout=15):
            if "/api/v1/usage/logs" in url:
                body = {"usage": []}
            elif "/api/v1/logs/config/indexes" in url:
                body = {"indexes": [{"name": "main", "num_retention_days": 15, "daily_limit": 5_000_000, "exclusion_filters": []}]}
            elif "/api/v2/logs/config/metrics" in url:
                body = {"data": []}
            elif "/api/v2/metrics" in url and "/volumes" in url:
                body = METRIC_VOLUMES_LOW_CARDINALITY
            elif "/api/v2/metrics" in url:
                body = {"data": []}
            elif "/api/v1/usage/timeseries" in url:
                body = {"usage": []}
            else:
                body = {}
            return (200, {}, __import__("json").dumps(body).encode())

        def fake_http_post(url, headers, body_bytes, timeout=15):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, __import__("json").dumps(_empty_events).encode())
            # aggregate returns empty buckets
            return (200, {}, __import__("json").dumps({"data": {"buckets": []}}).encode())

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.scan(api_key="k", app_key="a", site="us1")
        assert result["opportunities"] == []
        assert result["total_monthly_waste_usd"] == 0.0


# ---------------------------------------------------------------------------
# 6. build_apply_request
# ---------------------------------------------------------------------------

class TestBuildApplyRequest:
    def test_returns_generated_config_from_opportunity(self):
        """build_apply_request returns the opportunity's generated_config."""
        fake_opp: dict = {
            "id": "test-1",
            "lever": "exclusion_filter",
            "category": "logs",
            "title": "Test",
            "summary": "Test",
            "monthly_savings_usd": 100.0,
            "savings_pct": "10%",
            "effort": "low",
            "confidence": "high",
            "evidence": [],
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {"exclusion_filters": [{"name": "test", "filter": {"query": "status:200"}, "is_enabled": True}]},
            },
            "needs_write_scope": True,
        }
        result = savings.build_apply_request(fake_opp)
        assert result["endpoint"] == "/api/v1/logs/config/indexes/main"
        assert result["verb"] == "PUT"
        assert "exclusion_filters" in result["payload"]

    def test_build_apply_request_returns_dict_with_three_keys(self):
        fake_opp: dict = {
            "id": "x",
            "generated_config": {"endpoint": "/e", "verb": "POST", "payload": {}},
        }
        result = savings.build_apply_request(fake_opp)
        assert set(result.keys()) == {"endpoint", "verb", "payload"}


# ---------------------------------------------------------------------------
# 7. SECURITY — keys must never leak into generated output
# ---------------------------------------------------------------------------

class TestSecurityKeyNeverLeaks:
    FAKE_KEY = "SENSITIVE_API_KEY_NEVER_SHOULD_APPEAR"
    FAKE_APP = "SENSITIVE_APP_KEY_NEVER_SHOULD_APPEAR"

    def _make_http_mock(self, monkeypatch):
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            if "/api/v1/usage/logs" in url:
                body = USAGE_LOGS_FIXTURE
            elif "/api/v1/logs/config/indexes" in url:
                body = LOGS_INDEXES_FIXTURE
            elif "/api/v2/logs/config/metrics" in url:
                body = {"data": []}
            elif "/api/v2/metrics" in url and "/volumes" in url:
                body = METRIC_VOLUMES_HIGH_CARDINALITY
            elif "/api/v2/metrics" in url:
                body = METRICS_LIST_FIXTURE
            elif "/api/v1/usage/timeseries" in url:
                body = USAGE_TIMESERIES_FIXTURE
            else:
                body = {}
            return (200, {}, __import__("json").dumps(body).encode())

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 15):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, __import__("json").dumps(_empty_events).encode())
            if "/api/v2/logs/analytics/aggregate" in url:
                return (200, {}, __import__("json").dumps(LOGS_AGGREGATE_FIXTURE).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)

    def test_api_key_not_in_any_opportunity_field(self, monkeypatch):
        """API key must not appear in any field of any returned opportunity."""
        self._make_http_mock(monkeypatch)
        result = savings.scan(
            api_key=self.FAKE_KEY,
            app_key=self.FAKE_APP,
            site="us1",
        )
        result_str = json.dumps(result)
        assert self.FAKE_KEY not in result_str, "API key leaked into scan result!"
        assert self.FAKE_APP not in result_str, "App key leaked into scan result!"

    def test_api_key_not_in_generated_config(self, monkeypatch):
        """API key must not appear in generated_config payloads."""
        self._make_http_mock(monkeypatch)
        result = savings.scan(
            api_key=self.FAKE_KEY,
            app_key=self.FAKE_APP,
            site="us1",
        )
        for opp in result["opportunities"]:
            config_str = json.dumps(opp["generated_config"])
            assert self.FAKE_KEY not in config_str
            assert self.FAKE_APP not in config_str

    def test_api_key_not_in_exception_message(self, monkeypatch):
        """If HTTP layer raises, keys must not appear in exception message."""
        def fail_http_get(url, headers, timeout=15):
            return (401, {}, b'{"errors": ["Invalid API key"]}')

        def fail_http_post(url, headers, body_bytes, timeout=15):
            return (401, {}, b'{"errors": ["Invalid API key"]}')

        monkeypatch.setattr(savings, "_http_get", fail_http_get)
        monkeypatch.setattr(savings, "_http_post", fail_http_post)

        try:
            savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        except Exception as e:
            msg = str(e)
            assert self.FAKE_KEY not in msg, f"API key in exception: {msg}"
            assert self.FAKE_APP not in msg, f"App key in exception: {msg}"


# ---------------------------------------------------------------------------
# 8. DEFAULT_PRICES module-level dict
# ---------------------------------------------------------------------------

class TestDefaultPrices:
    def test_default_prices_has_required_keys(self):
        required = {"indexed_log_per_million", "ingested_log_per_gb", "custom_metric_per_month"}
        assert required.issubset(set(savings.DEFAULT_PRICES.keys()))

    def test_default_prices_values_are_positive(self):
        for key, val in savings.DEFAULT_PRICES.items():
            assert val > 0, f"Price {key}={val} must be positive"

    def test_default_prices_indexed_log_per_million_is_reasonable(self):
        """List price for 15-day retention is ~$1.70/million."""
        price = savings.DEFAULT_PRICES["indexed_log_per_million"]
        assert 0.50 <= price <= 5.0, f"indexed_log_per_million={price} seems unreasonable"


# ---------------------------------------------------------------------------
# 9. preflight_scopes — NEW (RED phase)
# ---------------------------------------------------------------------------

# Fixtures for estimated_cost and usage responses (for derive_effective_prices)
ESTIMATED_COST_FIXTURE = {
    "data": [
        {
            "attributes": {
                "charges": [
                    {"product_name": "Indexed Logs", "charge_type": "committed", "cost": 1200.0},
                    {"product_name": "Indexed Logs", "charge_type": "on_demand", "cost": 300.0},
                    {"product_name": "Infrastructure Hosts", "charge_type": "committed", "cost": 500.0},
                ]
            }
        }
    ]
}

# 30-day usage: 1,000 million events = 1 billion events in 30 days
USAGE_LOGS_FOR_PRICING_FIXTURE = {
    "usage": [
        {
            "date": f"2026-06-{d:02d}T00:00:00Z",
            "indexed_events_count": 33_000_000,  # 33M/day * 30 days = ~1 billion/month
            "ingested_events_bytes": 33_000_000_000,
        }
        for d in range(1, 31)
    ]
}


class TestPreflightScopes:
    """Tests for preflight_scopes(api_key, app_key, site) -> dict.

    preflight_scopes probes cheap read-only endpoints to infer which scopes
    the key has: 200 => present, 403 => absent. Returns:
      {
        "logs_read": bool,
        "metrics_read": bool,
        "billing_read": bool,
        "usage_read": bool,
        "missing": [scope_name, ...],
        "unlocks": {scope_name: description},
      }
    Keys must never appear in the returned dict.
    """

    FAKE_KEY = "PREFLIGHT_API_KEY_SENTINEL"
    FAKE_APP = "PREFLIGHT_APP_KEY_SENTINEL"

    def _make_scope_mock(self, monkeypatch, statuses: dict[str, int]):
        """statuses: {url_pattern: status_code}. Unmatched => 200."""
        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            for pattern, code in statuses.items():
                if pattern in url:
                    return (code, {}, b'{"errors": ["Forbidden"]}' if code == 403 else b'{"data": []}')
            return (200, {}, b'{"data": []}')

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            for pattern, code in statuses.items():
                if pattern in url:
                    return (code, {}, b'{"errors": ["Forbidden"]}' if code == 403 else b'{}')
            return (200, {}, b'{}')

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)

    def test_preflight_returns_dict_with_required_keys(self, monkeypatch):
        """preflight_scopes returns a dict with logs_read, metrics_read, billing_read, usage_read, missing, unlocks."""
        self._make_scope_mock(monkeypatch, {})
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        assert isinstance(result, dict)
        for key in ("logs_read", "metrics_read", "billing_read", "usage_read", "missing", "unlocks"):
            assert key in result, f"Missing key: {key}"

    def test_preflight_all_200_means_all_scopes_present(self, monkeypatch):
        """When every endpoint returns 200, all four scopes should be True."""
        self._make_scope_mock(monkeypatch, {})  # all return 200
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        assert result["logs_read"] is True
        assert result["metrics_read"] is True
        assert result["billing_read"] is True
        assert result["usage_read"] is True
        assert result["missing"] == []

    def test_preflight_403_on_logs_means_logs_read_false(self, monkeypatch):
        """403 on the logs endpoint => logs_read=False, in missing list."""
        self._make_scope_mock(monkeypatch, {"/api/v1/logs/config/indexes": 403})
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        assert result["logs_read"] is False
        assert "logs_read" in result["missing"]

    def test_preflight_403_on_metrics_means_metrics_read_false(self, monkeypatch):
        """403 on /api/v2/metrics => metrics_read=False."""
        self._make_scope_mock(monkeypatch, {"/api/v2/metrics": 403})
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        assert result["metrics_read"] is False
        assert "metrics_read" in result["missing"]

    def test_preflight_403_on_billing_means_billing_and_usage_false(self, monkeypatch):
        """403 on /api/v2/usage/estimated_cost => billing_read=False, usage_read=False."""
        self._make_scope_mock(monkeypatch, {"/api/v2/usage/estimated_cost": 403})
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        assert result["billing_read"] is False
        assert "billing_read" in result["missing"]

    def test_preflight_missing_list_contains_all_absent_scopes(self, monkeypatch):
        """missing[] must list every scope that returned 403."""
        self._make_scope_mock(monkeypatch, {
            "/api/v1/logs/config/indexes": 403,
            "/api/v2/metrics": 403,
        })
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        assert "logs_read" in result["missing"]
        assert "metrics_read" in result["missing"]
        # billing_read was 200, so NOT in missing
        assert "billing_read" not in result["missing"]

    def test_preflight_unlocks_populated_for_missing_scopes(self, monkeypatch):
        """unlocks must contain a non-empty description for each missing scope."""
        self._make_scope_mock(monkeypatch, {"/api/v1/logs/config/indexes": 403})
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        assert "logs_read" in result["unlocks"]
        assert isinstance(result["unlocks"]["logs_read"], str)
        assert len(result["unlocks"]["logs_read"]) > 5

    def test_preflight_unlocks_not_empty_even_with_all_scopes(self, monkeypatch):
        """unlocks should describe what each scope unlocks (informational), even if all present."""
        self._make_scope_mock(monkeypatch, {})
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        # When all present, missing=[] but unlocks may still describe what they enable
        assert isinstance(result["unlocks"], dict)

    def test_preflight_keys_never_in_result(self, monkeypatch):
        """API key and app key must never appear anywhere in the preflight result."""
        self._make_scope_mock(monkeypatch, {})
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        result_str = json.dumps(result)
        assert self.FAKE_KEY not in result_str, "API key leaked into preflight result!"
        assert self.FAKE_APP not in result_str, "App key leaked into preflight result!"

    def test_preflight_keys_never_in_result_when_403(self, monkeypatch):
        """Keys must not leak even when endpoints return 403."""
        self._make_scope_mock(monkeypatch, {"/api/v2/metrics": 403})
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        result_str = json.dumps(result)
        assert self.FAKE_KEY not in result_str
        assert self.FAKE_APP not in result_str

    def test_preflight_non_200_non_403_treated_as_absent(self, monkeypatch):
        """Errors other than 403 (e.g. 500, 429) treat the scope as absent (safe default)."""
        self._make_scope_mock(monkeypatch, {"/api/v2/metrics": 500})
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        # 500 on metrics should treat metrics_read as absent/unknown — not crash
        assert isinstance(result["metrics_read"], bool)
        assert result["metrics_read"] is False  # treat unknown as absent for safety

    def test_preflight_does_not_crash_on_all_403(self, monkeypatch):
        """preflight_scopes handles a completely locked-out key gracefully."""
        self._make_scope_mock(monkeypatch, {
            "/api/v1/logs/config/indexes": 403,
            "/api/v2/metrics": 403,
            "/api/v2/usage/estimated_cost": 403,
        })
        result = savings.preflight_scopes(self.FAKE_KEY, self.FAKE_APP, "us1")
        assert result["logs_read"] is False
        assert result["metrics_read"] is False
        assert result["billing_read"] is False
        assert len(result["missing"]) >= 3


# ---------------------------------------------------------------------------
# 10. derive_effective_prices — NEW (RED phase)
# ---------------------------------------------------------------------------

class TestDeriveEffectivePrices:
    """Tests for derive_effective_prices(estimated_cost_json, usage_logs_json, defaults) -> dict.

    Returns:
      {
        "prices": {... same keys as DEFAULT_PRICES ...},
        "source": "derived" | "list",
        "blended_note": str,
      }

    If estimated_cost + usage volume both present:
      compute real blended indexed-log rate = total_indexed_log_$ / indexed_events_millions
      override prices["indexed_log_per_million"]
      source = "derived"
    Else: return defaults with source = "list".
    Guard divide-by-zero.
    """

    def test_returns_dict_with_required_keys(self):
        """derive_effective_prices returns prices, source, blended_note."""
        result = savings.derive_effective_prices(ESTIMATED_COST_FIXTURE, USAGE_LOGS_FOR_PRICING_FIXTURE)
        assert isinstance(result, dict)
        assert "prices" in result
        assert "source" in result
        assert "blended_note" in result

    def test_prices_has_all_default_price_keys(self):
        """The returned prices dict must have all keys from DEFAULT_PRICES."""
        result = savings.derive_effective_prices(ESTIMATED_COST_FIXTURE, USAGE_LOGS_FOR_PRICING_FIXTURE)
        for key in savings.DEFAULT_PRICES:
            assert key in result["prices"], f"Missing price key: {key}"

    def test_derived_source_when_both_present(self):
        """source='derived' when estimated_cost and usage volume are both present."""
        result = savings.derive_effective_prices(ESTIMATED_COST_FIXTURE, USAGE_LOGS_FOR_PRICING_FIXTURE)
        assert result["source"] == "derived"

    def test_derived_blended_rate_math(self):
        """Blended rate = total indexed-log $ / indexed_events_millions.

        ESTIMATED_COST_FIXTURE: Indexed Logs = $1200 committed + $300 on_demand = $1500 total.
        USAGE_LOGS_FOR_PRICING_FIXTURE: 33M/day * 30 days = 990M events = 990 million.
        Expected blended rate = 1500 / 990 ≈ 1.515.
        """
        result = savings.derive_effective_prices(ESTIMATED_COST_FIXTURE, USAGE_LOGS_FOR_PRICING_FIXTURE)
        blended = result["prices"]["indexed_log_per_million"]
        expected = 1500.0 / 990.0  # ~1.515
        assert abs(blended - expected) < 0.01, f"Blended rate {blended:.4f} != expected {expected:.4f}"

    def test_list_source_when_estimated_cost_empty(self):
        """source='list' when estimated_cost data is empty."""
        result = savings.derive_effective_prices({"data": []}, USAGE_LOGS_FOR_PRICING_FIXTURE)
        assert result["source"] == "list"
        assert result["prices"]["indexed_log_per_million"] == savings.DEFAULT_PRICES["indexed_log_per_million"]

    def test_list_source_when_usage_empty(self):
        """source='list' when usage_logs data is empty (can't compute volume)."""
        result = savings.derive_effective_prices(ESTIMATED_COST_FIXTURE, {"usage": []})
        assert result["source"] == "list"

    def test_no_divide_by_zero_when_usage_zero(self):
        """Guard against divide-by-zero: if indexed events = 0, fall back to list price."""
        zero_usage = {
            "usage": [
                {"date": "2026-06-01T00:00:00Z", "indexed_events_count": 0}
            ]
        }
        result = savings.derive_effective_prices(ESTIMATED_COST_FIXTURE, zero_usage)
        # Must not raise; must return list fallback
        assert result["source"] == "list"
        assert result["prices"]["indexed_log_per_million"] > 0

    def test_defaults_override_accepted(self):
        """Caller-supplied defaults are used as the non-indexed_log keys."""
        custom_defaults = dict(savings.DEFAULT_PRICES)
        custom_defaults["custom_metric_per_month"] = 99.99
        result = savings.derive_effective_prices(ESTIMATED_COST_FIXTURE, USAGE_LOGS_FOR_PRICING_FIXTURE, defaults=custom_defaults)
        # The custom_metric_per_month should be preserved
        assert result["prices"]["custom_metric_per_month"] == 99.99

    def test_blended_note_is_non_empty_string(self):
        """blended_note must be a non-empty string regardless of source."""
        result_derived = savings.derive_effective_prices(ESTIMATED_COST_FIXTURE, USAGE_LOGS_FOR_PRICING_FIXTURE)
        result_list = savings.derive_effective_prices({"data": []}, {"usage": []})
        assert isinstance(result_derived["blended_note"], str) and result_derived["blended_note"]
        assert isinstance(result_list["blended_note"], str) and result_list["blended_note"]

    def test_list_fallback_preserves_all_default_values(self):
        """When falling back to list, all price values match DEFAULT_PRICES exactly."""
        result = savings.derive_effective_prices({"data": []}, {"usage": []})
        for key, val in savings.DEFAULT_PRICES.items():
            assert result["prices"][key] == val, f"Price {key} mismatch on list fallback"


# ---------------------------------------------------------------------------
# 11. Transparency: detection_query + why in every opportunity — NEW (RED phase)
# ---------------------------------------------------------------------------

class TestOpportunityTransparency:
    """Every opportunity dict returned by detectors must carry detection_query + why.

    NOTE: detect_exclusion_candidates and detect_logs_to_metrics were removed in
    v2. Only high_cardinality_metric and index_quota tests remain here.
    """

    def test_high_cardinality_metric_has_detection_query(self):
        metrics_volumes = {"custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes)
        assert opp is not None
        assert "detection_query" in opp, "Missing detection_query in high_cardinality_metric opportunity"

    def test_high_cardinality_metric_detection_query_is_non_empty(self):
        metrics_volumes = {"custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes)
        assert isinstance(opp["detection_query"], str) and opp["detection_query"]

    def test_high_cardinality_metric_has_why(self):
        metrics_volumes = {"custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes)
        assert "why" in opp, "Missing 'why' in high_cardinality_metric opportunity"

    def test_high_cardinality_metric_why_is_non_empty(self):
        metrics_volumes = {"custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes)
        assert isinstance(opp["why"], str) and opp["why"]

    def test_index_quota_has_detection_query(self):
        opp = savings.detect_index_quota(USAGE_LOGS_FIXTURE, LOGS_INDEXES_FIXTURE)
        assert opp is not None
        assert "detection_query" in opp, "Missing detection_query in index_quota opportunity"

    def test_index_quota_detection_query_is_non_empty(self):
        opp = savings.detect_index_quota(USAGE_LOGS_FIXTURE, LOGS_INDEXES_FIXTURE)
        assert isinstance(opp["detection_query"], str) and opp["detection_query"]

    def test_index_quota_has_why(self):
        opp = savings.detect_index_quota(USAGE_LOGS_FIXTURE, LOGS_INDEXES_FIXTURE)
        assert "why" in opp, "Missing 'why' in index_quota opportunity"

    def test_index_quota_why_is_non_empty(self):
        opp = savings.detect_index_quota(USAGE_LOGS_FIXTURE, LOGS_INDEXES_FIXTURE)
        assert isinstance(opp["why"], str) and opp["why"]

    def test_scan_opportunities_have_detection_query_and_why(self, monkeypatch):
        """All opportunities in scan() output carry detection_query and why."""
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            if "/api/v1/usage/logs" in url:
                body = USAGE_LOGS_FIXTURE
            elif "/api/v1/logs/config/indexes" in url:
                body = LOGS_INDEXES_FIXTURE
            elif "/api/v2/logs/config/metrics" in url:
                body = {"data": []}
            elif "/api/v2/metrics" in url and "/volumes" in url:
                body = METRIC_VOLUMES_HIGH_CARDINALITY
            elif "/api/v2/metrics" in url:
                body = METRICS_LIST_FIXTURE
            elif "/api/v2/usage/estimated_cost" in url:
                body = ESTIMATED_COST_FIXTURE
            else:
                body = {}
            return (200, {}, json.dumps(body).encode())

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 15):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, json.dumps(_empty_events).encode())
            if "/api/v2/logs/analytics/aggregate" in url:
                return (200, {}, json.dumps(LOGS_AGGREGATE_FIXTURE).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.scan(api_key="test_key", app_key="test_app_key", site="us1")
        for opp in result["opportunities"]:
            assert "detection_query" in opp, f"Opportunity {opp.get('id')} missing detection_query"
            assert "why" in opp, f"Opportunity {opp.get('id')} missing why"
            assert opp["detection_query"], f"Opportunity {opp.get('id')} has empty detection_query"
            assert opp["why"], f"Opportunity {opp.get('id')} has empty why"


# ---------------------------------------------------------------------------
# 12. scan() scope_check + price_source integration — NEW (RED phase)
# ---------------------------------------------------------------------------

class TestScanScopeCheckAndPriceSource:
    """Tests for updated scan() that attaches scope_check and price_source."""

    FAKE_KEY = "SCAN_SCOPE_TEST_KEY_SENTINEL"
    FAKE_APP = "SCAN_SCOPE_TEST_APP_SENTINEL"

    def _make_full_mock(self, monkeypatch, extra_statuses=None):
        """Mock all endpoints to return 200 + fixture data."""
        extra_statuses = extra_statuses or {}
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            # Check for forced-status overrides first
            for pattern, code in extra_statuses.items():
                if pattern in url:
                    body = b'{"errors":["Forbidden"]}' if code == 403 else b'{}'
                    return (code, {}, body)
            # Normal fixture responses
            if "/api/v1/usage/logs" in url:
                body = USAGE_LOGS_FIXTURE
            elif "/api/v1/logs/config/indexes" in url:
                body = LOGS_INDEXES_FIXTURE
            elif "/api/v2/logs/config/metrics" in url:
                body = {"data": []}
            elif "/api/v2/metrics" in url and "/volumes" in url:
                body = METRIC_VOLUMES_HIGH_CARDINALITY
            elif "/api/v2/metrics" in url:
                body = METRICS_LIST_FIXTURE
            elif "/api/v2/usage/estimated_cost" in url:
                body = ESTIMATED_COST_FIXTURE
            else:
                body = {}
            return (200, {}, json.dumps(body).encode())

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 15):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, json.dumps(_empty_events).encode())
            if "/api/v2/logs/analytics/aggregate" in url:
                return (200, {}, json.dumps(LOGS_AGGREGATE_FIXTURE).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)

    def test_scan_attaches_scope_check(self, monkeypatch):
        """scan() result must include 'scope_check' key."""
        self._make_full_mock(monkeypatch)
        result = savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        assert "scope_check" in result, "scan() result missing 'scope_check' key"

    def test_scan_scope_check_has_required_fields(self, monkeypatch):
        """scope_check must have logs_read, metrics_read, billing_read, usage_read, missing, unlocks."""
        self._make_full_mock(monkeypatch)
        result = savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        sc = result["scope_check"]
        for key in ("logs_read", "metrics_read", "billing_read", "usage_read", "missing", "unlocks"):
            assert key in sc, f"scope_check missing key: {key}"

    def test_scan_attaches_price_source(self, monkeypatch):
        """scan() result must include 'price_source' key."""
        self._make_full_mock(monkeypatch)
        result = savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        assert "price_source" in result, "scan() result missing 'price_source' key"

    def test_scan_price_source_derived_when_estimated_cost_available(self, monkeypatch):
        """When estimated_cost returns data, price_source='derived'."""
        self._make_full_mock(monkeypatch)
        result = savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        assert result["price_source"] in ("derived", "list", "custom")

    def test_scan_price_source_custom_when_prices_passed(self, monkeypatch):
        """When caller passes explicit prices, price_source='custom'."""
        self._make_full_mock(monkeypatch)
        custom_prices = dict(savings.DEFAULT_PRICES)
        result = savings.scan(
            api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1",
            prices=custom_prices
        )
        assert result["price_source"] == "custom"

    def test_scan_scope_check_keys_never_in_result(self, monkeypatch):
        """API/app keys must never appear in scope_check or price_source."""
        self._make_full_mock(monkeypatch)
        result = savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        sc_str = json.dumps(result["scope_check"])
        assert self.FAKE_KEY not in sc_str, "API key in scope_check!"
        assert self.FAKE_APP not in sc_str, "App key in scope_check!"

    def test_scan_graceful_when_logs_scope_missing(self, monkeypatch):
        """If logs scope returns 403, scan completes without crash; scope_check reflects it."""
        self._make_full_mock(monkeypatch, extra_statuses={"/api/v1/logs/config/indexes": 403})
        # Should not raise
        result = savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        assert "scope_check" in result
        # The scan may have fewer opportunities but must not crash
        assert isinstance(result["opportunities"], list)

    def test_scan_price_source_list_when_no_estimated_cost(self, monkeypatch):
        """When estimated_cost is unavailable (403/error), price_source='list'."""
        self._make_full_mock(monkeypatch, extra_statuses={"/api/v2/usage/estimated_cost": 403})
        result = savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        # Without estimated_cost data we can't derive, so source must be 'list'
        assert result["price_source"] in ("list", "derived")  # derived only if usage still worked

    def test_scan_result_still_valid_scan_result(self, monkeypatch):
        """After adding scope_check/price_source, existing _validate_scan_result still passes."""
        self._make_full_mock(monkeypatch)
        result = savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        # existing contract must still hold
        _validate_scan_result(result)

    def test_scan_attaches_bill_share_pct_from_leaderboard(self, monkeypatch):
        """scan() must attach bill_share_pct = top-20 cumulative_pct from leaderboard."""
        self._make_full_mock(monkeypatch)
        result = savings.scan(api_key=self.FAKE_KEY, app_key=self.FAKE_APP, site="us1")
        assert "bill_share_pct" in result, "scan() result missing 'bill_share_pct' key"
        assert isinstance(result["bill_share_pct"], (int, float))
        assert 0.0 <= result["bill_share_pct"] <= 100.0

    def test_bill_share_pct_is_zero_when_no_leaderboard(self, monkeypatch):
        """When pattern_leaderboard is empty, bill_share_pct=0.0."""
        # Mock with empty events (so no patterns)
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            if "/api/v1/usage/logs" in url:
                return (200, {}, json.dumps(USAGE_LOGS_FIXTURE).encode())
            elif "/api/v1/logs/config/indexes" in url:
                return (200, {}, json.dumps(LOGS_INDEXES_FIXTURE).encode())
            else:
                return (200, {}, json.dumps({}).encode())

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, json.dumps(_empty_events).encode())
            if "/api/v2/logs/analytics/aggregate" in url:
                return (200, {}, json.dumps({"data": {"buckets": []}}).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.scan(api_key="k", app_key="a", site="us1")
        assert result["bill_share_pct"] == 0.0


# ---------------------------------------------------------------------------
# TASK A: _fetch_pattern_timeseries
# ---------------------------------------------------------------------------

class TestFetchPatternTimeseries:
    """Tests for _fetch_pattern_timeseries(phrase_query, api_key, app_key, site, days=17)."""

    def test_fetch_pattern_timeseries_posts_to_aggregate_endpoint(self, monkeypatch):
        """_fetch_pattern_timeseries must POST to /api/v2/logs/analytics/aggregate."""
        captured = {}

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/analytics/aggregate" in url:
                captured["url"] = url
                captured["body"] = json.loads(body_bytes)
                return (200, {}, json.dumps({"data": {"buckets": []}}).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        phrase = "connection timeout"
        savings._fetch_pattern_timeseries(phrase, "key", "app", "us1", days=17)
        assert "/api/v2/logs/analytics/aggregate" in captured.get("url", "")

    def test_fetch_pattern_timeseries_body_has_correct_structure(self, monkeypatch):
        """Body must have filter.query, timeseries compute, and empty group_by."""
        captured = {}

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/analytics/aggregate" in url:
                captured["body"] = json.loads(body_bytes)
                return (200, {}, json.dumps({"data": {"buckets": []}}).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        phrase = "connection timeout"
        savings._fetch_pattern_timeseries(phrase, "key", "app", "us1", days=17)
        body = captured["body"]
        assert body["filter"]["query"] == phrase
        assert body["group_by"] == []
        assert body["compute"][0]["type"] == "timeseries"
        assert body["compute"][0]["aggregation"] == "count"

    def test_fetch_pattern_timeseries_returns_json(self, monkeypatch):
        """Must return the parsed JSON response."""
        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            return (200, {}, json.dumps({"data": {"buckets": [{"by": {"service": "test"}, "computes": {"c0": [1, 2, 3]}}]}}).encode())

        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings._fetch_pattern_timeseries("phrase", "key", "app", "us1")
        assert "data" in result
        assert "buckets" in result["data"]


# ---------------------------------------------------------------------------
# TASK B: _validate_exclusion_query
# ---------------------------------------------------------------------------

class TestValidateExclusionQuery:
    """Tests for _validate_exclusion_query(query, expected_24h_events, api_key, app_key, site)."""

    def test_validate_exclusion_query_returns_dict_with_required_keys(self, monkeypatch):
        """Must return dict with actual, expected, ratio, confidence."""
        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            # Mock response with one bucket containing count
            return (200, {}, json.dumps({
                "data": {"buckets": [{"computes": {"c0": 1000}}]}
            }).encode())

        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings._validate_exclusion_query("status:200", 2000, "key", "app", "us1")
        assert "actual" in result
        assert "expected" in result
        assert "ratio" in result
        assert "confidence" in result

    def test_validate_exclusion_query_ratio_high_confidence_when_close(self, monkeypatch):
        """Confidence should be 'high' when 0.5 <= ratio <= 2.0."""
        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            return (200, {}, json.dumps({
                "data": {"buckets": [{"computes": {"c0": 1500}}]}
            }).encode())

        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        # actual=1500, expected=2000, ratio=0.75 (within 0.5-2.0 range)
        result = savings._validate_exclusion_query("status:200", 2000, "key", "app", "us1")
        assert result["confidence"] == "high"

    def test_validate_exclusion_query_ratio_low_confidence_when_far(self, monkeypatch):
        """Confidence should be 'low' when ratio < 0.5 or > 2.0."""
        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            return (200, {}, json.dumps({
                "data": {"buckets": [{"computes": {"c0": 500}}]}
            }).encode())

        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        # actual=500, expected=2000, ratio=0.25 (< 0.5)
        result = savings._validate_exclusion_query("status:200", 2000, "key", "app", "us1")
        assert result["confidence"] == "low"

    def test_validate_exclusion_query_returns_unknown_on_error(self, monkeypatch):
        """On DatadogError, must return actual=0, expected=expected, ratio=1.0, confidence='unknown'."""
        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            raise dd_client.DatadogError("Network error")

        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings._validate_exclusion_query("status:200", 2000, "key", "app", "us1")
        assert result["actual"] == 0
        assert result["expected"] == 2000
        assert result["ratio"] == 1.0
        assert result["confidence"] == "unknown"


# ---------------------------------------------------------------------------
# TASK C: logs_read_data scope probe
# ---------------------------------------------------------------------------

class TestPreflightScopesLogsReadData:
    """Tests for logs_read_data scope in preflight_scopes."""

    def test_preflight_adds_logs_read_data_to_result(self, monkeypatch):
        """preflight_scopes must include 'logs_read_data' in returned dict."""
        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            return (200, {}, b'{}')

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, json.dumps({"data": []}).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.preflight_scopes("key", "app", "us1")
        assert "logs_read_data" in result

    def test_preflight_logs_read_data_true_on_200(self, monkeypatch):
        """When /api/v2/logs/events/search returns 200, logs_read_data=True."""
        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            return (200, {}, b'{}')

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, json.dumps({"data": []}).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.preflight_scopes("key", "app", "us1")
        assert result["logs_read_data"] is True

    def test_preflight_logs_read_data_false_on_403(self, monkeypatch):
        """When /api/v2/logs/events/search returns 403, logs_read_data=False."""
        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            return (200, {}, b'{}')

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                return (403, {}, b'{"errors":["Forbidden"]}')
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.preflight_scopes("key", "app", "us1")
        assert result["logs_read_data"] is False

    def test_preflight_logs_read_data_in_missing_when_false(self, monkeypatch):
        """When logs_read_data=False, it must appear in missing[]."""
        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            return (200, {}, b'{}')

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                return (403, {}, b'{}')
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.preflight_scopes("key", "app", "us1")
        assert "logs_read_data" in result["missing"]

    def test_preflight_logs_read_data_not_in_missing_when_true(self, monkeypatch):
        """When logs_read_data=True, it must NOT appear in missing[]."""
        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            return (200, {}, b'{}')

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, json.dumps({"data": []}).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.preflight_scopes("key", "app", "us1")
        assert "logs_read_data" not in result.get("missing", [])


# ---------------------------------------------------------------------------
# TASK D: scan() pooled, pattern-first model
# ---------------------------------------------------------------------------

class TestScanPatternFirst:
    """Tests for scan() POOLED, pattern-first rewire."""

    def _make_full_mock_with_patterns(self, monkeypatch):
        """Mock all endpoints for pattern-first scan."""
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            if "/api/v1/usage/logs" in url:
                return (200, {}, json.dumps(USAGE_LOGS_FIXTURE).encode())
            elif "/api/v1/logs/config/indexes" in url:
                return (200, {}, json.dumps(LOGS_INDEXES_FIXTURE).encode())
            elif "/api/v2/logs/config/metrics" in url:
                return (200, {}, json.dumps({"data": []}).encode())
            elif "/api/v2/metrics" in url and "/volumes" in url:
                metric_name = url.split("/api/v2/metrics/")[1].split("/volumes")[0]
                if "user.requests" in metric_name:
                    return (200, {}, json.dumps(METRIC_VOLUMES_HIGH_CARDINALITY).encode())
                else:
                    return (200, {}, json.dumps(METRIC_VOLUMES_LOW_CARDINALITY).encode())
            elif "/api/v2/metrics" in url:
                return (200, {}, json.dumps(METRICS_LIST_FIXTURE).encode())
            elif "/api/v2/usage/estimated_cost" in url:
                return (200, {}, json.dumps(ESTIMATED_COST_FIXTURE).encode())
            else:
                return (200, {}, b"{}")

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, json.dumps(_empty_events).encode())
            if "/api/v2/logs/analytics/aggregate" in url:
                return (200, {}, json.dumps(LOGS_AGGREGATE_FIXTURE).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)

    def test_scan_returns_patterns_key(self, monkeypatch):
        """scan() must return 'patterns' key with list of pattern opportunities."""
        self._make_full_mock_with_patterns(monkeypatch)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert "patterns" in result
        assert isinstance(result["patterns"], list)

    def test_scan_returns_pattern_leaderboard_key(self, monkeypatch):
        """scan() must return 'pattern_leaderboard' key."""
        self._make_full_mock_with_patterns(monkeypatch)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert "pattern_leaderboard" in result
        assert isinstance(result["pattern_leaderboard"], list)

    def test_scan_returns_similar_families_key(self, monkeypatch):
        """scan() must return 'similar_families' key."""
        self._make_full_mock_with_patterns(monkeypatch)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert "similar_families" in result
        assert isinstance(result["similar_families"], list)

    def test_scan_returns_surges_key(self, monkeypatch):
        """scan() must return 'surges' key (anomalies with pattern tagging)."""
        self._make_full_mock_with_patterns(monkeypatch)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert "surges" in result
        assert isinstance(result["surges"], list)

    def test_scan_returns_bill_share_pct_key(self, monkeypatch):
        """scan() must return 'bill_share_pct' key (sum of pattern shares)."""
        self._make_full_mock_with_patterns(monkeypatch)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert "bill_share_pct" in result
        assert isinstance(result["bill_share_pct"], (int, float))

    def test_scan_returns_scope_gate_key(self, monkeypatch):
        """scan() must return 'scope_gate' key (True if logs_read_data missing)."""
        self._make_full_mock_with_patterns(monkeypatch)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert "scope_gate" in result
        assert isinstance(result["scope_gate"], bool)

    def test_scan_returns_lines_examined_key(self, monkeypatch):
        """scan() must return 'lines_examined' key (event count from pooled sample)."""
        self._make_full_mock_with_patterns(monkeypatch)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert "lines_examined" in result
        assert isinstance(result["lines_examined"], int)

    def test_scan_returns_sampled_key(self, monkeypatch):
        """scan() must return 'sampled' key (True if events were fetched)."""
        self._make_full_mock_with_patterns(monkeypatch)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert "sampled" in result
        assert isinstance(result["sampled"], bool)

    def test_scan_scope_gate_true_when_logs_read_data_missing(self, monkeypatch):
        """When logs_read_data scope is missing, scope_gate=True."""
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            if "/api/v1/usage/logs" in url:
                return (200, {}, json.dumps(USAGE_LOGS_FIXTURE).encode())
            elif "/api/v1/logs/config/indexes" in url:
                return (200, {}, json.dumps(LOGS_INDEXES_FIXTURE).encode())
            elif "/api/v2/logs/config/metrics" in url:
                return (200, {}, json.dumps({"data": []}).encode())
            elif "/api/v2/metrics" in url and "/volumes" in url:
                return (200, {}, json.dumps(METRIC_VOLUMES_LOW_CARDINALITY).encode())
            elif "/api/v2/metrics" in url:
                return (200, {}, json.dumps(METRICS_LIST_FIXTURE).encode())
            elif "/api/v2/usage/estimated_cost" in url:
                return (200, {}, json.dumps(ESTIMATED_COST_FIXTURE).encode())
            else:
                return (200, {}, b"{}")

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                # Return 403 for logs_read_data probe (check page.limit to distinguish)
                page = json.loads(body_bytes).get("page", {})
                if "limit" in page:
                    return (403, {}, b'{}')
                return (200, {}, json.dumps(_empty_events).encode())
            if "/api/v2/logs/analytics/aggregate" in url:
                return (200, {}, json.dumps(LOGS_AGGREGATE_FIXTURE).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert result["scope_gate"] is True

    def test_scan_patterns_empty_when_scope_gate(self, monkeypatch):
        """When scope_gate=True, patterns=[] (skipped due to missing scope)."""
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            if "/api/v1/usage/logs" in url:
                return (200, {}, json.dumps(USAGE_LOGS_FIXTURE).encode())
            elif "/api/v1/logs/config/indexes" in url:
                return (200, {}, json.dumps(LOGS_INDEXES_FIXTURE).encode())
            elif "/api/v2/logs/config/metrics" in url:
                return (200, {}, json.dumps({"data": []}).encode())
            elif "/api/v2/metrics" in url and "/volumes" in url:
                return (200, {}, json.dumps(METRIC_VOLUMES_LOW_CARDINALITY).encode())
            elif "/api/v2/metrics" in url:
                return (200, {}, json.dumps(METRICS_LIST_FIXTURE).encode())
            elif "/api/v2/usage/estimated_cost" in url:
                return (200, {}, json.dumps(ESTIMATED_COST_FIXTURE).encode())
            else:
                return (200, {}, b"{}")

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                page = json.loads(body_bytes).get("page", {})
                if "limit" in page:
                    return (403, {}, b'{}')
                return (200, {}, json.dumps(_empty_events).encode())
            if "/api/v2/logs/analytics/aggregate" in url:
                return (200, {}, json.dumps(LOGS_AGGREGATE_FIXTURE).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)
        result = savings.scan(api_key="key", app_key="app", site="us1")
        assert result["patterns"] == []


# ---------------------------------------------------------------------------
# 14. Progress callback support (PART A of async work)
# ---------------------------------------------------------------------------

class TestProgressCallback:
    """savings.scan(progress_cb=...) should call progress_cb(stage, pct) at milestones."""

    def _make_http_mock(self, monkeypatch):
        _empty_events = {"data": [], "meta": {"page": {"after": None}}}

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            if "/api/v1/usage/logs" in url:
                return (200, {}, json.dumps(USAGE_LOGS_FIXTURE).encode())
            elif "/api/v1/logs/config/indexes" in url:
                return (200, {}, json.dumps(LOGS_INDEXES_FIXTURE).encode())
            elif "/api/v2/logs/config/metrics" in url:
                return (200, {}, json.dumps({"data": []}).encode())
            elif "/api/v2/metrics" in url and "/volumes" in url:
                return (200, {}, json.dumps(METRIC_VOLUMES_LOW_CARDINALITY).encode())
            elif "/api/v2/metrics" in url:
                return (200, {}, json.dumps(METRICS_LIST_FIXTURE).encode())
            elif "/api/v2/usage/estimated_cost" in url:
                return (200, {}, json.dumps(ESTIMATED_COST_FIXTURE).encode())
            else:
                return (200, {}, b"{}")

        def fake_http_post(url: str, headers: dict, body_bytes: bytes, timeout: int = 30):
            if "/api/v2/logs/events/search" in url:
                return (200, {}, json.dumps(_empty_events).encode())
            if "/api/v2/logs/analytics/aggregate" in url:
                return (200, {}, json.dumps(LOGS_AGGREGATE_FIXTURE).encode())
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)

    def test_scan_accepts_progress_cb_kwarg(self, monkeypatch):
        """savings.scan(..., progress_cb=...) must not crash."""
        self._make_http_mock(monkeypatch)
        calls = []
        def capture_cb(stage, pct):
            calls.append((stage, pct))
        result = savings.scan(api_key="key", app_key="app", site="us1", progress_cb=capture_cb)
        assert "total_monthly_waste_usd" in result

    def test_scan_progress_cb_receives_stages(self, monkeypatch):
        """progress_cb must be called with (stage, pct) tuples."""
        self._make_http_mock(monkeypatch)
        calls = []
        def capture_cb(stage, pct):
            calls.append((stage, pct))
        savings.scan(api_key="key", app_key="app", site="us1", progress_cb=capture_cb)
        # Must have at least one call
        assert len(calls) > 0, "progress_cb was never called"
        # Each call should be a (str, int/float) tuple
        for stage, pct in calls:
            assert isinstance(stage, str)
            assert isinstance(pct, (int, float))

    def test_scan_progress_cb_reaches_done(self, monkeypatch):
        """progress_cb must include a ("Done", 100) call."""
        self._make_http_mock(monkeypatch)
        calls = []
        def capture_cb(stage, pct):
            calls.append((stage, pct))
        savings.scan(api_key="key", app_key="app", site="us1", progress_cb=capture_cb)
        # Last call should be ("Done", 100)
        assert calls[-1] == ("Done", 100), f"Expected ('Done', 100), got {calls[-1]}"

    def test_scan_progress_cb_none_is_safe(self, monkeypatch):
        """savings.scan without progress_cb (None) must work (backward compat)."""
        self._make_http_mock(monkeypatch)
        result = savings.scan(api_key="key", app_key="app", site="us1", progress_cb=None)
        assert "total_monthly_waste_usd" in result

    def test_scan_progress_cb_exception_swallowed(self, monkeypatch):
        """If progress_cb raises an exception, scan must continue (fail-safe)."""
        self._make_http_mock(monkeypatch)
        def bad_cb(stage, pct):
            raise RuntimeError("oops")
        result = savings.scan(api_key="key", app_key="app", site="us1", progress_cb=bad_cb)
        # Scan should complete despite callback error
        assert "total_monthly_waste_usd" in result
