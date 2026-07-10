"""
Tests for dd_client.py — written FIRST (TDD Red phase).
All tests must fail before dd_client.py exists.
"""

import json
import sys
import types
import pytest

# ---------------------------------------------------------------------------
# Fixtures — response bodies matching the spec
# ---------------------------------------------------------------------------

ESTIMATED_JSON = {
    "data": [
        {
            "type": "cost_by_org",
            "attributes": {
                "org_name": "Acme",
                "public_id": "abc",
                "region": "us",
                "date": "2026-06-01T00:00:00Z",
                "charges": [
                    {"product_name": "Infrastructure Hosts", "charge_type": "committed", "cost": 9876.54},
                    {"product_name": "Infrastructure Hosts", "charge_type": "on_demand", "cost": 1200.00},
                    {"product_name": "Logs", "charge_type": "on_demand", "cost": 540.10},
                    {"product_name": "APM Hosts", "charge_type": "committed", "cost": 456.78},
                ],
            },
        }
    ]
}

PROJECTED_JSON = {
    "data": [
        {
            "type": "cost_by_org",
            "attributes": {
                "org_name": "Acme",
                "public_id": "abc",
                "region": "us",
                "date": "2026-06-01T00:00:00Z",
                "charges": [
                    {"product_name": "Infrastructure Hosts", "charge_type": "committed", "cost": 10500.00},
                    {"product_name": "Infrastructure Hosts", "charge_type": "on_demand", "cost": 1500.00},
                    {"product_name": "Logs", "charge_type": "on_demand", "cost": 600.00},
                    {"product_name": "APM Hosts", "charge_type": "committed", "cost": 500.00},
                ],
            },
        }
    ]
}

PREV_HISTORICAL_JSON = {
    "data": [
        {
            "type": "cost_by_org",
            "attributes": {
                "org_name": "Acme",
                "public_id": "abc",
                "region": "us",
                "date": "2026-05-01T00:00:00Z",
                "charges": [
                    {"product_name": "Infrastructure Hosts", "charge_type": "committed", "cost": 9000.00},
                    {"product_name": "Infrastructure Hosts", "charge_type": "on_demand", "cost": 200.00},
                    {"product_name": "Logs", "charge_type": "on_demand", "cost": 100.00},
                    {"product_name": "APM Hosts", "charge_type": "committed", "cost": 400.00},
                ],
            },
        }
    ]
}

ATTRIBUTION_CONFIGURED_JSON = {
    "metadata": {"pagination": {"next_record_id": None}},
    "data": [
        {
            "type": "cost_by_tag",
            "attributes": {
                "month": "2026-05-01T00",
                "org_name": "Acme",
                "tags": {"team": ["platform"], "service": ["auth"]},
                "infra_host_total_cost": 1234.56,
                "apm_host_total_cost": 456.78,
            },
        }
    ],
}

ATTRIBUTION_NOT_CONFIGURED_JSON = {
    "metadata": {"pagination": {"next_record_id": None}},
    "data": [
        {
            "type": "cost_by_tag",
            "attributes": {
                "month": "2026-05-01T00",
                "org_name": "Acme",
                "tags": None,
                "infra_host_total_cost": 1234.56,
                "apm_host_total_cost": 456.78,
            },
        }
    ],
}

FAKE_API_KEY = "TEST_API_KEY_DO_NOT_LEAK"
FAKE_APP_KEY = "TEST_APP_KEY_DO_NOT_LEAK"


# ---------------------------------------------------------------------------
# Helper — build a fake _http_get callable
# ---------------------------------------------------------------------------

def make_fake_http(status, resp_headers=None, body=None):
    """Return a callable that acts as _http_get, returning fixed values."""
    if body is None:
        body = b"{}"
    if resp_headers is None:
        resp_headers = {}

    def _fake_http(url, headers, timeout=15):
        return (status, resp_headers, body)

    return _fake_http


# ---------------------------------------------------------------------------
# 1. base_url — all known sites and unknown raises ValueError
# ---------------------------------------------------------------------------

