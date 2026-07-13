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

def _make_http_mock(monkeypatch, aggregate=None, facet_response=None):
    """Monkeypatch _http_get and _http_post for savings.py.

    aggregate: override for the logs aggregate POST response
    facet_response: override for secondary facet POST (drill query)
    """
    if aggregate is None:
        aggregate = LOGS_AGGREGATE_MULTI
    if facet_response is None:
        # Non-empty drill result
        facet_response = {
            "data": {
                "buckets": [
                    {"by": {"@http.url_details.path": "/health"}, "computes": {"c0": 180_000_000}},
                    {"by": {"@http.url_details.path": "/metrics"}, "computes": {"c0": 50_000_000}},
                    {"by": {"@http.url_details.path": "/ready"}, "computes": {"c0": 26_666_667}},
                ]
            }
        }

    def fake_http_get(url, headers, timeout=15):
        if "/api/v1/logs/config/indexes" in url:
            body = LOGS_INDEXES_FIXTURE
        elif "/api/v1/usage/logs" in url:
            body = {"usage": []}
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
        if "/api/v2/logs/analytics/aggregate" in url:
            try:
                payload = json.loads(body_bytes)
            except Exception:
                payload = {}
            # Check if this is a facet drill (has a filter query narrowing to a service)
            filter_q = payload.get("filter", {}).get("query", "")
            if filter_q and "service:" in filter_q:
                return (200, {"content-type": "application/json"}, json.dumps(facet_response).encode())
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
# 2. detect_exclusion_candidates_multi — multiple opportunities
# ===========================================================================

class TestDetectExclusionCandidatesMulti:
    """Test the new multi-opportunity version of the exclusion detector."""

    def test_returns_list(self):
        """detect_exclusion_candidates_multi returns a list."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        assert isinstance(result, list)

    def test_two_noisy_services_produce_two_opportunities(self):
        """With 5 distinct noise buckets above 1M threshold, at least 2 opportunities emitted."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        # aws/debug, nginx/200, payment/debug, health/200, auth/201 all >= 1M threshold
        assert len(result) >= 2, f"Expected >= 2 opportunities, got {len(result)}"

    def test_each_opportunity_is_separate_service_status(self):
        """Each opportunity corresponds to a distinct (service, status) pair."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        seen = set()
        for opp in result:
            key = (opp.get("service") or opp.get("title", ""), opp.get("status", ""))
            assert key not in seen, f"Duplicate (service, status) pair: {key}"
            seen.add(key)

    def test_non_noise_statuses_not_included(self):
        """error/info/warn statuses must NOT produce opportunities."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        for opp in result:
            status = opp.get("status", "")
            assert status not in ("error", "info", "warn"), (
                f"Non-noise status {status!r} incorrectly emitted as opportunity"
            )

    def test_below_threshold_not_included(self):
        """Buckets below 1M monthly events threshold (tiny/debug: 214k/mo) must not appear."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        for opp in result:
            # tiny service should never appear
            assert opp.get("service") != "tiny", "tiny/debug below 1M threshold should be excluded"

    def test_sorted_desc_by_savings(self):
        """Opportunities sorted descending by monthly_savings_usd."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        savings_list = [o["monthly_savings_usd"] for o in result]
        assert savings_list == sorted(savings_list, reverse=True)

    def test_capped_at_8_opportunities(self):
        """Never more than 8 opportunities returned."""
        # Build aggregate with 15 noise buckets
        big_aggregate = {
            "data": {
                "buckets": [
                    {"by": {"service": f"svc-{i}", "status": "debug"},
                     "computes": {"c0": 10_000_000 - i * 500_000}}
                    for i in range(15)
                ]
            }
        }
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=big_aggregate,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        assert len(result) <= 8, f"Expected <= 8, got {len(result)}"

    def test_empty_when_no_noise(self):
        """Returns empty list when no noise buckets meet the criteria."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_NO_NOISE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        assert result == []

    def test_each_opportunity_has_required_fields(self):
        """Each opportunity has standard SavingsOpportunity fields."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        required = {
            "id", "lever", "category", "title", "summary",
            "monthly_savings_usd", "savings_pct", "effort", "confidence",
            "evidence", "generated_config", "needs_write_scope",
            "detection_query", "why",
        }
        for opp in result:
            missing = required - set(opp.keys())
            assert not missing, f"Opportunity missing fields: {missing}"

    def test_each_opportunity_has_service_and_status(self):
        """Each opportunity carries a service and status field."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        for opp in result:
            assert "service" in opp and opp["service"], f"Missing service in opp: {opp}"
            assert "status" in opp and opp["status"], f"Missing status in opp: {opp}"

    def test_each_opportunity_has_monthly_events(self):
        """Each opportunity carries monthly_events."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        for opp in result:
            assert "monthly_events" in opp
            assert opp["monthly_events"] >= 1_000_000

    def test_savings_math_single_bucket(self):
        """For a single-bucket case, savings = bucket_cost = monthly_events/1e6 * price."""
        single_bucket = {
            "data": {
                "buckets": [
                    {"by": {"service": "aws", "status": "debug"},
                     "computes": {"c0": 10_000_000}},  # 10M/7d → ~42.86M/mo
                ]
            }
        }
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=single_bucket,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        assert len(result) == 1
        opp = result[0]
        expected_monthly = 10_000_000 * 30 / 7
        expected_cost = expected_monthly / 1_000_000 * TEST_PRICES["indexed_log_per_million"]
        assert abs(opp["monthly_savings_usd"] - expected_cost) < 0.01

    def test_generated_config_has_exclusion_filter_for_specific_service(self):
        """Each opportunity's generated_config exclusion filter query targets that specific service."""
        result = savings.detect_exclusion_candidates_multi(
            logs_aggregate=LOGS_AGGREGATE_MULTI,
            logs_indexes=LOGS_INDEXES_FIXTURE,
            prices=TEST_PRICES,
        )
        for opp in result:
            service = opp.get("service", "")
            gc = opp.get("generated_config", {})
            # The exclusion filter query should reference this service
            gc_str = json.dumps(gc)
            assert service in gc_str, (
                f"Service {service!r} not found in generated_config for opp {opp['id']!r}"
            )


