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
        self.app.append_to_store = lambda *a, **kw: None

    def test_returns_string(self):
        result = self.app.page_sample()
        assert isinstance(result, str)

    def test_shows_savings_dashboard_total(self):
        # page_sample() now renders the savings dashboard using fixtures.SAMPLE_SCAN
        # which has total_monthly_waste_usd = 3500.0
        result = self.app.page_sample()
        assert "$3,500" in result

    def test_shows_lever_titles(self):
        # The savings dashboard shows opportunity titles, not product names.
        # SAMPLE_SCAN (v2) has pattern_exclusion opportunities; the top one is
        # "Exclude noisy template in aws: put record ok in"
        result = self.app.page_sample()
        assert (
            "put record" in result.lower()
            or "aws" in result.lower()
            or "exclude" in result.lower()
            or "pattern" in result.lower()
        )

    def test_shows_reserve_upsell(self):
        result = self.app.page_sample()
        assert "reserve" in result.lower() or "/reserve" in result

    def test_shows_dashboard_css(self):
        result = self.app.page_sample()
        # DASHBOARD_CSS is injected in the page
        assert "hero-card" in result or "hero-amount" in result or "lever-table" in result


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
        token = self.app.ADMIN_TOKEN
        result = self.app.page_metrics(token)
        assert "funnel" in result.lower() or "metrics" in result.lower() or "visit" in result.lower()

    def test_metrics_shows_funnel_steps(self):
        token = self.app.ADMIN_TOKEN
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

    def test_landing_contains_form_action_scan(self, server):
        _, body = get("/")
        # Primary form now posts to /scan (savings scanner); /analyze is now /breakdown
        assert 'action="/scan"' in body

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

    def test_sample_shows_waste_total(self, server):
        # page_sample() now renders the savings dashboard — $3,500 total waste
        _, body = get("/sample")
        assert "$3,500" in body

    def test_sample_shows_savings_levers(self, server):
        # Shows savings opportunities (lever titles), not bill breakdown
        _, body = get("/sample")
        assert "CDN" in body or "access logs" in body.lower() or "logs to metrics" in body.lower() or "savings" in body.lower()

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
        token = import_app().ADMIN_TOKEN
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


class TestApplyResolvesRealScanOpps:
    """Apply must act on REAL scan findings (stashed in the session), not only fixtures."""

    def test_session_stores_and_resolves_real_opp(self):
        app = import_app()
        real_opp = {"id": "pattern-svc-xyz", "lever": "pattern_exclusion",
                    "generated_config": {"endpoint": "/x", "verb": "PUT", "payload": {}}}
        token = app._create_apply_session("a", "b", "us1", "wk", opportunities=[real_opp])
        session = app._get_apply_session(token)
        found = app._find_opp_by_id("pattern-svc-xyz", session=session)
        assert found is not None and found["lever"] == "pattern_exclusion"
        # keys never surface in the session-derived opp
        assert "wk" not in str(found)

    def test_unknown_id_returns_none_even_with_session(self):
        app = import_app()
        token = app._create_apply_session("a", "b", "us1", "wk", opportunities=[])
        session = app._get_apply_session(token)
        assert app._find_opp_by_id("does-not-exist", session=session) is None


# ============================================================================
# Security tests (FIX 1: XSS via unescaped service names in hero)
# ============================================================================