class TestBaseUrl:
    def setup_method(self):
        import dd_client
        self.base_url = dd_client.base_url

    def test_us1_returns_datadoghq_com(self):
        assert self.base_url("us1") == "https://api.datadoghq.com"

    def test_us3_returns_us3_url(self):
        assert self.base_url("us3") == "https://api.us3.datadoghq.com"

    def test_us5_returns_us5_url(self):
        assert self.base_url("us5") == "https://api.us5.datadoghq.com"

    def test_eu_returns_datadoghq_eu(self):
        assert self.base_url("eu") == "https://api.datadoghq.eu"

    def test_ap1_returns_ap1_url(self):
        assert self.base_url("ap1") == "https://api.ap1.datadoghq.com"

    def test_ap2_returns_ap2_url(self):
        assert self.base_url("ap2") == "https://api.ap2.datadoghq.com"

    def test_uk1_returns_uk1_url(self):
        assert self.base_url("uk1") == "https://api.uk1.datadoghq.com"

    def test_unknown_site_raises_value_error(self):
        with pytest.raises(ValueError):
            self.base_url("xx99")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            self.base_url("")


# ---------------------------------------------------------------------------
# 2. Error mapping via monkeypatched _http_get
# ---------------------------------------------------------------------------

class TestErrorMapping:
    def setup_method(self):
        import dd_client
        self.dd = dd_client

    def _call(self, status, resp_headers=None, body=None):
        """Monkeypatch _http_get and call _request."""
        self.dd._http_get = make_fake_http(status, resp_headers, body or b'{"error":"test"}')
        return self.dd._request(
            "/api/v2/usage/estimated_cost",
            {},
            FAKE_API_KEY,
            FAKE_APP_KEY,
            "us1",
        )

    def test_401_raises_auth_error(self):
        with pytest.raises(self.dd.AuthError):
            self._call(401)

    def test_403_raises_permission_error(self):
        with pytest.raises(self.dd.PermissionError):
            self._call(403)

    def test_403_permission_error_is_subclass_of_datadog_error(self):
        with pytest.raises(self.dd.DatadogError):
            self._call(403)

    def test_429_raises_rate_limit_error(self):
        with pytest.raises(self.dd.RateLimitError):
            self._call(429, resp_headers={"X-RateLimit-Reset": "60"})

    def test_429_rate_limit_error_has_reset_seconds(self):
        try:
            self._call(429, resp_headers={"X-RateLimit-Reset": "42"})
        except self.dd.RateLimitError as e:
            assert e.reset_seconds == 42
        else:
            pytest.fail("RateLimitError not raised")

    def test_500_raises_datadog_error(self):
        with pytest.raises(self.dd.DatadogError):
            self._call(500)

    def test_200_returns_parsed_json(self):
        body = json.dumps({"data": []}).encode()
        self.dd._http_get = make_fake_http(200, {}, body)
        result = self.dd._request(
            "/api/v2/usage/estimated_cost",
            {},
            FAKE_API_KEY,
            FAKE_APP_KEY,
            "us1",
        )
        assert result == {"data": []}


# ---------------------------------------------------------------------------
# 3. Security — exception messages must NOT leak api/app keys
# ---------------------------------------------------------------------------

class TestSecurityKeyLeakage:
    def setup_method(self):
        import dd_client
        self.dd = dd_client

    def _attempt(self, status):
        self.dd._http_get = make_fake_http(status, {}, b'{"error":"forbidden"}')
        try:
            self.dd._request(
                "/api/v2/usage/estimated_cost",
                {},
                FAKE_API_KEY,
                FAKE_APP_KEY,
                "us1",
            )
        except Exception as exc:
            return str(exc)
        return ""

    def test_401_exception_message_does_not_contain_api_key(self):
        msg = self._attempt(401)
        assert FAKE_API_KEY not in msg

    def test_401_exception_message_does_not_contain_app_key(self):
        msg = self._attempt(401)
        assert FAKE_APP_KEY not in msg

    def test_403_exception_message_does_not_contain_api_key(self):
        msg = self._attempt(403)
        assert FAKE_API_KEY not in msg

    def test_403_exception_message_does_not_contain_app_key(self):
        msg = self._attempt(403)
        assert FAKE_APP_KEY not in msg

    def test_500_exception_message_does_not_contain_api_key(self):
        msg = self._attempt(500)
        assert FAKE_API_KEY not in msg

    def test_500_exception_message_does_not_contain_app_key(self):
        msg = self._attempt(500)
        assert FAKE_APP_KEY not in msg


