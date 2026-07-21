"""TDD tests for watchdog — RED phase."""
import json
import os
import tempfile
from pathlib import Path

import pytest

# Import watchdog module (will exist after implementation)
# from watchdog import fingerprint, load_state, save_state, check_for_alerts, dedup_alerts, update_baselines, format_alert, run_watchdog_once


def test_fingerprint_deterministic():
    """fingerprint(template) returns 16-char hex, deterministic."""
    from watchdog import fingerprint

    fp1 = fingerprint("SELECT * FROM users WHERE <id> = ?")
    fp2 = fingerprint("SELECT * FROM users WHERE <id> = ?")
    assert fp1 == fp2
    assert len(fp1) == 16
    assert all(c in "0123456789abcdef" for c in fp1)


def test_fingerprint_different_templates():
    """Different templates yield different fingerprints."""
    from watchdog import fingerprint

    fp1 = fingerprint("template_a")
    fp2 = fingerprint("template_b")
    assert fp1 != fp2


def test_load_state_missing_file():
    """load_state returns {} if file does not exist."""
    from watchdog import load_state

    state = load_state("/nonexistent/path/state.json")
    assert state == {}


def test_load_state_valid_file():
    """load_state parses JSON from an existing file."""
    from watchdog import load_state

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "state.json")
        data = {"baselines": {"key": "value"}, "sent_ids": []}
        with open(path, "w") as f:
            json.dump(data, f)

        state = load_state(path)
        assert state == data