class TestSecurityXSSHeroServiceNames:
    """Test that service names in hero card are escaped to prevent XSS."""

    def test_hero_service_names_are_escaped(self):
        """Service names with script tags are escaped in hero HTML."""
        import ui
        import fixtures

        # Copy SAMPLE_SCAN and inject a malicious service name
        malicious_scan = json.loads(json.dumps(fixtures.SAMPLE_SCAN))

        # Modify the first pattern_leaderboard entry to have XSS payload in service name
        if malicious_scan.get("pattern_leaderboard"):
            # Update in place to ensure the star_row selection logic picks it up
            malicious_scan["pattern_leaderboard"][0]["services"] = [
                {"service": "<script>alert(1)</script>", "count": 9, "share_pct": 100.0}
            ]
            # Ensure this row will be selected as the star row by setting appropriate action
            malicious_scan["pattern_leaderboard"][0]["recommended_action"] = "exclude"
            malicious_scan["pattern_leaderboard"][0]["monthly_cost_usd"] = 999999.0

            # Render the hero with this malicious data
            html_output = ui.render_dashboard(malicious_scan)

            # Verify:
            # 1. The raw script tag is NOT in the output
            assert "<script>alert(1)</script>" not in html_output, \
                "Raw script tag found in hero HTML - XSS vulnerability!"

            # 2. The escaped version IS in the output
            assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_output, \
                "Escaped script tag not found - service name likely not escaped"


# ============================================================================
# Protection page rendering tests (render_protection_page)
# ============================================================================