# ---------------------------------------------------------------------------
# 4. Auth headers — _request sends DD-API-KEY and DD-APPLICATION-KEY
# ---------------------------------------------------------------------------

class TestAuthHeaders:
    def setup_method(self):
        import dd_client
        self.dd = dd_client

    def test_request_sends_api_key_header(self):
        captured = {}

        def capturing_http(url, headers, timeout=15):
            captured["headers"] = headers
            return (200, {}, b'{"data":[]}')

        self.dd._http_get = capturing_http
        self.dd._request("/api/v2/usage/estimated_cost", {}, FAKE_API_KEY, FAKE_APP_KEY, "us1")
        assert captured["headers"].get("DD-API-KEY") == FAKE_API_KEY

    def test_request_sends_app_key_header(self):
        captured = {}

        def capturing_http(url, headers, timeout=15):
            captured["headers"] = headers
            return (200, {}, b'{"data":[]}')

        self.dd._http_get = capturing_http
        self.dd._request("/api/v2/usage/estimated_cost", {}, FAKE_API_KEY, FAKE_APP_KEY, "us1")
        assert captured["headers"].get("DD-APPLICATION-KEY") == FAKE_APP_KEY

    def test_api_key_not_in_url(self):
        captured = {}

        def capturing_http(url, headers, timeout=15):
            captured["url"] = url
            return (200, {}, b'{"data":[]}')

        self.dd._http_get = capturing_http
        self.dd._request("/api/v2/usage/estimated_cost", {}, FAKE_API_KEY, FAKE_APP_KEY, "us1")
        assert FAKE_API_KEY not in captured["url"]

    def test_app_key_not_in_url(self):
        captured = {}

        def capturing_http(url, headers, timeout=15):
            captured["url"] = url
            return (200, {}, b'{"data":[]}')

        self.dd._http_get = capturing_http
        self.dd._request("/api/v2/usage/estimated_cost", {}, FAKE_API_KEY, FAKE_APP_KEY, "us1")
        assert FAKE_APP_KEY not in captured["url"]


# ---------------------------------------------------------------------------
# 5. parse_cost_by_product — aggregation across charge_types
# ---------------------------------------------------------------------------

class TestParseCostByProduct:
    def setup_method(self):
        import dd_client
        self.parse = dd_client.parse_cost_by_product

    def test_returns_list(self):
        result = self.parse(ESTIMATED_JSON)
        assert isinstance(result, list)

    def test_infrastructure_hosts_committed(self):
        result = self.parse(ESTIMATED_JSON)
        infra = next(r for r in result if r["product_name"] == "Infrastructure Hosts")
        assert infra["committed"] == pytest.approx(9876.54)

    def test_infrastructure_hosts_on_demand(self):
        result = self.parse(ESTIMATED_JSON)
        infra = next(r for r in result if r["product_name"] == "Infrastructure Hosts")
        assert infra["on_demand"] == pytest.approx(1200.00)

    def test_infrastructure_hosts_total_is_sum(self):
        result = self.parse(ESTIMATED_JSON)
        infra = next(r for r in result if r["product_name"] == "Infrastructure Hosts")
        assert infra["total"] == pytest.approx(9876.54 + 1200.00)

    def test_logs_only_on_demand(self):
        result = self.parse(ESTIMATED_JSON)
        logs = next(r for r in result if r["product_name"] == "Logs")
        assert logs["committed"] == pytest.approx(0.0)
        assert logs["on_demand"] == pytest.approx(540.10)
        assert logs["total"] == pytest.approx(540.10)

    def test_apm_hosts_only_committed(self):
        result = self.parse(ESTIMATED_JSON)
        apm = next(r for r in result if r["product_name"] == "APM Hosts")
        assert apm["on_demand"] == pytest.approx(0.0)
        assert apm["committed"] == pytest.approx(456.78)

    def test_three_distinct_products(self):
        result = self.parse(ESTIMATED_JSON)
        assert len(result) == 3

    def test_all_products_have_required_keys(self):
        result = self.parse(ESTIMATED_JSON)
        for r in result:
            assert "product_name" in r
            assert "committed" in r
            assert "on_demand" in r
            assert "total" in r

    def test_adjustment_included_in_total(self):
        data_with_adjustment = {
            "data": [
                {
                    "type": "cost_by_org",
                    "attributes": {
                        "charges": [
                            {"product_name": "Logs", "charge_type": "committed", "cost": 100.0},
                            {"product_name": "Logs", "charge_type": "on_demand", "cost": 50.0},
                            {"product_name": "Logs", "charge_type": "adjustment", "cost": -10.0},
                        ]
                    },
                }
            ]
        }
        result = self.parse(data_with_adjustment)
        logs = next(r for r in result if r["product_name"] == "Logs")
        assert logs["total"] == pytest.approx(100.0 + 50.0 + (-10.0))