# ===========================================================================
# 3. Whale drill — sub-pattern detection
# ===========================================================================

class TestWhaleDrill:
    """Test that the top 1-3 noisy buckets get a facet drill for sub-patterns."""

    def test_drill_attaches_drill_patterns_when_facet_has_data(self, monkeypatch):
        """When facet drill returns data, the top opportunity has drill_patterns."""
        _make_http_mock(monkeypatch)
        result = savings.scan(
            api_key="test_key",
            app_key="test_app_key",
            site="us1",
            prices=TEST_PRICES,
        )
        # Find the top exclusion_filter opportunity (aws/debug)
        excl_opps = [o for o in result["opportunities"] if o.get("lever") == "exclusion_filter"]
        assert len(excl_opps) >= 1, "Expected at least one exclusion_filter opportunity"

        # The top opportunity should have drill_patterns from the secondary query
        top_opp = excl_opps[0]
        assert "drill_patterns" in top_opp, (
            f"Top exclusion opportunity missing drill_patterns. Keys: {list(top_opp.keys())}"
        )
        assert isinstance(top_opp["drill_patterns"], list)
        assert len(top_opp["drill_patterns"]) >= 1

    def test_drill_patterns_are_dicts_with_facet_and_count(self, monkeypatch):
        """drill_patterns items have facet_value and monthly_events fields."""
        _make_http_mock(monkeypatch)
        result = savings.scan(
            api_key="test_key",
            app_key="test_app_key",
            site="us1",
            prices=TEST_PRICES,
        )
        excl_opps = [o for o in result["opportunities"] if o.get("lever") == "exclusion_filter"
                     and "drill_patterns" in o]
        if not excl_opps:
            pytest.skip("No drilled opportunity found")
        patterns = excl_opps[0]["drill_patterns"]
        for p in patterns:
            assert "facet_value" in p, f"drill_pattern missing facet_value: {p}"
            assert "monthly_events" in p, f"drill_pattern missing monthly_events: {p}"

    def test_drill_attaches_drill_facet(self, monkeypatch):
        """Drilled opportunity carries drill_facet field."""
        _make_http_mock(monkeypatch)
        result = savings.scan(
            api_key="test_key",
            app_key="test_app_key",
            site="us1",
            prices=TEST_PRICES,
        )
        excl_opps = [o for o in result["opportunities"] if o.get("lever") == "exclusion_filter"
                     and "drill_facet" in o]
        assert excl_opps, "No opportunity with drill_facet found"

    def test_drill_degrades_gracefully_when_facet_empty(self, monkeypatch):
        """When facet drill returns empty buckets, opportunity still present (no crash)."""
        empty_facet = {"data": {"buckets": []}}
        _make_http_mock(monkeypatch, facet_response=empty_facet)
        result = savings.scan(
            api_key="test_key",
            app_key="test_app_key",
            site="us1",
            prices=TEST_PRICES,
        )
        excl_opps = [o for o in result["opportunities"] if o.get("lever") == "exclusion_filter"]
        assert excl_opps, "Opportunities list should still have entries even with empty drill"
        # With empty drill, drill_patterns should be absent or empty
        top_opp = excl_opps[0]
        patterns = top_opp.get("drill_patterns", [])
        assert isinstance(patterns, list)
        # Either missing or empty is fine
        assert len(patterns) == 0

    def test_drill_degrades_gracefully_when_facet_errors(self, monkeypatch):
        """When facet drill POST returns 403, opportunity still present (no crash)."""
        def fake_http_get(url, headers, timeout=15):
            if "/api/v1/logs/config/indexes" in url:
                body = LOGS_INDEXES_FIXTURE
            elif "/api/v1/usage/logs" in url:
                body = {"usage": []}
            elif "/api/v2/metrics" in url:
                body = {"data": []}
            elif "/api/v2/usage/estimated_cost" in url:
                body = {"data": []}
            else:
                body = {}
            return (200, {}, json.dumps(body).encode())

        call_count = {"n": 0}
        def fake_http_post(url, headers, body_bytes, timeout=15):
            if "/api/v2/logs/analytics/aggregate" in url:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    # First call: main aggregate
                    return (200, {}, json.dumps(LOGS_AGGREGATE_MULTI).encode())
                else:
                    # Subsequent calls: drill queries fail with 403
                    return (403, {}, b'{"errors": ["Forbidden"]}')
            return (200, {}, b"{}")

        monkeypatch.setattr(savings, "_http_get", fake_http_get)
        monkeypatch.setattr(savings, "_http_post", fake_http_post)

        result = savings.scan(
            api_key="test_key",
            app_key="test_app_key",
            site="us1",
            prices=TEST_PRICES,
        )
        excl_opps = [o for o in result["opportunities"] if o.get("lever") == "exclusion_filter"]
        assert excl_opps, "Opportunities should still be present despite drill 403"

    def test_specific_exclusion_query_when_drill_found_path(self, monkeypatch):
        """When drill finds a top path, detection_query or generated_config has more specific filter."""
        _make_http_mock(monkeypatch)
        result = savings.scan(
            api_key="test_key",
            app_key="test_app_key",
            site="us1",
            prices=TEST_PRICES,
        )
        excl_opps = [o for o in result["opportunities"] if o.get("lever") == "exclusion_filter"
                     and o.get("drill_patterns")]
        if not excl_opps:
            pytest.skip("No drilled opportunity found")
        top_opp = excl_opps[0]
        # The top pattern value should appear somewhere in detection_query or generated_config
        top_pattern = top_opp["drill_patterns"][0]["facet_value"]
        gc_str = json.dumps(top_opp.get("generated_config", {}))
        dq = top_opp.get("detection_query", "")
        assert top_pattern in gc_str or top_pattern in dq, (
            f"Top drill pattern {top_pattern!r} not found in generated_config or detection_query"
        )


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

    def test_scan_multiple_exclusion_opportunities_from_multi_bucket(self, monkeypatch):
        """scan() emits multiple exclusion_filter opportunities when multiple noisy buckets present."""
        _make_http_mock(monkeypatch)
        result = savings.scan(
            api_key="k",
            app_key="a",
            site="us1",
            prices=TEST_PRICES,
        )
        excl_opps = [o for o in result["opportunities"] if o.get("lever") == "exclusion_filter"]
        assert len(excl_opps) >= 2, (
            f"Expected >= 2 exclusion_filter opportunities, got {len(excl_opps)}"
        )

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
# 6. Backward-compatibility: existing test helper still works
# ===========================================================================

class TestBackwardCompatibility:
    """Old detect_exclusion_candidates still works (returns single opportunity)."""

    def test_old_detect_exclusion_still_works(self):
        """detect_exclusion_candidates (original) still returns a single opportunity."""
        from tests.test_savings import LOGS_AGGREGATE_FIXTURE, LOGS_INDEXES_FIXTURE
        opp = savings.detect_exclusion_candidates(
            logs_aggregate=LOGS_AGGREGATE_FIXTURE,
            logs_indexes=LOGS_INDEXES_FIXTURE,
        )
        assert opp is not None
        assert "lever" in opp
        assert opp["lever"] == "exclusion_filter"

    def test_scan_result_still_has_total_monthly_waste(self, monkeypatch):
        """scan() result still has total_monthly_waste_usd key (not broken)."""
        _make_http_mock(monkeypatch)
        result = savings.scan(api_key="k", app_key="a", site="us1", prices=TEST_PRICES)
        assert "total_monthly_waste_usd" in result
        assert "opportunities" in result
        assert isinstance(result["opportunities"], list)
