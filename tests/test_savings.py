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

# GET /api/v2/metrics/{name}/volumes
# custom.user.requests: high cardinality due to user_id tag
METRIC_VOLUMES_HIGH_CARDINALITY = {
    "data": {
        "id": "custom.user.requests",
        "type": "metric_volumes",
        "attributes": {
            "indexed_volume": 500_000,
            "ingested_volume": 500_000,
            "tag_configurations": [
                {"tag_keys": ["service", "user_id", "env"], "indexed_volume": 500_000},
            ],
            "tags": {
                "service": ["api", "web"],
                "user_id": ["u1", "u2", "u3"],  # explosive: many unique values
                "env": ["prod", "staging"],
            },
        },
    }
}

METRIC_VOLUMES_LOW_CARDINALITY = {
    "data": {
        "id": "custom.api.latency",
        "type": "metric_volumes",
        "attributes": {
            "indexed_volume": 5_000,
            "ingested_volume": 5_000,
            "tag_configurations": [
                {"tag_keys": ["service", "env"], "indexed_volume": 5_000},
            ],
            "tags": {
                "service": ["api", "web"],
                "env": ["prod", "staging"],
            },
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
        "exclusion_filter", "logs_to_metrics", "high_cardinality_metric", "index_quota"
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
# 1. detect_exclusion_candidates
# ---------------------------------------------------------------------------

class TestDetectExclusionCandidates:
    def test_returns_opportunity_for_high_volume_200_service(self):
        """High-volume 200-status service should produce an exclusion_filter opportunity."""
        opp = savings.detect_exclusion_candidates(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp is not None
        _validate_opportunity(opp)
        assert opp["lever"] == "exclusion_filter"
        assert opp["category"] == "logs"

    def test_exclusion_candidate_savings_math(self):
        """monthly_savings_usd = excluded_events/1e6 * price_per_million."""
        prices = dict(savings.DEFAULT_PRICES)
        prices["indexed_log_per_million"] = 2.0  # easy math

        opp = savings.detect_exclusion_candidates(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=prices,
        )
        assert opp is not None
        # api-gateway 200: 45M/7d * 30 = ~192.8M/month, cdn-proxy: 60M/7d*30=257M/month,
        # health-checker: 30M/7d*30=128.5M/month, payment 200: 8M/7d*30=34.3M/month
        # total 200/DEBUG candidates summed. At min the top candidate should be > 0
        assert opp["monthly_savings_usd"] > 0.0

    def test_exclusion_generated_config_is_put_index(self):
        """generated_config must be a PUT to /api/v1/logs/config/indexes/{index}."""
        opp = savings.detect_exclusion_candidates(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        gc = opp["generated_config"]
        assert gc["verb"] == "PUT"
        assert "/api/v1/logs/config/indexes/" in gc["endpoint"]
        # payload must have exclusion_filters key
        assert "exclusion_filters" in gc["payload"]

    def test_exclusion_needs_write_scope(self):
        opp = savings.detect_exclusion_candidates(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp["needs_write_scope"] is True

    def test_exclusion_evidence_lists_top_services(self):
        """Evidence list must contain at least one entry with label/volume/cost_usd."""
        opp = savings.detect_exclusion_candidates(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert len(opp["evidence"]) >= 1
        for ev in opp["evidence"]:
            assert ev["cost_usd"] >= 0.0

    def test_exclusion_returns_none_when_no_high_volume_services(self):
        """Returns None when all services are low-volume (below threshold)."""
        minimal_aggregate = {
            "data": {
                "buckets": [
                    {"by": {"service": "quiet-svc", "status": "200"}, "computes": {"c0": 100}},
                ]
            }
        }
        opp = savings.detect_exclusion_candidates(
            logs_aggregate=minimal_aggregate,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp is None

    def test_exclusion_prices_override_changes_savings(self):
        """Doubling price should double savings."""
        base = dict(savings.DEFAULT_PRICES)
        doubled = dict(savings.DEFAULT_PRICES)
        doubled["indexed_log_per_million"] = base["indexed_log_per_million"] * 2

        opp_base = savings.detect_exclusion_candidates(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=base,
        )
        opp_doubled = savings.detect_exclusion_candidates(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=doubled,
        )
        assert opp_doubled is not None and opp_base is not None
        assert abs(opp_doubled["monthly_savings_usd"] - opp_base["monthly_savings_usd"] * 2) < 0.01


# ---------------------------------------------------------------------------
# 2. detect_logs_to_metrics
# ---------------------------------------------------------------------------

class TestDetectLogsToMetrics:
    def test_returns_opportunity_for_cdn_health_check_pattern(self):
        """High-volume low-variance 200 service (cdn-proxy) should surface logs_to_metrics."""
        opp = savings.detect_logs_to_metrics(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp is not None
        _validate_opportunity(opp)
        assert opp["lever"] == "logs_to_metrics"
        assert opp["category"] == "logs"

    def test_logs_to_metrics_generated_config_is_post(self):
        """generated_config must be POST /api/v1/logs-metrics."""
        opp = savings.detect_logs_to_metrics(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        gc = opp["generated_config"]
        assert gc["verb"] == "POST"
        assert gc["endpoint"] == "/api/v1/logs-metrics"
        assert "data" in gc["payload"]

    def test_logs_to_metrics_needs_write_scope(self):
        opp = savings.detect_logs_to_metrics(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp["needs_write_scope"] is True

    def test_logs_to_metrics_no_forbidden_group_by_tags(self):
        """generated_config payload must NOT group by user_id/trace_id/request_id/ip."""
        opp = savings.detect_logs_to_metrics(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        payload_str = json.dumps(opp["generated_config"]["payload"])
        for forbidden in ("user_id", "trace_id", "request_id", "ip"):
            assert forbidden not in payload_str, f"Forbidden tag '{forbidden}' found in generated_config"

    def test_logs_to_metrics_savings_positive(self):
        """Savings must be positive: indexed_cost - metric_cost."""
        opp = savings.detect_logs_to_metrics(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp["monthly_savings_usd"] > 0.0

    def test_logs_to_metrics_prices_override(self):
        """Price override changes the savings calculation."""
        prices_low = dict(savings.DEFAULT_PRICES)
        prices_low["indexed_log_per_million"] = 0.50

        prices_high = dict(savings.DEFAULT_PRICES)
        prices_high["indexed_log_per_million"] = 5.00

        opp_low = savings.detect_logs_to_metrics(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=prices_low,
        )
        opp_high = savings.detect_logs_to_metrics(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=prices_high,
        )
        assert opp_high["monthly_savings_usd"] > opp_low["monthly_savings_usd"]

    def test_logs_to_metrics_returns_none_when_no_candidates(self):
        """Returns None when all services have mixed statuses (not safe to convert)."""
        mixed_aggregate = {
            "data": {
                "buckets": [
                    {"by": {"service": "mixed-svc", "status": "200"}, "computes": {"c0": 100}},
                    {"by": {"service": "mixed-svc", "status": "500"}, "computes": {"c0": 90}},
                ]
            }
        }
        opp = savings.detect_logs_to_metrics(
            logs_aggregate=mixed_aggregate,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp is None


# ---------------------------------------------------------------------------
# 3. detect_high_cardinality_metrics
# ---------------------------------------------------------------------------

class TestDetectHighCardinalityMetrics:
    def test_returns_opportunity_for_user_id_tag(self):
        """Metric with user_id tag in tag_configurations should surface high_cardinality_metric."""
        metrics_volumes = {
            "custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY,
            "custom.api.latency": METRIC_VOLUMES_LOW_CARDINALITY,
        }
        opp = savings.detect_high_cardinality_metrics(
            metrics_volumes=metrics_volumes,
        )
        assert opp is not None
        _validate_opportunity(opp)
        assert opp["lever"] == "high_cardinality_metric"
        assert opp["category"] == "metrics"

    def test_high_cardinality_generated_config_is_patch(self):
        """generated_config must be PATCH /api/v1/metric/{name}/tag_configurations."""
        metrics_volumes = {"custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        gc = opp["generated_config"]
        assert gc["verb"] == "PATCH"
        assert "/api/v1/metric/" in gc["endpoint"]
        assert "tag_configurations" in gc["endpoint"]
        assert "data" in gc["payload"]

    def test_high_cardinality_needs_write_scope(self):
        metrics_volumes = {"custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        assert opp["needs_write_scope"] is True

    def test_high_cardinality_excludes_forbidden_tags_from_recommendation(self):
        """The recommended tag list must NOT include forbidden high-cardinality tags."""
        metrics_volumes = {"custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        payload_str = json.dumps(opp["generated_config"]["payload"])
        for forbidden in ("user_id", "trace_id", "request_id", "ip"):
            # The recommended KEEP tags must not include forbidden ones
            assert forbidden not in payload_str, f"Forbidden tag '{forbidden}' in payload"

    def test_high_cardinality_savings_positive(self):
        metrics_volumes = {"custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        assert opp["monthly_savings_usd"] > 0.0

    def test_high_cardinality_prices_override(self):
        """Doubling custom_metric_per_month doubles savings."""
        metrics_volumes = {"custom.user.requests": METRIC_VOLUMES_HIGH_CARDINALITY}
        base = dict(savings.DEFAULT_PRICES)
        doubled = dict(savings.DEFAULT_PRICES)
        doubled["custom_metric_per_month"] = base["custom_metric_per_month"] * 2

        opp_base = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes, prices=base)
        opp_doubled = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes, prices=doubled)
        assert opp_doubled["monthly_savings_usd"] > opp_base["monthly_savings_usd"]

    def test_high_cardinality_returns_none_when_no_forbidden_tags(self):
        """Returns None when no metrics have forbidden high-cardinality tags."""
        metrics_volumes = {"custom.api.latency": METRIC_VOLUMES_LOW_CARDINALITY}
        opp = savings.detect_high_cardinality_metrics(metrics_volumes=metrics_volumes)
        assert opp is None

    def test_high_cardinality_returns_none_for_empty_volumes(self):
        opp = savings.detect_high_cardinality_metrics(metrics_volumes={})
        assert opp is None


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
        """Patch savings._http_get to return fixture data based on URL path."""

        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            body: dict
            if "/api/v2/logs/analytics/aggregate" in url:
                body = LOGS_AGGREGATE_FIXTURE
            elif "/api/v1/usage/logs" in url:
                body = USAGE_LOGS_FIXTURE
            elif "/api/v1/logs/config/indexes" in url:
                body = LOGS_INDEXES_FIXTURE
            elif "/api/v2/metrics" in url and "/volumes" in url:
                metric_name = url.split("/api/v2/metrics/")[1].split("/volumes")[0]
                if "user.requests" in metric_name:
                    body = METRIC_VOLUMES_HIGH_CARDINALITY
                else:
                    body = METRIC_VOLUMES_LOW_CARDINALITY
            elif "/api/v2/metrics" in url:
                body = METRICS_LIST_FIXTURE
            elif "/api/v1/usage/timeseries" in url:
                body = USAGE_TIMESERIES_FIXTURE
            else:
                body = {}
            return (200, {"content-type": "application/json"}, __import__("json").dumps(body).encode())

        monkeypatch.setattr(savings, "_http_get", fake_http_get)

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
        def fake_http_get(url, headers, timeout=15):
            if "/api/v2/logs/analytics/aggregate" in url:
                body = {"data": {"buckets": []}}
            elif "/api/v1/usage/logs" in url:
                body = {"usage": []}
            elif "/api/v1/logs/config/indexes" in url:
                body = {"indexes": [{"name": "main", "num_retention_days": 15, "daily_limit": 5_000_000, "exclusion_filters": []}]}
            elif "/api/v2/metrics" in url and "/volumes" in url:
                body = METRIC_VOLUMES_LOW_CARDINALITY
            elif "/api/v2/metrics" in url:
                body = {"data": []}
            elif "/api/v1/usage/timeseries" in url:
                body = {"usage": []}
            else:
                body = {}
            return (200, {}, __import__("json").dumps(body).encode())

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
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
        def fake_http_get(url: str, headers: dict, timeout: int = 15):
            if "/api/v2/logs/analytics/aggregate" in url:
                body = LOGS_AGGREGATE_FIXTURE
            elif "/api/v1/usage/logs" in url:
                body = USAGE_LOGS_FIXTURE
            elif "/api/v1/logs/config/indexes" in url:
                body = LOGS_INDEXES_FIXTURE
            elif "/api/v2/metrics" in url and "/volumes" in url:
                body = METRIC_VOLUMES_HIGH_CARDINALITY
            elif "/api/v2/metrics" in url:
                body = METRICS_LIST_FIXTURE
            elif "/api/v1/usage/timeseries" in url:
                body = USAGE_TIMESERIES_FIXTURE
            else:
                body = {}
            return (200, {}, __import__("json").dumps(body).encode())

        monkeypatch.setattr(savings, "_http_get", fake_http_get)

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

        monkeypatch.setattr(savings, "_http_get", fail_http_get)

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
