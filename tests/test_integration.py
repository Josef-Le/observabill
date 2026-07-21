"""
tests/test_integration.py — Savings Scanner Integration Tests (TDD Red → Green).

Covers:
  - Landing page: scan form + optional write-key field + sample link
  - GET /sample: renders savings dashboard ($3,500 total, lever titles)
  - POST /scan: with savings.scan monkeypatched, renders dashboard
  - POST /scan error path: friendly + no key echo
  - POST /apply: without write_key → 403 friendly
  - POST /apply: with write_key + confirm → calls dd_client.write once, no key leak
  - GET /breakdown: old analyze flow still works
  - 3-key security test: api_key, app_key, write_key never in HTML or store writes

Run: /opt/homebrew/opt/python@3.12/bin/python3.12 -m pytest tests/ -q
"""

import sys
import os
import json
import threading
import urllib.parse
import http.client

import pytest

# ---------------------------------------------------------------------------
# Test-port and helpers
# ---------------------------------------------------------------------------

TEST_PORT = 8932  # different from test_observabill.py (8931) and prod (8921)


def _raw(method, path, body=None, port=TEST_PORT):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    status = resp.status
    text = resp.read().decode("utf-8")
    conn.close()
    return status, text


def get(path, port=TEST_PORT):
    return _raw("GET", path, port=port)


def post(path, data: dict, port=TEST_PORT):
    body = urllib.parse.urlencode(data).encode()
    return _raw("POST", path, body=body, port=port)


def import_app():
    app_dir = os.path.join(os.path.dirname(__file__), "..")
    if os.path.abspath(app_dir) not in sys.path:
        sys.path.insert(0, os.path.abspath(app_dir))
    import app
    return app


# ---------------------------------------------------------------------------
# Fixture: minimal ScanResult
# ---------------------------------------------------------------------------

MINIMAL_SCAN = {
    "total_monthly_waste_usd": 3500.0,
    "currency": "USD",
    "region": "us",
    "opportunities": [
        {
            "id": "test-opp-001",
            "lever": "exclusion_filter",
            "category": "logs",
            "title": "Archive CDN/LB 2xx access logs to metrics",
            "summary": "High-volume logs can be safely archived.",
            "monthly_savings_usd": 3500.0,
            "savings_pct": "100%",
            "effort": "low",
            "confidence": "high",
            "evidence": [
                {"label": "cdn-prod", "volume": "18M events/day", "cost_usd": 3500.0}
            ],
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {"exclusion_filters": [{"name": "test-exclude"}]},
            },
            "needs_write_scope": True,
        }
    ],
    "sparkline": [100.0] * 30,
    "notes": [],
}


# ---------------------------------------------------------------------------
# Live server fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def server():
    import time
    app = import_app()
    srv = app.make_server(TEST_PORT)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    yield srv
    srv.shutdown()


# ---------------------------------------------------------------------------
# A. Landing page: new savings pitch
# ---------------------------------------------------------------------------

class TestLandingPageSavingsPitch:
    """Landing page must be the savings scanner, not the old breakdown form."""

    def test_landing_headline_mentions_wasted_spend(self, server):
        _, body = get("/")
        assert "wasted spend" in body.lower() or "waste" in body.lower()

    def test_landing_headline_mentions_free(self, server):
        _, body = get("/")
        assert "free" in body.lower()

    def test_landing_subtitle_mentions_read_only(self, server):
        _, body = get("/")
        assert "read-only" in body.lower() or "read only" in body.lower()

    def test_landing_form_posts_to_scan(self, server):
        _, body = get("/")
        assert 'action="/scan"' in body

    def test_landing_has_api_key_password_field(self, server):
        _, body = get("/")
        assert 'name="api_key"' in body

    def test_landing_has_app_key_password_field(self, server):
        _, body = get("/")
        assert 'name="app_key"' in body

    def test_landing_has_site_select(self, server):
        _, body = get("/")
        assert 'name="site"' in body and "us1" in body

    def test_landing_has_optional_write_key_field(self, server):
        _, body = get("/")
        assert 'name="write_key"' in body

    def test_landing_write_key_field_is_password_type(self, server):
        _, body = get("/")
        # write_key field must be type="password"
        assert 'name="write_key"' in body
        # Find the write_key input and confirm it has type=password
        idx = body.find('name="write_key"')
        surrounding = body[max(0, idx - 200): idx + 200]
        assert 'type="password"' in surrounding or "password" in surrounding

    def test_landing_has_trust_block_keys_never_stored(self, server):
        _, body = get("/")
        assert "never stored" in body.lower() or "never" in body.lower()

    def test_landing_has_sample_link(self, server):
        _, body = get("/")
        assert "/sample" in body

    def test_landing_has_reserve_upsell(self, server):
        _, body = get("/")
        assert "reserve" in body.lower() or "$99" in body

    def test_landing_returns_200(self, server):
        status, _ = get("/")
        assert status == 200