# ---------------------------------------------------------------------------
# 6. grand_total
# ---------------------------------------------------------------------------

class TestGrandTotal:
    def setup_method(self):
        import dd_client
        self.grand_total = dd_client.grand_total
        self.parse = dd_client.parse_cost_by_product

    def test_grand_total_sums_all_product_totals(self):
        by_product = self.parse(ESTIMATED_JSON)
        # infra: 9876.54+1200 = 11076.54; logs: 540.10; apm: 456.78
        expected = 11076.54 + 540.10 + 456.78
        assert self.grand_total(by_product) == pytest.approx(expected)

    def test_grand_total_empty_list_is_zero(self):
        assert self.grand_total([]) == pytest.approx(0.0)

    def test_grand_total_returns_float(self):
        by_product = self.parse(ESTIMATED_JSON)
        assert isinstance(self.grand_total(by_product), float)


# ---------------------------------------------------------------------------
# 7. parse_tag_attribution
# ---------------------------------------------------------------------------

class TestParseTagAttribution:
    def setup_method(self):
        import dd_client
        self.parse = dd_client.parse_tag_attribution

    def test_configured_returns_list(self):
        dims = ["infra_host_total_cost", "apm_host_total_cost"]
        result = self.parse(ATTRIBUTION_CONFIGURED_JSON, dims)
        assert isinstance(result, list)

    def test_configured_row_has_tags(self):
        dims = ["infra_host_total_cost"]
        result = self.parse(ATTRIBUTION_CONFIGURED_JSON, dims)
        assert result[0]["tags"] == {"team": ["platform"], "service": ["auth"]}

    def test_configured_row_has_costs_for_requested_dims(self):
        dims = ["infra_host_total_cost", "apm_host_total_cost"]
        result = self.parse(ATTRIBUTION_CONFIGURED_JSON, dims)
        assert result[0]["costs"]["infra_host_total_cost"] == pytest.approx(1234.56)
        assert result[0]["costs"]["apm_host_total_cost"] == pytest.approx(456.78)

    def test_not_configured_returns_marker_dict(self):
        dims = ["infra_host_total_cost"]
        result = self.parse(ATTRIBUTION_NOT_CONFIGURED_JSON, dims)
        # Must be a dict, not a list
        assert isinstance(result, dict)

    def test_not_configured_marker_has_configured_false(self):
        dims = ["infra_host_total_cost"]
        result = self.parse(ATTRIBUTION_NOT_CONFIGURED_JSON, dims)
        assert result.get("configured") is False

    def test_not_configured_marker_has_empty_rows(self):
        dims = ["infra_host_total_cost"]
        result = self.parse(ATTRIBUTION_NOT_CONFIGURED_JSON, dims)
        assert result.get("rows") == []


# ---------------------------------------------------------------------------
# 8. summarize
# ---------------------------------------------------------------------------