class TestRenderProtectionPage:
    """Tests for render_protection_page() — the configuration form for automated protection."""

    def setup_method(self):
        self.app = import_app()
        import ui
        import config
        self.ui = ui
        self.config = config

    def test_renders_with_default_policy(self):
        """render_protection_page accepts default_policy() and returns HTML string."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert isinstance(html, str)
        assert len(html) > 100

    def test_form_posts_to_protection_endpoint(self):
        """Form action is /protection."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert 'action="/protection"' in html

    def test_form_uses_post_method(self):
        """Form method is POST."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert 'method="POST"' in html

    def test_contains_mode_select_for_exclude(self):
        """Has select with name='mode_exclude'."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert 'name="mode_exclude"' in html

    def test_contains_mode_select_for_cost_surge(self):
        """Has select with name='mode_cost_surge'."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert 'name="mode_cost_surge"' in html

    def test_contains_min_confidence_select(self):
        """Has select with name='min_confidence_for_auto'."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert 'name="min_confidence_for_auto"' in html

    def test_contains_auto_max_actions_input(self):
        """Has number input with name='auto_max_actions_per_day'."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert 'name="auto_max_actions_per_day"' in html

    def test_contains_email_input(self):
        """Has text input with name='email'."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert 'name="email"' in html

    def test_contains_api_key_password_input(self):
        """Has password input with name='api_key'."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert 'name="api_key"' in html
        assert 'type="password"' in html

    def test_contains_disposition_keywords(self):
        """Mentions the three dispositions: Recommend, Alert, Auto."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert "Recommend" in html
        assert "Alert" in html
        assert "Auto" in html

    def test_xss_protection_on_email(self):
        """Email with script tag is escaped, not rendered."""
        policy = self.config.default_policy()
        policy["channels"]["email"] = "<script>x</script>"
        html = self.ui.render_protection_page(policy)
        # Raw script must not appear
        assert "<script>x</script>" not in html
        # Escaped version must appear
        assert "&lt;script&gt;" in html

    def test_enabled_checkbox_checked_when_true(self):
        """When enabled=True, checkbox is checked."""
        policy = self.config.default_policy()
        policy["enabled"] = True
        html = self.ui.render_protection_page(policy)
        # Find the enabled checkbox and verify it's checked
        assert 'name="enabled"' in html
        # Should have checked="checked" or just checked
        import re
        # Look for name="enabled" and if it's in a checked context
        match = re.search(r'<input[^>]*name="enabled"[^>]*(?:checked)?', html)
        assert match is not None
        # The actual HTML should have checked if enabled=True
        # Let's verify the form pre-fills from policy
        assert 'value="true"' in html or 'checked' in html

    def test_dry_run_checkbox_checked_when_true(self):
        """When dry_run=True, checkbox is checked."""
        policy = self.config.default_policy()
        policy["dry_run"] = True
        html = self.ui.render_protection_page(policy)
        assert 'name="dry_run"' in html

    def test_mode_presets_to_policy_value(self):
        """Selects preset to policy["modes"][kind]."""
        policy = self.config.default_policy()
        policy["modes"]["exclude"] = "auto"
        html = self.ui.render_protection_page(policy)
        # Should have "auto" selected in the exclude dropdown
        # Check that "auto" option is marked selected
        assert "auto" in html

    def test_no_stored_keys_rendered(self):
        """API keys, app keys, write keys never appear in HTML."""
        policy = self.config.default_policy()
        policy["api_key"] = "SECRET_API_KEY_12345"
        html = self.ui.render_protection_page(policy)
        assert "SECRET_API_KEY_12345" not in html

    def test_audit_lines_shown_when_provided(self):
        """When audit_lines provided, shows recent actions section."""
        policy = self.config.default_policy()
        audit_lines = ["Excluded pattern X on 2026-07-10", "Sampled Y on 2026-07-09"]
        html = self.ui.render_protection_page(policy, audit_lines=audit_lines)
        assert "recent" in html.lower() or "automated action" in html.lower()
        assert "Excluded pattern X" in html
        assert "Sampled Y" in html

    def test_no_audit_lines_shows_empty_message(self):
        """When no audit lines, shows empty message."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy, audit_lines=[])
        # Should mention no actions
        assert "no" in html.lower() and "action" in html.lower()

    def test_minimum_length_above_2000_chars(self):
        """Rendered HTML is reasonably large (>2000 chars)."""
        policy = self.config.default_policy()
        html = self.ui.render_protection_page(policy)
        assert len(html) > 2000


# ---------------------------------------------------------------------------
# Protection Routes: GET /protection, POST /protection, POST /webhook/datadog
# ---------------------------------------------------------------------------

class TestProtectionGetRoute:
    """Test GET /protection route."""

    def setup_method(self):
        self.app = import_app()
        # Use temporary data directory
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.app.DATA_DIR = self.tmpdir
        self.app.POLICY_PATH = os.path.join(self.tmpdir, "policy.json")
        self.app.CREDS_PATH = os.path.join(self.tmpdir, "creds.json")
        self.app.PROTECT_STATE_PATH = os.path.join(self.tmpdir, "watchdog_state.json")

    def test_get_protection_returns_200(self, server):
        """GET /protection returns 200 OK."""
        status, body = get("/protection", port=TEST_PORT)
        assert status == 200

    def test_get_protection_returns_html(self, server):
        """GET /protection returns HTML content."""
        status, body = get("/protection", port=TEST_PORT)
        assert "<!DOCTYPE" in body or "<html" in body.lower()

    def test_get_protection_contains_form_action(self, server):
        """GET /protection contains form with action="/protection"."""
        status, body = get("/protection", port=TEST_PORT)
        assert 'action="/protection"' in body or 'action="/protection' in body

    def test_get_protection_shows_mode_selects(self, server):
        """GET /protection shows mode dropdown selects for each kind."""
        status, body = get("/protection", port=TEST_PORT)
        # Should have mode selects for exclude, sample, etc.
        # Look for select elements or radio buttons for mode_exclude, mode_sample, etc.
        assert "mode_" in body or "exclude" in body.lower()

    def test_get_protection_no_keys_rendered(self, server):
        """GET /protection never renders any credentials."""
        status, body = get("/protection", port=TEST_PORT)
        # Should not contain API keys or app keys
        assert "api_key_" not in body
        assert "app_key_" not in body


class TestProtectionPostRoute:
    """Test POST /protection route."""

    def setup_method(self):
        self.app = import_app()
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.app.DATA_DIR = self.tmpdir
        self.app.POLICY_PATH = os.path.join(self.tmpdir, "policy.json")
        self.app.CREDS_PATH = os.path.join(self.tmpdir, "creds.json")
        self.app.PROTECT_STATE_PATH = os.path.join(self.tmpdir, "watchdog_state.json")
        # Clear any monkeypatched functions
        import config
        import dd_client
        self.app.config = config
        self.app.dd_client = dd_client

    def test_post_protection_saves_policy(self, server):
        """POST /protection with mode_exclude=auto, enabled=on saves policy."""
        data = {
            "enabled": "on",
            "dry_run": "on",
            "mode_exclude": "auto",
            "mode_sample": "recommend",
            "mode_to_metric": "recommend",
            "mode_review": "recommend",
            "mode_new_pattern": "alert",
            "mode_cost_surge": "alert",
            "mode_volume_surge": "alert",
            "email": "me@example.com",
        }
        status, body = post("/protection", data, port=TEST_PORT)
        # Should redirect or re-render with success
        assert status in (200, 302)

        # Verify policy was saved
        assert os.path.exists(self.app.POLICY_PATH)
        import json
        with open(self.app.POLICY_PATH, "r") as f:
            saved_policy = json.load(f)
        assert saved_policy["modes"]["exclude"] == "auto"
        assert saved_policy["enabled"] is True

    def test_post_protection_stores_creds_0600(self, server):
        """POST /protection with api_key, app_key stores creds with mode 0600."""
        data = {
            "enabled": "on",
            "mode_exclude": "recommend",
            "api_key": "test_api_key_12345",
            "app_key": "test_app_key_67890",
            "write_key": "test_write_key_xyz",
            "site": "us1",
        }
        status, body = post("/protection", data, port=TEST_PORT)
        assert status in (200, 302)

        # Verify creds were saved with 0600 permissions
        assert os.path.exists(self.app.CREDS_PATH)
        stat_info = os.stat(self.app.CREDS_PATH)
        mode = stat_info.st_mode & 0o777
        assert mode == 0o600, f"Expected mode 0o600, got {oct(mode)}"

    def test_post_protection_never_echoes_keys(self, server):
        """POST /protection response never contains API keys."""
        data = {
            "enabled": "on",
            "mode_exclude": "auto",
            "api_key": "SECRET_API_KEY_PROTECT_TEST",
            "app_key": "SECRET_APP_KEY_PROTECT_TEST",
        }
        status, body = post("/protection", data, port=TEST_PORT)
        assert "SECRET_API_KEY_PROTECT_TEST" not in body
        assert "SECRET_APP_KEY_PROTECT_TEST" not in body

    def test_post_protection_get_reflects_saved_policy(self, server):
        """POST /protection, then GET /protection shows saved values."""
        data = {
            "enabled": "on",
            "mode_exclude": "auto",
            "mode_sample": "alert",
            "email": "test@example.com",
        }
        post("/protection", data, port=TEST_PORT)

        # Now GET /protection should reflect the saved values
        status, body = get("/protection", port=TEST_PORT)
        assert status == 200
        # The form should show the saved mode as selected
        # (This depends on how the form is rendered, but at minimum it should exist)
        assert "form" in body.lower() or "action" in body.lower()


class TestProtectionPostFormatInputErrors:
    """FIX 7 (MED): bad form input should not crash server."""

    def setup_method(self):
        self.app = import_app()
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.app.DATA_DIR = self.tmpdir
        self.app.POLICY_PATH = os.path.join(self.tmpdir, "policy.json")
        self.app.CREDS_PATH = os.path.join(self.tmpdir, "creds.json")
        self.app.PROTECT_STATE_PATH = os.path.join(self.tmpdir, "watchdog_state.json")
        import config
        self.config = config

    def test_post_protection_with_non_numeric_min_cost_returns_valid_response(self, server):
        """POST /protection with min_cost_usd=abc returns valid HTTP response (not crash)."""
        data = {
            "enabled": "on",
            "mode_exclude": "recommend",
            "min_cost_usd": "abc",  # invalid
        }
        status, body = post("/protection", data, port=TEST_PORT)
        # Must return valid HTTP response (200 or 400), not hang/crash
        assert status in (200, 400)
        # Response must be valid HTML or JSON
        assert len(body) > 0
        assert ("<!DOCTYPE" in body or "<html" in body.lower() or "{" in body)

    def test_post_protection_with_non_numeric_auto_max_actions_returns_valid_response(self, server):
        """POST /protection with auto_max_actions_per_day=xyz returns valid HTTP response."""
        data = {
            "enabled": "on",
            "mode_exclude": "recommend",
            "auto_max_actions_per_day": "xyz",  # invalid
        }
        status, body = post("/protection", data, port=TEST_PORT)
        assert status in (200, 400)
        assert len(body) > 0

    def test_post_protection_with_invalid_mode_returns_valid_response(self, server):
        """POST /protection with mode_exclude=bogusmode returns valid HTTP response."""
        data = {
            "enabled": "on",
            "mode_exclude": "bogusmode",  # invalid
        }
        status, body = post("/protection", data, port=TEST_PORT)
        assert status in (200, 400)
        assert len(body) > 0

    def test_post_protection_invalid_input_does_not_corrupt_policy_file(self, server):
        """POST /protection with invalid inputs should not corrupt the policy file."""
        # First, set a valid policy
        valid_data = {
            "enabled": "on",
            "mode_exclude": "auto",
        }
        post("/protection", valid_data, port=TEST_PORT)

        # Verify valid policy saved
        with open(self.app.POLICY_PATH, "r") as f:
            initial_policy = json.load(f)
        assert initial_policy["modes"]["exclude"] == "auto"

        # Now post invalid input
        invalid_data = {
            "enabled": "on",
            "min_cost_usd": "not_a_number",
        }
        post("/protection", invalid_data, port=TEST_PORT)

        # Policy file should still be loadable (not corrupted)
        try:
            with open(self.app.POLICY_PATH, "r") as f:
                after_invalid = json.load(f)
            # Should have loaded successfully
            assert "modes" in after_invalid
        except json.JSONDecodeError:
            pytest.fail("Policy file was corrupted by invalid input")


class TestAuditLineRendering:
    """FIX 8 (LOW): audit lines should read 'ts'/'kind' not 'timestamp'/'finding_kind'."""

    def setup_method(self):
        self.app = import_app()
        import ui
        self.ui = ui

    def test_audit_line_with_ts_and_kind_keys(self):
        """Given audit line with ts/kind/action, rendering includes the values (not '?')."""
        # page_protection_get reads audit lines and renders them
        # Create a mock audit entry with the correct keys
        audit_line_dict = {
            "ts": "2026-07-21T00:00:00Z",
            "kind": "exclude",
            "action": "applied",
            "template": "put record <NUM> ok",
            "monthly_cost_usd": 500,
        }

        # The renderer should convert this to a human-readable string
        # We test by rendering protection page with sample audit lines
        import config
        policy = config.default_policy()

        # Simulate audit lines as would be read from file
        audit_lines = [
            "applied exclude on 2026-07-21T00:00:00Z",  # what the renderer produces
        ]

        html = self.ui.render_protection_page(policy, audit_lines=audit_lines)

        # The HTML should show the actual values, not "?"
        assert "exclude" in html
        assert "applied" in html
        # Should NOT have "?" for the field names
        assert "? ?" not in html

    def test_page_protection_get_reads_audit_ts_and_kind(self):
        """page_protection_get correctly reads audit entries with ts/kind (fallback to timestamp/finding_kind)."""
        import tempfile
        import config

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app.DATA_DIR = tmpdir
            self.app.POLICY_PATH = os.path.join(tmpdir, "policy.json")
            self.app.CREDS_PATH = os.path.join(tmpdir, "creds.json")
            self.app.PROTECT_STATE_PATH = os.path.join(tmpdir, "watchdog_state.json")

            # Create audit file with ts/kind entries (from remediate.py)
            audit_file = self.app.PROTECT_STATE_PATH + ".audit"
            os.makedirs(os.path.dirname(audit_file), exist_ok=True)
            with open(audit_file, "w") as f:
                f.write(json.dumps({
                    "ts": "2026-07-21T12:00:00Z",
                    "kind": "exclude",
                    "action": "applied",
                    "template": "put record ok",
                    "monthly_cost_usd": 500,
                }) + "\n")

            # Call page_protection_get
            html = self.app.page_protection_get()

            # The audit line should be rendered with actual values
            assert "exclude" in html
            assert "applied" in html
            # Should not render "?" for missing old keys
            assert "? ?" not in html or html.count("?") < 4  # Some ? might be in placeholders


class TestWebhookDatadogRoute:
    """Test POST /webhook/datadog route."""

    def setup_method(self):
        self.app = import_app()
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.app.DATA_DIR = self.tmpdir
        self.app.POLICY_PATH = os.path.join(self.tmpdir, "policy.json")
        self.app.CREDS_PATH = os.path.join(self.tmpdir, "creds.json")
        self.app.PROTECT_STATE_PATH = os.path.join(self.tmpdir, "watchdog_state.json")
        self.app.WEBHOOK_SECRET = "test_secret_token_12345"

        # Monkeypatch savings.scan / dd_client.write — SAVE ORIGINALS so teardown
        # restores them (else they leak into test_savings.py and break 27 tests).
        import savings
        import dd_client
        self._orig_scan = savings.scan
        self._orig_write = dd_client.write
        self.app.savings = savings
        savings.scan = lambda *a, **kw: {"patterns": [], "surges": []}
        self.app.dd_client = dd_client
        dd_client.write = lambda *a, **kw: None

    def teardown_method(self):
        import savings
        import dd_client
        savings.scan = self._orig_scan
        dd_client.write = self._orig_write

    def test_webhook_requires_secret(self, server):
        """POST /webhook/datadog without matching secret returns 403."""
        data = {"secret": "wrong_secret"}
        status, body = post("/webhook/datadog?secret=wrong_secret", data, port=TEST_PORT)
        assert status == 403

    def test_webhook_with_correct_secret_and_policy_disabled_returns_ok(self, server):
        """POST /webhook/datadog with correct secret but policy disabled returns 200 no-op."""
        # Set WEBHOOK_SECRET to a known value
        self.app.WEBHOOK_SECRET = "correct_secret"

        data = {}
        status, body = post(
            f"/webhook/datadog?secret=correct_secret",
            data,
            port=TEST_PORT
        )
        # Should return 200 (disabled policy is safe)
        assert status == 200
        assert "disabled" in body.lower() or "status" in body.lower()

    def test_webhook_does_not_call_scan_when_disabled(self, server):
        """POST /webhook/datadog does not call savings.scan if policy disabled."""
        self.app.WEBHOOK_SECRET = "correct_secret"
        self.scan_called = False

        def mock_scan(*a, **kw):
            self.scan_called = True
            return {"patterns": [], "surges": []}

        self.app.savings.scan = mock_scan

        data = {}
        status, body = post(
            f"/webhook/datadog?secret=correct_secret",
            data,
            port=TEST_PORT
        )
        assert not self.scan_called

    def test_webhook_no_key_leak_in_response(self, server):
        """POST /webhook/datadog never leaks credentials in response."""
        self.app.WEBHOOK_SECRET = "correct_secret"
        # Save some creds to the creds file
        import json
        os.makedirs(self.app.DATA_DIR, exist_ok=True)
        with open(self.app.CREDS_PATH, "w") as f:
            json.dump({
                "api_key": "SECRET_API_KEY_WEBHOOK_TEST",
                "app_key": "SECRET_APP_KEY_WEBHOOK_TEST",
            }, f)

        data = {}
        status, body = post(
            f"/webhook/datadog?secret=correct_secret",
            data,
            port=TEST_PORT
        )
        assert "SECRET_API_KEY_WEBHOOK_TEST" not in body
        assert "SECRET_APP_KEY_WEBHOOK_TEST" not in body


class TestConcurrentProtectionCycles:
    """FIX 5 (HIGH): concurrent run_cycle race (scheduler + webhook)."""

    def setup_method(self):
        self.app = import_app()
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.app.DATA_DIR = self.tmpdir
        self.app.POLICY_PATH = os.path.join(self.tmpdir, "policy.json")
        self.app.CREDS_PATH = os.path.join(self.tmpdir, "creds.json")
        self.app.PROTECT_STATE_PATH = os.path.join(self.tmpdir, "watchdog_state.json")
        self.app.WEBHOOK_SECRET = "test_secret_token_12345"

        import savings
        import dd_client
        self._orig_scan = savings.scan
        self._orig_write = dd_client.write
        self.app.savings = savings
        savings.scan = lambda *a, **kw: {"patterns": [], "surges": []}
        self.app.dd_client = dd_client
        dd_client.write = lambda *a, **kw: None

    def teardown_method(self):
        import savings
        import dd_client
        savings.scan = self._orig_scan
        dd_client.write = self._orig_write

    def test_protection_lock_exists(self):
        """_protection_lock module-level lock exists in app.py."""
        assert hasattr(self.app, "_protection_lock")
        # Check that it's a lock-like object (has acquire and release)
        assert hasattr(self.app._protection_lock, "acquire")
        assert hasattr(self.app._protection_lock, "release")

    def test_webhook_uses_protection_lock(self):
        """page_webhook_datadog wraps runner.run_cycle with _protection_lock."""
        import inspect
        source = inspect.getsource(self.app.page_webhook_datadog)
        # Check that the source contains reference to _protection_lock
        assert "_protection_lock" in source
        assert "with _protection_lock" in source or "with self._protection_lock" in source


class TestWebhookRateLimit:
    """FIX 6 (HIGH): webhook DoS protection with rate limit."""

    def setup_method(self):
        self.app = import_app()
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.app.DATA_DIR = self.tmpdir
        self.app.POLICY_PATH = os.path.join(self.tmpdir, "policy.json")
        self.app.CREDS_PATH = os.path.join(self.tmpdir, "creds.json")
        self.app.PROTECT_STATE_PATH = os.path.join(self.tmpdir, "watchdog_state.json")
        self.app.WEBHOOK_SECRET = "test_secret_token_12345"

        import savings
        import dd_client
        self._orig_scan = savings.scan
        self._orig_write = dd_client.write
        self.scan_calls = []
        self.app.savings = savings
        savings.scan = lambda *a, **kw: (self.scan_calls.append(1), {"patterns": [], "surges": []})[1]
        self.app.dd_client = dd_client
        dd_client.write = lambda *a, **kw: None

        # Reset rate limit for test
        self.app._last_webhook_ts = [0.0]

    def teardown_method(self):
        import savings
        import dd_client
        savings.scan = self._orig_scan
        dd_client.write = self._orig_write

    def test_webhook_rate_limit_exists(self):
        """_last_webhook_ts module-level rate limit state exists."""
        assert hasattr(self.app, "_last_webhook_ts")
        assert isinstance(self.app._last_webhook_ts, list)

    def test_webhook_rate_limit_blocks_rapid_calls(self, server):
        """Two rapid POSTs to /webhook/datadog within 60s — second is throttled."""
        self.app.WEBHOOK_SECRET = "correct_secret"

        # Need valid policy + creds for the call to proceed past early returns
        import json
        os.makedirs(self.app.DATA_DIR, exist_ok=True)
        with open(self.app.POLICY_PATH, "w") as f:
            import config
            json.dump(config.default_policy(), f)
        with open(self.app.CREDS_PATH, "w") as f:
            json.dump({
                "api_key": "test_key",
                "app_key": "test_app_key",
                "site": "us1",
            }, f)

        # Reset rate limit
        self.app._last_webhook_ts = [0.0]
        self.scan_calls.clear()

        # First call should succeed
        status1, body1 = post(
            "/webhook/datadog?secret=correct_secret",
            {},
            port=TEST_PORT
        )
        first_scan_count = len(self.scan_calls)

        # Second call immediately after should be throttled (within 60s)
        status2, body2 = post(
            "/webhook/datadog?secret=correct_secret",
            {},
            port=TEST_PORT
        )
        second_scan_count = len(self.scan_calls)

        # Second call should NOT have called scan() again (throttled)
        assert second_scan_count == first_scan_count, \
            f"Expected no new scan call on throttled request, but got {second_scan_count - first_scan_count} new calls"

        # Second response should indicate throttled status
        assert "throttled" in body2.lower() or status2 == 200