# ---------------------------------------------------------------------------
# B. GET /sample — savings dashboard, no keys required
# ---------------------------------------------------------------------------

class TestSampleRoute:
    """GET /sample must render the savings dashboard from fixtures.SAMPLE_SCAN."""

    def test_sample_returns_200(self, server):
        status, _ = get("/sample")
        assert status == 200

    def test_sample_shows_total_waste_3500(self, server):
        _, body = get("/sample")
        assert "$3,500" in body

    def test_sample_shows_at_least_one_lever_title(self, server):
        _, body = get("/sample")
        # SAMPLE_SCAN has 6 opportunities; first is CDN/LB
        assert "CDN" in body or "access logs" in body.lower() or "logs to metrics" in body.lower() or "archive" in body.lower()

    def test_sample_contains_dashboard_css(self, server):
        _, body = get("/sample")
        # DASHBOARD_CSS adds .hero-card
        assert "hero-card" in body or "hero-amount" in body or "lever-table" in body

    def test_sample_does_not_require_api_key(self, server):
        # GET request, no keys, must succeed
        status, _ = get("/sample")
        assert status == 200


# ---------------------------------------------------------------------------
# C. POST /scan — monkeypatched savings.scan
# ---------------------------------------------------------------------------

class TestScanRoute:
    """POST /scan must call savings.scan, wrap with html_page + DASHBOARD_CSS."""

    def setup_method(self):
        self.app = import_app()
        self.writes = []
        self.app.append_to_store = lambda f, d: self.writes.append((f, json.dumps(d)))
        import savings as _s
        self._orig_scan = _s.scan  # save for teardown

    def teardown_method(self):
        import savings as _s
        _s.scan = self._orig_scan  # restore

    def _patch_savings_scan(self, return_value=None):
        import savings
        if return_value is None:
            return_value = MINIMAL_SCAN
        savings.scan = lambda api_key, app_key, site, **kw: return_value
        self.app.savings = savings

    def test_scan_returns_200(self, server):
        self._patch_savings_scan()
        status, _ = post("/scan", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert status == 200

    def test_scan_renders_dashboard_total(self, server):
        self._patch_savings_scan()
        _, body = post("/scan", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert "$3,500" in body

    def test_scan_renders_lever_title(self, server):
        # v3 is pattern-first: a real scan renders the mined pattern templates
        # (leaderboard), not the old service-lever titles.
        import fixtures
        self._patch_savings_scan(fixtures.SAMPLE_SCAN)
        _, body = post("/scan", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert "No more objects to analyze" in body        # a mined pattern template
        assert "of your log bill" in body                  # the leaderboard framing

    def test_scan_includes_dashboard_css(self, server):
        self._patch_savings_scan()
        _, body = post("/scan", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert "hero-card" in body or "lever-table" in body

    def test_scan_logs_scan_event_with_site(self, server):
        self._patch_savings_scan()
        self.writes.clear()
        post("/scan", {"api_key": "k", "app_key": "ak", "site": "eu"})
        events = [json.loads(v) for _, v in self.writes]
        scan_events = [e for e in events if e.get("event") == "scan"]
        assert scan_events, "scan event not logged"
        assert any(e.get("site") == "eu" for e in scan_events)

    def test_scan_does_not_log_api_key(self, server):
        self._patch_savings_scan()
        self.writes.clear()
        post("/scan", {"api_key": "SECRETDDKEY", "app_key": "SECRETAPPKEY", "site": "us1"})
        combined = " ".join(v for _, v in self.writes)
        assert "SECRETDDKEY" not in combined
        assert "SECRETAPPKEY" not in combined

    def test_scan_does_not_echo_api_key_in_html(self, server):
        self._patch_savings_scan()
        _, body = post("/scan", {"api_key": "SECRETDDKEY", "app_key": "SECRETAPPKEY", "site": "us1"})
        assert "SECRETDDKEY" not in body
        assert "SECRETAPPKEY" not in body


# ---------------------------------------------------------------------------
# D. POST /scan — error paths
# ---------------------------------------------------------------------------

class TestScanErrorPaths:
    """POST /scan errors must return friendly HTML without echoing keys."""

    def setup_method(self):
        self.app = import_app()
        self.app.append_to_store = lambda *a, **kw: None
        import savings as _s
        self._orig_scan = _s.scan

    def teardown_method(self):
        import savings as _s
        _s.scan = self._orig_scan

    def _patch_savings_raises(self, exc_class):
        import savings
        import dd_client
        def _raise(*a, **kw):
            raise exc_class("boom")
        savings.scan = _raise
        self.app.savings = savings

    def test_auth_error_returns_friendly_message(self, server):
        import dd_client
        self._patch_savings_raises(dd_client.AuthError)
        _, body = post("/scan", {"api_key": "bad", "app_key": "bad", "site": "us1"})
        assert "rejected" in body.lower() or "invalid" in body.lower() or "authentication" in body.lower() or "key" in body.lower()

    def test_auth_error_does_not_echo_key(self, server):
        import dd_client
        self._patch_savings_raises(dd_client.AuthError)
        _, body = post("/scan", {"api_key": "MY_SECRET_KEY", "app_key": "MY_SECRET_APP", "site": "us1"})
        assert "MY_SECRET_KEY" not in body
        assert "MY_SECRET_APP" not in body

    def test_rate_limit_error_friendly(self, server):
        import dd_client
        self._patch_savings_raises(dd_client.RateLimitError)
        _, body = post("/scan", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert "rate" in body.lower() or "limit" in body.lower()

    def test_permission_error_friendly(self, server):
        import dd_client
        self._patch_savings_raises(dd_client.PermissionError)
        _, body = post("/scan", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert "permission" in body.lower() or "scope" in body.lower() or "access" in body.lower()

    def test_generic_error_friendly(self, server):
        import dd_client
        self._patch_savings_raises(dd_client.DatadogError)
        _, body = post("/scan", {"api_key": "k", "app_key": "ak", "site": "us1"})
        assert "datadog" in body.lower() or "couldn't" in body.lower() or "error" in body.lower()


# ---------------------------------------------------------------------------
# E. POST /apply — token-based flow (keys never leave server)
# ---------------------------------------------------------------------------

class TestApplyRoute:
    """
    POST /apply uses ephemeral server-side tokens (from POST /scan).
    Keys (api_key, app_key, write_key) must never appear in HTML or logs.
    """

    def setup_method(self):
        self.app = import_app()
        self.write_calls = []
        self.writes = []
        self.app.append_to_store = lambda f, d: self.writes.append((f, json.dumps(d)))
        import savings as _s
        import dd_client as _dd
        self._orig_scan = _s.scan
        self._orig_write = getattr(_dd, "write", None)

    def teardown_method(self):
        import savings as _s
        import dd_client as _dd
        _s.scan = self._orig_scan
        if self._orig_write is not None:
            _dd.write = self._orig_write

    def _patch_dd_write(self):
        import dd_client
        def _write(path, verb, payload, api_key, app_key, site):
            self.write_calls.append({
                "path": path, "verb": verb, "payload": payload, "site": site
            })
            return {"status": "ok"}
        dd_client.write = _write
        self.app.dd_client = dd_client

    def _patch_savings_scan(self, return_value=None):
        import savings, fixtures
        if return_value is None:
            return_value = fixtures.SAMPLE_SCAN
        savings.scan = lambda *a, **kw: return_value
        self.app.savings = savings

    def _do_scan_and_get_token(self, server, api_key="k", app_key="ak",
                                site="us1", write_key="WRITE_KEY_VALUE"):
        """POST /scan with a write_key; return the dashboard body."""
        self._patch_savings_scan()
        _, body = post("/scan", {
            "api_key": api_key, "app_key": app_key,
            "site": site, "write_key": write_key,
        })
        return body

    def _create_token_directly(self, api_key="k", app_key="ak",
                                site="us1", write_key="WRITE_KEY_VALUE"):
        """Create an apply session token directly via the app module."""
        return self.app._create_apply_session(api_key, app_key, site, write_key)

    # -- Token creation via /scan ------------------------------------------------

    def test_scan_with_write_key_dashboard_has_apply_form(self, server):
        """Dashboard rendered after /scan with write_key must contain an apply form."""
        body = self._do_scan_and_get_token(server)
        assert 'action="/apply"' in body

    def test_scan_with_write_key_dashboard_has_apply_token_field(self, server):
        """Dashboard must embed apply_token hidden field (not write_key)."""
        body = self._do_scan_and_get_token(server)
        assert 'name="apply_token"' in body

    def test_scan_with_write_key_dashboard_no_key_in_html(self, server):
        """No key value must appear in dashboard HTML after /scan."""
        body = self._do_scan_and_get_token(
            server, api_key="CANARY_DD", app_key="CANARY_APP", write_key="CANARY_WRITE"
        )
        assert "CANARY_DD" not in body
        assert "CANARY_APP" not in body
        assert "CANARY_WRITE" not in body

    # -- Bogus / expired token --------------------------------------------------

    def test_apply_without_token_returns_session_expired(self, server):
        """POST /apply with no token must return friendly 'session expired' page."""
        status, body = post("/apply", {"opp_id": "opp-1", "confirm": "1"})
        assert status == 200
        assert "session expired" in body.lower() or "expired" in body.lower() or "re-run" in body.lower()

    def test_apply_without_token_zero_writes(self, server):
        """POST /apply with no token must never call dd_client.write."""
        self._patch_dd_write()
        post("/apply", {"opp_id": "opp-1", "confirm": "1"})
        assert len(self.write_calls) == 0

    def test_apply_bogus_token_returns_session_expired(self, server):
        """POST /apply with bogus token must return friendly 'session expired' page."""
        status, body = post("/apply", {
            "opp_id": "opp-1", "apply_token": "bogus-token-xyz", "confirm": "1"
        })
        assert "expired" in body.lower() or "session" in body.lower() or "re-run" in body.lower()

    def test_apply_bogus_token_zero_writes(self, server):
        """POST /apply with bogus token must never call dd_client.write."""
        self._patch_dd_write()
        post("/apply", {
            "opp_id": "opp-1", "apply_token": "bogus-token-xyz", "confirm": "1"
        })
        assert len(self.write_calls) == 0

    # -- Valid token without confirm (shows confirmation page) ------------------

    def test_apply_with_token_no_confirm_shows_confirmation(self, server):
        """POST /apply with valid token but no confirm must show confirmation step."""
        self._patch_dd_write()
        token = self._create_token_directly()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        status, body = post("/apply", {"opp_id": opp_id, "apply_token": token})
        assert "confirm" in body.lower() or "apply" in body.lower()
        assert len(self.write_calls) == 0

    def test_apply_confirmation_page_has_no_keys(self, server):
        """Confirmation page must not embed any key values."""
        self._patch_dd_write()
        token = self._create_token_directly(
            api_key="CANARY_DD", app_key="CANARY_APP", write_key="CANARY_WRITE"
        )
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        _, body = post("/apply", {"opp_id": opp_id, "apply_token": token})
        assert "CANARY_DD" not in body
        assert "CANARY_APP" not in body
        assert "CANARY_WRITE" not in body

    def test_apply_confirmation_page_has_apply_token_field(self, server):
        """Confirmation page must carry apply_token forward (not any key)."""
        self._patch_dd_write()
        token = self._create_token_directly()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        _, body = post("/apply", {"opp_id": opp_id, "apply_token": token})
        assert 'name="apply_token"' in body

    def test_apply_confirmation_zero_writes(self, server):
        """No dd_client.write call on the confirmation step."""
        self._patch_dd_write()
        token = self._create_token_directly()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        post("/apply", {"opp_id": opp_id, "apply_token": token})
        assert len(self.write_calls) == 0

    # -- Valid token + confirm=1 (executes write) --------------------------------

    def test_apply_with_token_and_confirm_calls_dd_write(self, server):
        """POST /apply with valid token + confirm=1 must call dd_client.write once."""
        self._patch_dd_write()
        token = self._create_token_directly()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        status, body = post("/apply", {
            "opp_id": opp_id, "apply_token": token, "confirm": "1"
        })
        assert status == 200
        assert len(self.write_calls) == 1

    def test_apply_with_token_and_confirm_calls_write_exactly_once(self, server):
        """dd_client.write called exactly once on confirm."""
        self._patch_dd_write()
        token = self._create_token_directly()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        post("/apply", {"opp_id": opp_id, "apply_token": token, "confirm": "1"})
        assert len(self.write_calls) == 1

    def test_apply_success_html_has_no_keys(self, server):
        """Success page must not embed any key values."""
        self._patch_dd_write()
        token = self._create_token_directly(
            api_key="CANARY_DD", app_key="CANARY_APP", write_key="CANARY_WRITE"
        )
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        _, body = post("/apply", {
            "opp_id": opp_id, "apply_token": token, "confirm": "1"
        })
        assert "CANARY_DD" not in body
        assert "CANARY_APP" not in body
        assert "CANARY_WRITE" not in body

    def test_apply_does_not_log_write_key(self, server):
        """apply event in store must not contain any key value."""
        self._patch_dd_write()
        token = self._create_token_directly(write_key="MYSECRETWRITEKEY")
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        self.writes.clear()
        post("/apply", {"opp_id": opp_id, "apply_token": token, "confirm": "1"})
        combined = " ".join(v for _, v in self.writes)
        assert "MYSECRETWRITEKEY" not in combined

    def test_apply_logs_lever_in_apply_event(self, server):
        """apply event must include the lever field."""
        self._patch_dd_write()
        token = self._create_token_directly()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        self.writes.clear()
        post("/apply", {"opp_id": opp_id, "apply_token": token, "confirm": "1"})
        events = [json.loads(v) for _, v in self.writes]
        apply_events = [e for e in events if e.get("event") == "apply"]
        assert apply_events, "apply event not logged"
        assert any("lever" in e for e in apply_events)


# ---------------------------------------------------------------------------
# F. GET /breakdown — old bill breakdown still reachable
# ---------------------------------------------------------------------------

class TestBreakdownRoute:
    """The old /breakdown route must exist and serve the bill breakdown form."""

    def test_breakdown_returns_200(self, server):
        status, _ = get("/breakdown")
        assert status == 200

    def test_breakdown_has_analyze_form(self, server):
        _, body = get("/breakdown")
        # Either old form action=/analyze or /breakdown as form action
        assert 'action="/analyze"' in body or 'action="/breakdown"' in body or "breakdown" in body.lower()

    def test_breakdown_shows_product_link_or_nav(self, server):
        _, body = get("/breakdown")
        assert "breakdown" in body.lower() or "bill" in body.lower()


# ---------------------------------------------------------------------------
# G. Funnel order in page_metrics
# ---------------------------------------------------------------------------

class TestFunnelOrder:
    """page_metrics must list funnel events in the specified order."""

    def setup_method(self):
        self.app = import_app()
        self.app.append_to_store = lambda *a, **kw: None

    def test_metrics_funnel_order_includes_scan(self):
        token = self.app.ADMIN_TOKEN
        result = self.app.page_metrics(token)
        # Must include "scan" in the funnel display (even if 0 count)
        assert "scan" in result

    def test_metrics_funnel_order_includes_apply(self):
        token = self.app.ADMIN_TOKEN
        result = self.app.page_metrics(token)
        assert "apply" in result

    def test_metrics_funnel_order_scan_before_apply(self):
        token = self.app.ADMIN_TOKEN
        result = self.app.page_metrics(token)
        scan_pos = result.find(">scan<") if ">scan<" in result else result.find("scan")
        apply_pos = result.find(">apply<") if ">apply<" in result else result.find("apply")
        assert scan_pos < apply_pos


# ---------------------------------------------------------------------------
# H. 3-key security test (unit, no live server needed)
# ---------------------------------------------------------------------------

class TestThreeKeySecurityUnit:
    """
    api_key, app_key, AND write_key must never appear in:
    - rendered HTML from page_scan()
    - rendered HTML from page_apply()
    - append_to_store writes

    page_apply() now uses the server-side token flow: keys are stored in
    _apply_sessions and retrieved by token, never accepted from the client.
    """

    def setup_method(self):
        self.app = import_app()
        self.writes = []
        self.app.append_to_store = lambda f, d: self.writes.append((f, json.dumps(d)))
        import savings as _s
        import dd_client as _dd
        self._orig_scan = _s.scan
        self._orig_write = getattr(_dd, "write", None)

    def teardown_method(self):
        import savings as _s
        import dd_client as _dd
        _s.scan = self._orig_scan
        if self._orig_write is not None:
            _dd.write = self._orig_write

    def _patch_savings_success(self):
        import savings
        savings.scan = lambda *a, **kw: MINIMAL_SCAN
        self.app.savings = savings

    def _patch_dd_write_success(self):
        import dd_client
        write_calls = []
        def _write(path, verb, payload, api_key, app_key, site):
            write_calls.append({"path": path, "verb": verb})
            return {"status": "ok"}
        dd_client.write = _write
        self.app.dd_client = dd_client
        return write_calls

    def _make_token(self, api_key, app_key, site, write_key):
        """Create an apply session directly for unit tests."""
        return self.app._create_apply_session(api_key, app_key, site, write_key)

    # -- page_scan security tests -----------------------------------------------

    def test_scan_html_never_contains_api_key(self):
        self._patch_savings_success()
        result = self.app.page_scan("SECRETAPIKEY", "SECRETAPPKEY", "us1", write_key=None)
        assert "SECRETAPIKEY" not in result

    def test_scan_html_never_contains_app_key(self):
        self._patch_savings_success()
        result = self.app.page_scan("SECRETAPIKEY", "SECRETAPPKEY", "us1", write_key=None)
        assert "SECRETAPPKEY" not in result

    def test_scan_html_never_contains_write_key(self):
        self._patch_savings_success()
        result = self.app.page_scan("k", "ak", "us1", write_key="SECRETWRITEKEY")
        assert "SECRETWRITEKEY" not in result

    def test_scan_store_never_contains_api_key(self):
        self._patch_savings_success()
        self.writes.clear()
        self.app.page_scan("SECRETAPIKEY", "SECRETAPPKEY", "us1", write_key=None)
        combined = " ".join(v for _, v in self.writes)
        assert "SECRETAPIKEY" not in combined

    def test_scan_store_never_contains_app_key(self):
        self._patch_savings_success()
        self.writes.clear()
        self.app.page_scan("SECRETAPIKEY", "SECRETAPPKEY", "us1", write_key=None)
        combined = " ".join(v for _, v in self.writes)
        assert "SECRETAPPKEY" not in combined

    def test_scan_store_never_contains_write_key(self):
        self._patch_savings_success()
        self.writes.clear()
        self.app.page_scan("k", "ak", "us1", write_key="SECRETWRITEKEY")
        combined = " ".join(v for _, v in self.writes)
        assert "SECRETWRITEKEY" not in combined

    # -- page_apply security tests (new token flow) -----------------------------

    def test_apply_html_never_contains_api_key(self):
        """Keys stored server-side must not leak into apply success HTML."""
        self._patch_dd_write_success()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self._make_token("SECRETAPIKEY", "SECRETAPPKEY", "us1", "WK")
        result, _ = self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=True)
        assert "SECRETAPIKEY" not in result

    def test_apply_html_never_contains_app_key(self):
        self._patch_dd_write_success()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self._make_token("k", "SECRETAPPKEY", "us1", "WK")
        result, _ = self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=True)
        assert "SECRETAPPKEY" not in result

    def test_apply_html_never_contains_write_key(self):
        self._patch_dd_write_success()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self._make_token("k", "ak", "us1", "SECRETWRITEKEY")
        result, _ = self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=True)
        assert "SECRETWRITEKEY" not in result

    def test_apply_store_never_contains_write_key(self):
        self._patch_dd_write_success()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self._make_token("k", "ak", "us1", "SECRETWRITEKEY")
        self.writes.clear()
        self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=True)
        combined = " ".join(v for _, v in self.writes)
        assert "SECRETWRITEKEY" not in combined

    def test_apply_store_never_contains_api_key(self):
        self._patch_dd_write_success()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self._make_token("SECRETAPIKEY", "ak", "us1", "WK")
        self.writes.clear()
        self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=True)
        combined = " ".join(v for _, v in self.writes)
        assert "SECRETAPIKEY" not in combined

    # -- Confirmation page security (no confirm yet) ----------------------------

    def test_apply_confirm_page_no_api_key(self):
        """Confirmation page (no confirm yet) must not contain api_key."""
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self._make_token("SECRETAPIKEY", "ak", "us1", "WK")
        result, _ = self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=False)
        assert "SECRETAPIKEY" not in result

    def test_apply_confirm_page_no_write_key(self):
        """Confirmation page must not contain write_key."""
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self._make_token("k", "ak", "us1", "SECRETWRITEKEY")
        result, _ = self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=False)
        assert "SECRETWRITEKEY" not in result

    def test_apply_confirm_page_has_apply_token(self):
        """Confirmation page must carry the apply_token forward."""
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self._make_token("k", "ak", "us1", "WK")
        result, _ = self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=False)
        assert 'name="apply_token"' in result


