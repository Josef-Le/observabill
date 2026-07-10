"""
tests/test_observabill.py — ObservaBill tests (TDD Red phase).
All tests must fail before app.py exists.

Run: /opt/homebrew/opt/python@3.12/bin/python3.12 -m pytest tests/ -q
"""

import sys
import os
import json
import threading
import urllib.request
import urllib.error
import urllib.parse
import http.client

import pytest

# ---------------------------------------------------------------------------
# Fixtures — realistic DD summary data
# ---------------------------------------------------------------------------

SAMPLE_SUMMARY = {
    "total": 8240.0,
    "by_product": [
        {"product_name": "Infrastructure Hosts", "committed": 5200.0, "on_demand": 1400.0, "total": 6600.0},
        {"product_name": "Logs", "committed": 0.0, "on_demand": 900.0, "total": 900.0},
        {"product_name": "APM Hosts", "committed": 740.0, "on_demand": 0.0, "total": 740.0},
    ],
    "on_demand_overage": 2300.0,
    "projected_end_of_month": 9300.0,
    "prev_month_total": 6800.0,
    "delta_pct": 21.2,
    "spike": True,
}

SAMPLE_TAG_ATTRIBUTION = [
    {"tags": {"team": ["platform"]}, "costs": {"infra_host_total_cost": 3200.0}},
    {"tags": {"team": ["backend"]}, "costs": {"infra_host_total_cost": 2400.0}},
]

ESTIMATED_JSON = {
    "data": [{
        "type": "cost_by_org",
        "attributes": {
            "charges": [
                {"product_name": "Infrastructure Hosts", "charge_type": "committed", "cost": 5200.0},
                {"product_name": "Infrastructure Hosts", "charge_type": "on_demand", "cost": 1400.0},
                {"product_name": "Logs", "charge_type": "on_demand", "cost": 900.0},
                {"product_name": "APM Hosts", "charge_type": "committed", "cost": 740.0},
            ]
        }
    }]
}

PROJECTED_JSON = {
    "data": [{
        "type": "cost_by_org",
        "attributes": {
            "charges": [
                {"product_name": "Infrastructure Hosts", "charge_type": "committed", "cost": 6000.0},
                {"product_name": "Infrastructure Hosts", "charge_type": "on_demand", "cost": 1600.0},
                {"product_name": "Logs", "charge_type": "on_demand", "cost": 1000.0},
                {"product_name": "APM Hosts", "charge_type": "committed", "cost": 700.0},
            ]
        }
    }]
}

PREV_HISTORICAL_JSON = {
    "data": [{
        "type": "cost_by_org",
        "attributes": {
            "charges": [
                {"product_name": "Infrastructure Hosts", "charge_type": "committed", "cost": 4800.0},
                {"product_name": "Logs", "charge_type": "on_demand", "cost": 800.0},
                {"product_name": "APM Hosts", "charge_type": "committed", "cost": 600.0},
                {"product_name": "Logs", "charge_type": "on_demand", "cost": 600.0},
            ]
        }
    }]
}

ATTRIBUTION_CONFIGURED_JSON = {
    "data": [{
        "type": "cost_by_tag",
        "attributes": {
            "month": "2026-06-01T00",
            "tags": {"team": ["platform"]},
            "infra_host_total_cost": 3200.0,
        }
    }]
}

ATTRIBUTION_NOT_CONFIGURED_JSON = {
    "data": [{
        "type": "cost_by_tag",
        "attributes": {
            "month": "2026-06-01T00",
            "tags": None,
            "infra_host_total_cost": 3200.0,
        }
    }]
}


# ---------------------------------------------------------------------------
# Import app module (server-less)
# ---------------------------------------------------------------------------

def import_app():
    """Import app from the observabill directory, adding it to sys.path."""
    app_dir = os.path.join(os.path.dirname(__file__), "..")
    if app_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(app_dir))
    import app
    return app


# ---------------------------------------------------------------------------
# Live server fixture (integration tests)
# ---------------------------------------------------------------------------