def test_save_state_creates_file_with_0600_perms():
    """save_state writes JSON file with mode 0600."""
    from watchdog import save_state

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "state.json")
        data = {"baselines": {}, "sent_ids": ["alert1"]}
        save_state(path, data)

        assert os.path.exists(path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded == data

        # Check file permissions are 0600
        mode = os.stat(path).st_mode
        perms = oct(mode)[-3:]
        assert perms == "600"


def test_check_for_alerts_new_pattern_above_threshold():
    """NEW pattern with monthly_cost >= min_new_cost triggers alert."""
    from watchdog import check_for_alerts, fingerprint

    template = "SELECT * FROM users WHERE <id> = ?"
    fp = fingerprint(template)

    scan_result = {
        "scan_time": "2026-07-14T10:00:00Z",
        "pattern_leaderboard": [
            {
                "template": template,
                "monthly_cost_usd": 500.0,
                "monthly_events": 1000,
                "recommended_action": "investigate",
            }
        ],
        "surges": [],
    }
    baselines = {"templates": {}, "updated": "2026-07-14T09:00:00Z"}

    alerts = check_for_alerts(scan_result, baselines, min_new_cost=100.0)

    assert len(alerts) == 1
    assert alerts[0]["kind"] == "new_pattern"
    assert alerts[0]["template"] == template
    assert alerts[0]["fingerprint"] == fp
    assert alerts[0]["monthly_cost_usd"] == 500.0
    assert alerts[0]["onset"] == "2026-07-14T10:00:00Z"
    assert "alert_id" in alerts[0]


def test_check_for_alerts_new_pattern_below_threshold():
    """NEW pattern with monthly_cost < min_new_cost → no alert."""
    from watchdog import check_for_alerts

    template = "SELECT * FROM <table> WHERE id = ?"

    scan_result = {
        "scan_time": "2026-07-14T10:00:00Z",
        "pattern_leaderboard": [
            {
                "template": template,
                "monthly_cost_usd": 10.0,
                "monthly_events": 100,
                "recommended_action": "monitor",
            }
        ],
        "surges": [],
    }
    baselines = {"templates": {}, "updated": "2026-07-14T09:00:00Z"}

    alerts = check_for_alerts(scan_result, baselines, min_new_cost=100.0)

    assert len(alerts) == 0


def test_check_for_alerts_cost_surge():
    """Known pattern with cost >= surge_ratio * baseline → cost_surge alert."""
    from watchdog import check_for_alerts, fingerprint

    template = "SELECT * FROM users WHERE <id> = ?"
    fp = fingerprint(template)

    scan_result = {
        "scan_time": "2026-07-14T10:00:00Z",
        "pattern_leaderboard": [
            {
                "template": template,
                "monthly_cost_usd": 300.0,
                "monthly_events": 3000,
                "recommended_action": "investigate",
            }
        ],
        "surges": [],
    }
    baselines = {
        "templates": {
            fp: {
                "template": template,
                "monthly_cost_usd": 100.0,
                "monthly_events": 1000,
                "first_seen": "2026-07-10T00:00:00Z",
                "last_seen": "2026-07-13T00:00:00Z",
            }
        },
        "updated": "2026-07-13T00:00:00Z",
    }

    alerts = check_for_alerts(scan_result, baselines, min_new_cost=100.0, surge_ratio=1.30)

    assert len(alerts) == 1
    assert alerts[0]["kind"] == "cost_surge"
    assert alerts[0]["template"] == template
    assert alerts[0]["monthly_cost_usd"] == 300.0
    assert alerts[0]["baseline_cost"] == 100.0
    assert alerts[0]["ratio"] == 3.0


def test_check_for_alerts_stable_pattern_no_surge():
    """Known pattern with stable or minor cost → no alert."""
    from watchdog import check_for_alerts, fingerprint

    template = "SELECT * FROM users WHERE <id> = ?"
    fp = fingerprint(template)

    scan_result = {
        "scan_time": "2026-07-14T10:00:00Z",
        "pattern_leaderboard": [
            {
                "template": template,
                "monthly_cost_usd": 110.0,  # Only 10% increase
                "monthly_events": 1100,
                "recommended_action": "monitor",
            }
        ],
        "surges": [],
    }
    baselines = {
        "templates": {
            fp: {
                "template": template,
                "monthly_cost_usd": 100.0,
                "monthly_events": 1000,
                "first_seen": "2026-07-10T00:00:00Z",
                "last_seen": "2026-07-13T00:00:00Z",
            }
        },
        "updated": "2026-07-13T00:00:00Z",
    }

    alerts = check_for_alerts(scan_result, baselines, surge_ratio=1.30)

    assert len(alerts) == 0


def test_check_for_alerts_volume_surge():
    """Volume anomaly in surges list becomes volume_surge alert."""
    from watchdog import check_for_alerts, fingerprint

    series_id = "some_series"

    scan_result = {
        "scan_time": "2026-07-14T10:00:00Z",
        "pattern_leaderboard": [],
        "surges": [
            {
                "series_id": series_id,
                "monthly_cost_usd": 75.0,
                "template": "Error: <timeout>",
            }
        ],
    }
    baselines = {"templates": {}, "updated": "2026-07-14T09:00:00Z"}

    alerts = check_for_alerts(scan_result, baselines, min_new_cost=50.0)

    assert len(alerts) == 1
    assert alerts[0]["kind"] == "volume_surge"
    assert alerts[0]["monthly_cost_usd"] == 75.0


def test_dedup_alerts_filters_sent():
    """dedup_alerts removes alerts whose alert_id is in sent_ids."""
    from watchdog import dedup_alerts, fingerprint

    template = "SELECT * FROM <table>"
    fp = fingerprint(template)

    alert_id_1 = fingerprint("new_pattern|" + template)
    alert_id_2 = fingerprint("cost_surge|" + template)

    alerts = [
        {"alert_id": alert_id_1, "kind": "new_pattern", "template": template},
        {"alert_id": alert_id_2, "kind": "cost_surge", "template": template},
    ]
    sent = {alert_id_1}

    fresh = dedup_alerts(alerts, sent)

    assert len(fresh) == 1
    assert fresh[0]["alert_id"] == alert_id_2


def test_dedup_alerts_empty_sent():
    """dedup_alerts with empty sent_ids returns all alerts."""
    from watchdog import dedup_alerts

    alerts = [
        {"alert_id": "id1", "kind": "new_pattern"},
        {"alert_id": "id2", "kind": "cost_surge"},
    ]

    fresh = dedup_alerts(alerts, set())

    assert len(fresh) == 2


def test_update_baselines_new_pattern():
    """update_baselines adds new leaderboard patterns to baselines."""
    from watchdog import update_baselines, fingerprint

    template = "SELECT * FROM users WHERE <id> = ?"
    fp = fingerprint(template)

    scan_result = {
        "pattern_leaderboard": [
            {
                "template": template,
                "monthly_cost_usd": 500.0,
                "monthly_events": 1000,
                "recommended_action": "investigate",
            }
        ]
    }
    baselines = {"templates": {}, "updated": "2026-07-13T00:00:00Z"}
    now = "2026-07-14T10:00:00Z"

    updated = update_baselines(baselines, scan_result, now)

    assert fp in updated["templates"]
    assert updated["templates"][fp]["template"] == template
    assert updated["templates"][fp]["monthly_cost_usd"] == 500.0
    assert updated["templates"][fp]["monthly_events"] == 1000
    assert updated["templates"][fp]["first_seen"] == now
    assert updated["templates"][fp]["last_seen"] == now
    assert updated["updated"] == now


def test_update_baselines_preserves_first_seen():
    """update_baselines preserves first_seen for existing patterns."""
    from watchdog import update_baselines, fingerprint

    template = "SELECT * FROM users WHERE <id> = ?"
    fp = fingerprint(template)
    first_seen = "2026-07-10T00:00:00Z"

    scan_result = {
        "pattern_leaderboard": [
            {
                "template": template,
                "monthly_cost_usd": 600.0,
                "monthly_events": 1200,
                "recommended_action": "investigate",
            }
        ]
    }
    baselines = {
        "templates": {
            fp: {
                "template": template,
                "monthly_cost_usd": 500.0,
                "monthly_events": 1000,
                "first_seen": first_seen,
                "last_seen": "2026-07-13T00:00:00Z",
            }
        },
        "updated": "2026-07-13T00:00:00Z",
    }
    now = "2026-07-14T10:00:00Z"

    updated = update_baselines(baselines, scan_result, now)

    assert updated["templates"][fp]["first_seen"] == first_seen
    assert updated["templates"][fp]["last_seen"] == now
    assert updated["templates"][fp]["monthly_cost_usd"] == 600.0


def test_format_alert_new_pattern():
    """format_alert produces email subject/body."""
    from watchdog import format_alert

    alert = {
        "kind": "new_pattern",
        "template": "SELECT * FROM users WHERE <id> = ?",
        "monthly_cost_usd": 500.0,
        "fingerprint": "abc123def456",
    }

    result = format_alert(alert)

    assert "subject" in result
    assert "body" in result
    assert "pattern" in result["subject"].lower()  # Should mention "pattern"
    assert "$500" in result["body"] or "500" in result["body"]
    assert "abc123def456" in result["body"]
    # Must not leak secrets
    assert "api_key" not in result["body"].lower()


def test_format_alert_with_exclude_base_url():
    """format_alert includes link when exclude_base_url provided."""
    from watchdog import format_alert

    alert = {
        "kind": "cost_surge",
        "template": "Error: <timeout>",
        "monthly_cost_usd": 300.0,
        "baseline_cost": 100.0,
        "fingerprint": "fp_xyz",
    }
    exclude_base = "https://example.com/observabill"

    result = format_alert(alert, exclude_base_url=exclude_base)

    assert "subject" in result
    assert "body" in result
    # Link should be in body
    assert "https://" in result["body"] or "example.com" in result["body"]


def test_run_watchdog_once_fires_new_alert():
    """run_watchdog_once fires alert on first scan."""
    from watchdog import run_watchdog_once, fingerprint

    template = "SELECT * FROM users WHERE <id> = ?"
    fp = fingerprint(template)

    def mock_scan_fn():
        return {
            "scan_time": "2026-07-14T10:00:00Z",
            "pattern_leaderboard": [
                {
                    "template": template,
                    "monthly_cost_usd": 500.0,
                    "monthly_events": 1000,
                    "recommended_action": "investigate",
                }
            ],
            "surges": [],
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "state.json")

        # Track notify calls
        notified = []
        def mock_notify(formatted):
            notified.append(formatted)

        alerts = run_watchdog_once(
            mock_scan_fn,
            state_path,
            "2026-07-14T10:00:00Z",
            notify_fn=mock_notify,
        )

        assert len(alerts) == 1
        assert alerts[0]["kind"] == "new_pattern"
        assert len(notified) == 1  # One notification sent

        # State file should exist
        assert os.path.exists(state_path)


def test_run_watchdog_once_dedup_on_second_run():
    """run_watchdog_once deduplicates on second run with same scan."""
    from watchdog import run_watchdog_once

    template = "SELECT * FROM users WHERE <id> = ?"

    def mock_scan_fn():
        return {
            "scan_time": "2026-07-14T10:00:00Z",
            "pattern_leaderboard": [
                {
                    "template": template,
                    "monthly_cost_usd": 500.0,
                    "monthly_events": 1000,
                    "recommended_action": "investigate",
                }
            ],
            "surges": [],
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "state.json")

        notified_count = [0]
        def mock_notify(formatted):
            notified_count[0] += 1

        # First run
        alerts_1 = run_watchdog_once(
            mock_scan_fn,
            state_path,
            "2026-07-14T10:00:00Z",
            notify_fn=mock_notify,
        )
        assert len(alerts_1) == 1
        first_notify = notified_count[0]

        # Second run with same scan (unchanged)
        alerts_2 = run_watchdog_once(
            mock_scan_fn,
            state_path,
            "2026-07-14T11:00:00Z",  # Different time, same scan
            notify_fn=mock_notify,
        )

        # No new alerts
        assert len(alerts_2) == 0
        # No new notification
        assert notified_count[0] == first_notify


def test_run_watchdog_once_state_persisted():
    """run_watchdog_once persists state to file."""
    from watchdog import run_watchdog_once
    import json

    template = "SELECT * FROM users WHERE <id> = ?"

    def mock_scan_fn():
        return {
            "scan_time": "2026-07-14T10:00:00Z",
            "pattern_leaderboard": [
                {
                    "template": template,
                    "monthly_cost_usd": 500.0,
                    "monthly_events": 1000,
                    "recommended_action": "investigate",
                }
            ],
            "surges": [],
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "state.json")

        run_watchdog_once(
            mock_scan_fn,
            state_path,
            "2026-07-14T10:00:00Z",
            notify_fn=lambda x: None,
        )

        with open(state_path) as f:
            state = json.load(f)

        assert "baselines" in state
        assert "sent_ids" in state
        assert len(state["sent_ids"]) == 1


def test_security_no_raw_content_persisted():
    """SECURITY: state file contains no sample_redacted, api_key, or raw content."""
    from watchdog import run_watchdog_once
    import json

    template = "SELECT * FROM users WHERE <id> = ?"

    def mock_scan_fn():
        return {
            "scan_time": "2026-07-14T10:00:00Z",
            "pattern_leaderboard": [
                {
                    "template": template,
                    "monthly_cost_usd": 500.0,
                    "monthly_events": 1000,
                    "recommended_action": "investigate",
                    "sample_redacted": "Fake email: user@example.com | api_key=secret123",
                }
            ],
            "surges": [],
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "state.json")

        run_watchdog_once(
            mock_scan_fn,
            state_path,
            "2026-07-14T10:00:00Z",
            notify_fn=lambda x: None,
        )

        # Read raw file text
        with open(state_path) as f:
            raw_text = f.read()

        # Assert NO sensitive content
        assert "sample_redacted" not in raw_text
        assert "api_key" not in raw_text
        assert "secret123" not in raw_text
        assert "user@example.com" not in raw_text

        # Parse and validate structure
        state = json.loads(raw_text)
        baselines = state.get("baselines", {})
        for fp, entry in baselines.get("templates", {}).items():
            assert "sample_redacted" not in entry
            assert "api_key" not in entry
            # Only masked template + numbers allowed
            assert "template" in entry
            assert "monthly_cost_usd" in entry
            assert "monthly_events" in entry


# ============================================================================
# Security tests (FIX 3: watchdog alert body embeds template verbatim)
# ============================================================================

class TestSecurityWatchdogAlertRedaction:
    """Test that watchdog alert bodies scrub secrets from template strings."""

    def test_format_alert_redacts_jwt_and_keys_from_template(self):
        """Alert body should not contain raw JWTs or long token sequences."""
        from watchdog import format_alert

        # Alert with JWT and AWS key in template
        alert = {
            "kind": "new_pattern",
            "template": "login eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4ifQ.sig token AKIAIOSFODNN7EXAMPLE9",
            "monthly_cost_usd": 500.0,
            "fingerprint": "abc123def456",
        }

        formatted = format_alert(alert)
        body = formatted["body"]

        # Verify:
        # 1. Raw JWT is NOT in the body
        assert "eyJhbGciOiJIUzI1NiJ9" not in body, \
            "Raw JWT found in alert body - secret exposure!"
        assert "eyJ1c2VyIjoiYWRtaW4ifQ" not in body, \
            "Raw JWT payload found in alert body - secret exposure!"

        # 2. Raw AWS key is NOT in the body
        assert "AKIAIOSFODNN7EXAMPLE9" not in body, \
            "Raw AWS key found in alert body - secret exposure!"

        # 3. Template is still referenced (but redacted)
        assert "login" in body, \
            "Template context was lost during redaction"