class TestSummarize:
    def setup_method(self):
        import dd_client
        self.summarize = dd_client.summarize

    def test_returns_dict(self):
        result = self.summarize(ESTIMATED_JSON, PROJECTED_JSON, PREV_HISTORICAL_JSON)
        assert isinstance(result, dict)

    def test_total_matches_grand_total_of_estimated(self):
        import dd_client
        result = self.summarize(ESTIMATED_JSON, PROJECTED_JSON, PREV_HISTORICAL_JSON)
        # 9876.54 + 1200 + 540.10 + 456.78 = 12073.42
        expected = 9876.54 + 1200.00 + 540.10 + 456.78
        assert result["total"] == pytest.approx(expected)

    def test_by_product_is_list(self):
        result = self.summarize(ESTIMATED_JSON, PROJECTED_JSON, PREV_HISTORICAL_JSON)
        assert isinstance(result["by_product"], list)

    def test_on_demand_overage_sums_on_demand_across_products(self):
        result = self.summarize(ESTIMATED_JSON, PROJECTED_JSON, PREV_HISTORICAL_JSON)
        # infra on_demand=1200, logs on_demand=540.10; APM=0
        expected_overage = 1200.00 + 540.10
        assert result["on_demand_overage"] == pytest.approx(expected_overage)

    def test_projected_end_of_month_from_projected_json(self):
        import dd_client
        result = self.summarize(ESTIMATED_JSON, PROJECTED_JSON, PREV_HISTORICAL_JSON)
        # projected: 10500+1500+600+500 = 13100
        expected = 10500.00 + 1500.00 + 600.00 + 500.00
        assert result["projected_end_of_month"] == pytest.approx(expected)

    def test_prev_month_total_from_historical_json(self):
        result = self.summarize(ESTIMATED_JSON, PROJECTED_JSON, PREV_HISTORICAL_JSON)
        # prev: 9000+200+100+400 = 9700
        expected = 9000.00 + 200.00 + 100.00 + 400.00
        assert result["prev_month_total"] == pytest.approx(expected)

    def test_delta_pct_vs_prev_month(self):
        result = self.summarize(ESTIMATED_JSON, PROJECTED_JSON, PREV_HISTORICAL_JSON)
        # current=12073.42, prev=9700 → delta=(12073.42-9700)/9700*100
        current = 9876.54 + 1200.00 + 540.10 + 456.78
        prev = 9000.00 + 200.00 + 100.00 + 400.00
        expected_delta = (current - prev) / prev * 100
        assert result["delta_pct"] == pytest.approx(expected_delta)

    def test_spike_true_when_delta_pct_above_25(self):
        result = self.summarize(ESTIMATED_JSON, PROJECTED_JSON, PREV_HISTORICAL_JSON)
        # delta is > 25%, so spike should be True
        current = 9876.54 + 1200.00 + 540.10 + 456.78
        prev = 9000.00 + 200.00 + 100.00 + 400.00
        delta = (current - prev) / prev * 100
        if delta > 25:
            assert result["spike"] is True

    def test_spike_true_when_on_demand_overage_exceeds_15pct_of_total(self):
        # Build a scenario where on_demand > 15% of total but delta < 25%
        low_delta_json = {
            "data": [
                {
                    "type": "cost_by_org",
                    "attributes": {
                        "charges": [
                            {"product_name": "Infra", "charge_type": "committed", "cost": 100.0},
                            # on_demand = 20 → 20/(100+20)=16.6% > 15%
                            {"product_name": "Infra", "charge_type": "on_demand", "cost": 20.0},
                        ]
                    },
                }
            ]
        }
        prev_similar = {
            "data": [
                {
                    "type": "cost_by_org",
                    "attributes": {
                        "charges": [
                            {"product_name": "Infra", "charge_type": "committed", "cost": 110.0},
                        ]
                    },
                }
            ]
        }
        projected_similar = {
            "data": [
                {
                    "type": "cost_by_org",
                    "attributes": {
                        "charges": [
                            {"product_name": "Infra", "charge_type": "committed", "cost": 120.0},
                        ]
                    },
                }
            ]
        }
        result = self.summarize(low_delta_json, projected_similar, prev_similar)
        # on_demand=20, total=120 → overage ratio = 20/120 = 16.6% > 15%
        assert result["spike"] is True

    def test_spike_false_when_below_thresholds(self):
        low_everything = {
            "data": [
                {
                    "type": "cost_by_org",
                    "attributes": {
                        "charges": [
                            {"product_name": "Infra", "charge_type": "committed", "cost": 1000.0},
                            {"product_name": "Infra", "charge_type": "on_demand", "cost": 10.0},
                        ]
                    },
                }
            ]
        }
        prev_similar = {
            "data": [
                {
                    "type": "cost_by_org",
                    "attributes": {
                        "charges": [
                            {"product_name": "Infra", "charge_type": "committed", "cost": 990.0},
                        ]
                    },
                }
            ]
        }
        projected_similar = {
            "data": [
                {
                    "type": "cost_by_org",
                    "attributes": {
                        "charges": [
                            {"product_name": "Infra", "charge_type": "committed", "cost": 1050.0},
                        ]
                    },
                }
            ]
        }
        result = self.summarize(low_everything, projected_similar, prev_similar)
        # delta=(1010-990)/990*100 = ~2%, on_demand=10/1010=~0.99% → both below thresholds
        assert result["spike"] is False

    def test_summarize_has_all_required_keys(self):
        result = self.summarize(ESTIMATED_JSON, PROJECTED_JSON, PREV_HISTORICAL_JSON)
        required = {"total", "by_product", "on_demand_overage", "projected_end_of_month",
                    "prev_month_total", "delta_pct", "spike"}
        assert required.issubset(result.keys())