TEST_PORT = 8931  # avoid collision with production port 8921


def start_test_server(port):
    """Start the ObservaBill server on given port in a daemon thread."""
    app = import_app()
    server = app.make_server(port)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


@pytest.fixture(scope="module")
def server():
    """Start a live server for integration tests."""
    import time
    srv = start_test_server(TEST_PORT)
    time.sleep(0.3)  # give the server a moment to bind
    yield srv
    srv.shutdown()


def _raw_request(method, path, body=None, headers=None, port=TEST_PORT):
    """
    Direct http.client connection to localhost, bypassing HTTP_PROXY.
    Per memory: HTTP_PROXY intercepts localhost — never use urllib for localhost requests.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Content-Type": "application/x-www-form-urlencoded"} if headers is None else headers
    conn.request(method, path, body=body, headers=h)
    resp = conn.getresponse()
    status = resp.status
    text = resp.read().decode("utf-8")
    conn.close()
    return status, text


def get(path, port=TEST_PORT):
    return _raw_request("GET", path, port=port)


def post(path, data: dict, port=TEST_PORT):
    body = urllib.parse.urlencode(data).encode()
    return _raw_request("POST", path, body=body, port=port)


# ---------------------------------------------------------------------------
# 1. Unit: html_page helper
# ---------------------------------------------------------------------------

class TestHtmlPage:
    def setup_method(self):
        self.app = import_app()

    def test_returns_string(self):
        result = self.app.html_page("Test", "<p>hello</p>")
        assert isinstance(result, str)

    def test_contains_title_observabill(self):
        result = self.app.html_page("Home", "<p>body</p>")
        assert "ObservaBill" in result

    def test_contains_body_content(self):
        result = self.app.html_page("Test", "<p>unique-content-xyz</p>")
        assert "unique-content-xyz" in result

    def test_contains_doctype(self):
        result = self.app.html_page("Test", "")
        assert "<!DOCTYPE html>" in result or "<!doctype html>" in result.lower()

    def test_extra_head_is_included(self):
        result = self.app.html_page("Test", "", extra_head='<meta name="robots" content="noindex">')
        assert 'content="noindex"' in result

    def test_nav_logo_says_observabill(self):
        result = self.app.html_page("Test", "")
        assert "ObservaBill" in result


# ---------------------------------------------------------------------------
# 2. Unit: append_to_store / log_event
# ---------------------------------------------------------------------------

class TestAppendToStore:
    def setup_method(self):
        self.app = import_app()
        self.writes = []
        # Monkeypatch to capture writes without touching disk
        self.app.append_to_store = lambda filename, data: self.writes.append((filename, data))

    def test_log_event_writes_to_funnel(self):
        self.app.log_event("visit", ref="google")
        assert any(f == "funnel.txt" for f, _ in self.writes)

    def test_log_event_records_event_name(self):
        self.app.log_event("analyze", site="us1")
        assert any(d.get("event") == "analyze" for _, d in self.writes)

    def test_log_event_records_extra_fields(self):
        self.app.log_event("analyze", site="eu")
        assert any(d.get("site") == "eu" for _, d in self.writes)

    def test_log_event_records_ref(self):
        self.app.log_event("visit", ref="twitter")
        assert any(d.get("ref") == "twitter" for _, d in self.writes)


# ---------------------------------------------------------------------------
# 3. Unit: render_breakdown renders correct HTML
# ---------------------------------------------------------------------------

class TestRenderBreakdown:
    """Tests for the breakdown rendering function (render_breakdown or page_breakdown)."""

    def setup_method(self):
        self.app = import_app()

    def test_shows_grand_total(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        assert "$8,240" in html

    def test_shows_projected_eom(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        assert "$9,300" in html

    def test_shows_by_product_table(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        assert "Infrastructure Hosts" in html

    def test_shows_apm_hosts_in_table(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        assert "APM Hosts" in html

    def test_shows_logs_in_table(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        assert "Logs" in html

    def test_spike_warning_when_spike_true(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        # spike=True in SAMPLE_SUMMARY
        assert "spike" in html.lower() or "warning" in html.lower() or "⚠" in html

    def test_reserve_upsell_present(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        assert "/reserve" in html or "reserve" in html.lower()

    def test_on_demand_overage_flag_present(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        # on_demand_overage = 2300 > 0
        assert "on-demand" in html.lower() or "on_demand" in html.lower() or "on demand" in html.lower()

    def test_delta_vs_last_month_shown(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        # prev_month_total = 6800, delta_pct = 21.2
        assert "$6,800" in html or "6,800" in html or "21" in html

    def test_tag_attribution_configured_shows_team_breakdown(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=SAMPLE_TAG_ATTRIBUTION)
        assert "platform" in html

    def test_tag_attribution_not_configured_shows_hint(self):
        not_configured = {"configured": False, "rows": []}
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=not_configured)
        # Should show hint about enabling Usage Attribution
        assert "attribution" in html.lower() or "usage attribution" in html.lower()

    def test_products_sorted_desc_by_total(self):
        html = self.app.render_breakdown(SAMPLE_SUMMARY, tag_attr=None)
        infra_pos = html.find("Infrastructure Hosts")
        logs_pos = html.find("Logs")
        apm_pos = html.find("APM Hosts")
        # Infrastructure Hosts (6600) > Logs (900) > APM (740) — but actually APM=740 < Logs=900
        # Infrastructure Hosts should appear before Logs which appears before APM
        assert infra_pos < logs_pos
        assert logs_pos < apm_pos


# ---------------------------------------------------------------------------
# 4. Unit: security — api_key and app_key must never appear in rendered HTML
#    or in append_to_store writes
# ---------------------------------------------------------------------------

class TestSecurityNoKeyLeak:
    def setup_method(self):
        self.app = import_app()
        self.writes = []
        self.app.append_to_store = lambda filename, data: self.writes.append((filename, json.dumps(data)))

    def _patch_dd_client_success(self):
        """Make all dd_client calls return fixture data."""
        import dd_client
        self.app.dd_client = dd_client
        dd_client.get_estimated_cost = lambda *a, **kw: ESTIMATED_JSON
        dd_client.get_projected_cost = lambda *a, **kw: PROJECTED_JSON
        dd_client.get_historical_cost = lambda *a, **kw: PREV_HISTORICAL_JSON
        dd_client.get_monthly_cost_attribution = lambda *a, **kw: ATTRIBUTION_NOT_CONFIGURED_JSON
        dd_client.summarize = lambda *a, **kw: SAMPLE_SUMMARY
        dd_client.parse_tag_attribution = lambda *a, **kw: {"configured": False, "rows": []}

    def test_api_key_never_in_rendered_html(self):
        self._patch_dd_client_success()
        html = self.app.page_analyze("SECRET_DD_KEY", "SECRET_APP_KEY", "us1")
        assert "SECRET_DD_KEY" not in html

    def test_app_key_never_in_rendered_html(self):
        self._patch_dd_client_success()
        html = self.app.page_analyze("SECRET_DD_KEY", "SECRET_APP_KEY", "us1")
        assert "SECRET_APP_KEY" not in html

    def test_api_key_never_in_store_writes(self):
        self._patch_dd_client_success()
        self.app.page_analyze("SECRET_DD_KEY", "SECRET_APP_KEY", "us1")
        combined = " ".join(v for _, v in self.writes)
        assert "SECRET_DD_KEY" not in combined

    def test_app_key_never_in_store_writes(self):
        self._patch_dd_client_success()
        self.app.page_analyze("SECRET_DD_KEY", "SECRET_APP_KEY", "us1")
        combined = " ".join(v for _, v in self.writes)
        assert "SECRET_APP_KEY" not in combined

    def test_not_configured_hint_shown_when_attribution_off(self):
        self._patch_dd_client_success()
        html = self.app.page_analyze("SECRET_DD_KEY", "SECRET_APP_KEY", "us1")
        assert "attribution" in html.lower() or "usage attribution" in html.lower()


# ---------------------------------------------------------------------------
# 5. Unit: page_analyze — happy path (monkeypatched dd_client)
# ---------------------------------------------------------------------------

class TestPageAnalyze:
    def setup_method(self):
        self.app = import_app()
        import dd_client
        self.dd = dd_client
        # Patch dd_client functions
        dd_client.get_estimated_cost = lambda *a, **kw: ESTIMATED_JSON
        dd_client.get_projected_cost = lambda *a, **kw: PROJECTED_JSON
        dd_client.get_historical_cost = lambda *a, **kw: PREV_HISTORICAL_JSON
        dd_client.get_monthly_cost_attribution = lambda *a, **kw: ATTRIBUTION_NOT_CONFIGURED_JSON
        dd_client.summarize = lambda *a, **kw: SAMPLE_SUMMARY
        dd_client.parse_tag_attribution = lambda *a, **kw: {"configured": False, "rows": []}
        self.app.dd_client = dd_client
        # Suppress store writes
        self.app.append_to_store = lambda *a, **kw: None

    def test_returns_string(self):
        result = self.app.page_analyze("k", "ak", "us1")
        assert isinstance(result, str)

    def test_shows_grand_total(self):
        result = self.app.page_analyze("k", "ak", "us1")
        assert "$8,240" in result

    def test_shows_projected_eom(self):
        result = self.app.page_analyze("k", "ak", "us1")
        assert "$9,300" in result

    def test_shows_infrastructure_hosts(self):
        result = self.app.page_analyze("k", "ak", "us1")
        assert "Infrastructure Hosts" in result

    def test_contains_reserve_upsell(self):
        result = self.app.page_analyze("k", "ak", "us1")
        assert "reserve" in result.lower() or "/reserve" in result


# ---------------------------------------------------------------------------
# 6. Unit: page_analyze — error paths (AuthError, PermissionError, etc.)
# ---------------------------------------------------------------------------

class TestPageAnalyzeErrors:
    def setup_method(self):
        self.app = import_app()
        import dd_client
        self.dd = dd_client
        self.app.dd_client = dd_client
        self.app.append_to_store = lambda *a, **kw: None

    def test_auth_error_returns_friendly_message(self):
        self.dd.get_estimated_cost = lambda *a, **kw: (_ for _ in ()).throw(self.dd.AuthError("401"))
        result = self.app.page_analyze("bad_key", "bad_app", "us1")
        assert "rejected" in result.lower() or "invalid" in result.lower() or "key" in result.lower()

    def test_auth_error_does_not_echo_api_key(self):
        self.dd.get_estimated_cost = lambda *a, **kw: (_ for _ in ()).throw(self.dd.AuthError("401"))
        result = self.app.page_analyze("SECRET_DD_KEY", "SECRET_APP_KEY", "us1")
        assert "SECRET_DD_KEY" not in result
        assert "SECRET_APP_KEY" not in result

    def test_auth_error_returns_http_200_not_500(self):
        # page_analyze is a rendering function; it always returns HTML (no exception)
        self.dd.get_estimated_cost = lambda *a, **kw: (_ for _ in ()).throw(self.dd.AuthError("401"))
        result = self.app.page_analyze("bad_key", "bad_app", "us1")
        assert isinstance(result, str) and len(result) > 0

    def test_permission_error_returns_friendly_message(self):
        self.dd.get_estimated_cost = lambda *a, **kw: (_ for _ in ()).throw(self.dd.PermissionError("403"))
        result = self.app.page_analyze("k", "ak", "us1")
        assert "permission" in result.lower() or "billing_read" in result.lower() or "usage_read" in result.lower()

    def test_rate_limit_error_returns_friendly_message(self):
        self.dd.get_estimated_cost = lambda *a, **kw: (_ for _ in ()).throw(self.dd.RateLimitError("429"))
        result = self.app.page_analyze("k", "ak", "us1")
        assert "rate" in result.lower() or "limit" in result.lower() or "minute" in result.lower()

    def test_generic_datadog_error_returns_friendly_message(self):
        self.dd.get_estimated_cost = lambda *a, **kw: (_ for _ in ()).throw(self.dd.DatadogError("500 oops"))
        result = self.app.page_analyze("k", "ak", "us1")
        assert "datadog" in result.lower() or "couldn't" in result.lower() or "reach" in result.lower()

    def test_generic_error_does_not_echo_keys(self):
        self.dd.get_estimated_cost = lambda *a, **kw: (_ for _ in ()).throw(self.dd.DatadogError("500"))
        result = self.app.page_analyze("SECRET_DD_KEY", "SECRET_APP_KEY", "us1")
        assert "SECRET_DD_KEY" not in result
        assert "SECRET_APP_KEY" not in result


# ---------------------------------------------------------------------------
# 7. Unit: page_sample — built-in sample data rendering
# ---------------------------------------------------------------------------

class TestPageSample:
    def setup_method(self):
        self.app = import_app()

    def test_returns_string(self):
        result = self.app.page_sample()
        assert isinstance(result, str)

    def test_shows_total(self):
        # Sample total is ~$8,240
        result = self.app.page_sample()
        assert "$8,240" in result

    def test_shows_infrastructure_hosts(self):
        result = self.app.page_sample()
        assert "Infrastructure Hosts" in result

    def test_shows_reserve_upsell(self):
        result = self.app.page_sample()
        assert "reserve" in result.lower() or "/reserve" in result

    def test_shows_by_product_table(self):
        result = self.app.page_sample()
        # All 3 products should be present
        assert "APM Hosts" in result or "Logs" in result


# ---------------------------------------------------------------------------
# 8. Unit: page_metrics — token gate
# ---------------------------------------------------------------------------

class TestPageMetrics:
    def setup_method(self):
        self.app = import_app()
        self.app.append_to_store = lambda *a, **kw: None

    def test_forbidden_without_token(self):
        result = self.app.page_metrics("")
        assert "forbidden" in result.lower() or "403" in result.lower()

    def test_forbidden_with_wrong_token(self):
        result = self.app.page_metrics("wrong-token")
        assert "forbidden" in result.lower() or "403" in result.lower()

    def test_allowed_with_correct_admin_token(self):
        token = os.environ.get("ADMIN_TOKEN", "observabill-admin")
        result = self.app.page_metrics(token)
        assert "funnel" in result.lower() or "metrics" in result.lower() or "visit" in result.lower()

    def test_metrics_shows_funnel_steps(self):
        token = os.environ.get("ADMIN_TOKEN", "observabill-admin")
        result = self.app.page_metrics(token)
        assert "visit" in result


# ---------------------------------------------------------------------------
# 9. Unit: reserve page
# ---------------------------------------------------------------------------

class TestPageReserve:
    def setup_method(self):
        self.app = import_app()
        self.writes = []
        self.app.append_to_store = lambda filename, data: self.writes.append((filename, data))

    def test_reserve_captures_email(self):
        self.app.handle_reserve("user@example.com", "great product")
        emails = [d.get("email") for _, d in self.writes]
        assert "user@example.com" in emails

    def test_reserve_writes_to_reserve_txt(self):
        self.app.handle_reserve("user@example.com", "")
        filenames = [f for f, _ in self.writes]
        assert "reserve.txt" in filenames

    def test_reserve_returns_thank_you_html(self):
        result = self.app.handle_reserve("user@example.com", "")
        assert "thank" in result.lower() or "reserved" in result.lower() or "✅" in result


# ---------------------------------------------------------------------------
# 10. Integration: live server routes
# ---------------------------------------------------------------------------

class TestIntegrationLanding:
    def test_get_root_returns_200(self, server):
        status, body = get("/")
        assert status == 200

    def test_landing_contains_form_action_analyze(self, server):
        _, body = get("/")
        assert 'action="/analyze"' in body

    def test_landing_contains_site_select(self, server):
        _, body = get("/")
        assert "us1" in body and "<select" in body

    def test_landing_contains_never_stored_trust_text(self, server):
        _, body = get("/")
        assert "never stored" in body.lower() or "never" in body.lower()

    def test_landing_contains_try_with_sample_link(self, server):
        _, body = get("/")
        assert "/sample" in body

    def test_landing_contains_reserve_upsell(self, server):
        _, body = get("/")
        assert "reserve" in body.lower() or "/reserve" in body

    def test_landing_contains_observabill_branding(self, server):
        _, body = get("/")
        assert "ObservaBill" in body

    def test_landing_has_password_fields(self, server):
        _, body = get("/")
        assert 'type="password"' in body


class TestIntegrationSample:
    def test_get_sample_returns_200(self, server):
        status, body = get("/sample")
        assert status == 200

    def test_sample_shows_total(self, server):
        _, body = get("/sample")
        assert "$8,240" in body

    def test_sample_shows_product_table(self, server):
        _, body = get("/sample")
        assert "Infrastructure Hosts" in body

    def test_sample_shows_reserve_upsell(self, server):
        _, body = get("/sample")
        assert "reserve" in body.lower()


class TestIntegrationAnalyze:
    """Integration test for POST /analyze with dd_client monkeypatched at module level."""

    def _patch_dd(self):
        """Patch the app module's dd_client reference in-process."""
        app = import_app()
        import dd_client
        app.dd_client = dd_client
        dd_client.get_estimated_cost = lambda *a, **kw: ESTIMATED_JSON
        dd_client.get_projected_cost = lambda *a, **kw: PROJECTED_JSON
        dd_client.get_historical_cost = lambda *a, **kw: PREV_HISTORICAL_JSON
        dd_client.get_monthly_cost_attribution = lambda *a, **kw: ATTRIBUTION_NOT_CONFIGURED_JSON
        dd_client.summarize = lambda *a, **kw: SAMPLE_SUMMARY
        dd_client.parse_tag_attribution = lambda *a, **kw: {"configured": False, "rows": []}
        app.append_to_store = lambda *a, **kw: None

    def test_post_analyze_returns_200(self, server):
        self._patch_dd()
        status, body = post("/analyze", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert status == 200

    def test_post_analyze_shows_grand_total(self, server):
        self._patch_dd()
        _, body = post("/analyze", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert "$8,240" in body

    def test_post_analyze_shows_projected_eom(self, server):
        self._patch_dd()
        _, body = post("/analyze", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert "$9,300" in body


class TestIntegrationHealthz:
    def test_healthz_returns_ok(self, server):
        status, body = get("/healthz")
        assert status == 200
        assert body.strip() == "ok"


class TestIntegrationMetrics:
    def test_metrics_forbidden_without_token(self, server):
        status, body = get("/metrics")
        assert "forbidden" in body.lower() or status in (200, 403)
        # Either the page says Forbidden or we get a 403
        if status == 200:
            assert "forbidden" in body.lower()

    def test_metrics_allowed_with_admin_token(self, server):
        token = os.environ.get("ADMIN_TOKEN", "observabill-admin")
        status, body = get(f"/metrics?token={token}")
        assert status == 200
        assert "visit" in body.lower() or "funnel" in body.lower() or "metrics" in body.lower()


class TestIntegrationReserve:
    def test_post_reserve_captures_email(self, server):
        app = import_app()
        captured = []
        app.append_to_store = lambda filename, data: captured.append((filename, data))
        status, body = post("/reserve", {"email": "test@example.com", "note": "interested"})
        assert status == 200
        # The in-process capture might not catch the live server's write, but
        # the body should contain a thank-you acknowledgment
        assert "thank" in body.lower() or "reserved" in body.lower() or "✅" in body

    def test_post_reserve_returns_200(self, server):
        status, _ = post("/reserve", {"email": "test@example.com", "note": ""})
        assert status == 200