# ---------------------------------------------------------------------------
# I. REGRESSION: canary 3-key leak test (FAIL-1 + FAIL-2 guard)
# ---------------------------------------------------------------------------

class TestCanaryKeyLeakRegression:
    """
    Regression test for QA-psycho FAIL-1 (keys in hidden fields) and FAIL-2
    (onclick XSS placeholder).

    Uses three distinctive canary strings. The test MUST FAIL on any code that
    embeds keys in HTML, and MUST PASS on the fixed token-based implementation.
    """

    CANARY_DD    = "CANARY_DD_KEY_99XZQ"
    CANARY_APP   = "CANARY_APP_KEY_77ABY"
    CANARY_WRITE = "CANARY_WRITE_KEY_55MNK"

    def setup_method(self):
        self.app = import_app()
        self.writes = []
        self.app.append_to_store = lambda f, d: self.writes.append((f, json.dumps(d)))
        import savings as _s
        import dd_client as _dd
        self._orig_scan = _s.scan
        self._orig_write = getattr(_dd, "write", None)

    def teardown_method(self):
        import savings as _s
        import dd_client as _dd
        _s.scan = self._orig_scan
        if self._orig_write is not None:
            _dd.write = self._orig_write

    def _patch_scan(self):
        import savings
        savings.scan = lambda *a, **kw: MINIMAL_SCAN
        self.app.savings = savings

    def _patch_write(self):
        import dd_client
        dd_client.write = lambda *a, **kw: {"status": "ok"}
        self.app.dd_client = dd_client

    def _canaries_in(self, text):
        found = []
        if self.CANARY_DD    in text: found.append("CANARY_DD")
        if self.CANARY_APP   in text: found.append("CANARY_APP")
        if self.CANARY_WRITE in text: found.append("CANARY_WRITE")
        return found

    # -- Dashboard HTML after /scan -------------------------------------------

    def test_dashboard_html_has_no_canary_keys(self):
        """After page_scan with all 3 canaries, none must appear in dashboard HTML."""
        self._patch_scan()
        result = self.app.page_scan(
            self.CANARY_DD, self.CANARY_APP, "us1", write_key=self.CANARY_WRITE
        )
        leaked = self._canaries_in(result)
        assert leaked == [], f"Canary keys leaked into dashboard HTML: {leaked}"

    def test_dashboard_html_no_onclick_key_embedding(self):
        """No onclick handler in dashboard must reference any key-like value."""
        self._patch_scan()
        result = self.app.page_scan(
            self.CANARY_DD, self.CANARY_APP, "us1", write_key=self.CANARY_WRITE
        )
        # Old FAIL-2: onclick="alert('Apply {oid} — wire to server endpoint')"
        assert "alert(" not in result, "onclick alert() placeholder still present in HTML"

    # -- Confirmation page HTML -----------------------------------------------

    def test_confirmation_page_html_has_no_canary_keys(self):
        """Confirmation page (confirm=False) must not embed any canary key."""
        self._patch_write()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self.app._create_apply_session(
            self.CANARY_DD, self.CANARY_APP, "us1", self.CANARY_WRITE
        )
        result, _ = self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=False)
        leaked = self._canaries_in(result)
        assert leaked == [], f"Canary keys leaked into confirmation HTML: {leaked}"

    def test_confirmation_page_has_no_onclick_placeholder(self):
        """Confirmation page must not have the old onclick alert() placeholder."""
        self._patch_write()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self.app._create_apply_session(
            self.CANARY_DD, self.CANARY_APP, "us1", self.CANARY_WRITE
        )
        result, _ = self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=False)
        assert "alert(" not in result

    # -- Success page HTML ----------------------------------------------------

    def test_success_page_html_has_no_canary_keys(self):
        """Apply success page must not embed any canary key."""
        self._patch_write()
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self.app._create_apply_session(
            self.CANARY_DD, self.CANARY_APP, "us1", self.CANARY_WRITE
        )
        result, _ = self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=True)
        leaked = self._canaries_in(result)
        assert leaked == [], f"Canary keys leaked into success HTML: {leaked}"

    # -- Store writes ---------------------------------------------------------

    def test_store_writes_have_no_canary_keys(self):
        """append_to_store must never write any canary key value."""
        self._patch_scan()
        self._patch_write()
        self.writes.clear()
        self.app.page_scan(
            self.CANARY_DD, self.CANARY_APP, "us1", write_key=self.CANARY_WRITE
        )
        import fixtures
        opp_id = fixtures.SAMPLE_SCAN["opportunities"][0]["id"]
        token = self.app._create_apply_session(
            self.CANARY_DD, self.CANARY_APP, "us1", self.CANARY_WRITE
        )
        self.app.page_apply(opp_id=opp_id, apply_token=token, confirm=True)
        combined = " ".join(v for _, v in self.writes)
        leaked = self._canaries_in(combined)
        assert leaked == [], f"Canary keys leaked into store writes: {leaked}"