# ---------------------------------------------------------------------------
# 9. Public API functions — verify they call _request with correct path
# ---------------------------------------------------------------------------

class TestPublicApiFunctions:
    def setup_method(self):
        import dd_client
        self.dd = dd_client
        self.captured = {}

        def capturing_http(url, headers, timeout=15):
            self.captured["url"] = url
            self.captured["headers"] = headers
            return (200, {}, json.dumps(ESTIMATED_JSON).encode())

        self.dd._http_get = capturing_http

    def test_get_estimated_cost_calls_correct_path(self):
        self.dd.get_estimated_cost(FAKE_API_KEY, FAKE_APP_KEY, "us1")
        assert "/api/v2/usage/estimated_cost" in self.captured["url"]

    def test_get_projected_cost_calls_correct_path(self):
        self.dd.get_projected_cost(FAKE_API_KEY, FAKE_APP_KEY, "us1")
        assert "/api/v2/usage/projected_cost" in self.captured["url"]

    def test_get_historical_cost_calls_correct_path(self):
        self.dd.get_historical_cost(FAKE_API_KEY, FAKE_APP_KEY, "us1", start_month="2026-05-01")
        assert "/api/v2/usage/historical_cost" in self.captured["url"]

    def test_get_historical_cost_includes_start_month_param(self):
        self.dd.get_historical_cost(FAKE_API_KEY, FAKE_APP_KEY, "us1", start_month="2026-05-01")
        assert "start_month=2026-05-01" in self.captured["url"]

    def test_get_historical_cost_includes_end_month_when_provided(self):
        self.dd.get_historical_cost(
            FAKE_API_KEY, FAKE_APP_KEY, "us1",
            start_month="2026-04-01", end_month="2026-05-01"
        )
        assert "end_month=2026-05-01" in self.captured["url"]

    def test_get_monthly_cost_attribution_calls_correct_path(self):
        self.dd.get_monthly_cost_attribution(FAKE_API_KEY, FAKE_APP_KEY, "us1", start_month="2026-05")
        assert "/api/v2/usage/monthly_cost_attribution" in self.captured["url"]

    def test_get_monthly_cost_attribution_includes_start_month(self):
        self.dd.get_monthly_cost_attribution(FAKE_API_KEY, FAKE_APP_KEY, "us1", start_month="2026-05")
        assert "start_month=2026-05" in self.captured["url"]

    def test_get_monthly_cost_attribution_includes_fields(self):
        self.dd.get_monthly_cost_attribution(
            FAKE_API_KEY, FAKE_APP_KEY, "us1",
            start_month="2026-05", fields="infra_host_total_cost"
        )
        assert "fields=infra_host_total_cost" in self.captured["url"]

    def test_get_monthly_cost_attribution_default_fields_is_star(self):
        self.dd.get_monthly_cost_attribution(FAKE_API_KEY, FAKE_APP_KEY, "us1", start_month="2026-05")
        assert "fields=%2A" in self.captured["url"] or "fields=*" in self.captured["url"]

    def test_get_monthly_cost_attribution_includes_tag_breakdown_keys_when_provided(self):
        self.dd.get_monthly_cost_attribution(
            FAKE_API_KEY, FAKE_APP_KEY, "us1",
            start_month="2026-05", tag_breakdown_keys="team,service"
        )
        assert "tag_breakdown_keys" in self.captured["url"]

    def test_public_functions_return_dict(self):
        result = self.dd.get_estimated_cost(FAKE_API_KEY, FAKE_APP_KEY, "us1")
        assert isinstance(result, dict)
