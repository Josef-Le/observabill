"""
test_remediate.py — Tests for auto-remediation executor.

Strict TDD: RED tests first (all should fail initially), then implement.
Tests verify:
  - Disposition logic (recommend/alert/auto)
  - Dry-run behavior (no write)
  - Applied behavior (write called, undo record created, audit logged)
  - Error handling (exception masked, never propagated)
  - Security (no api/app keys or raw content in audit/return)
  - Daily cap enforcement
  - Undo reversal
"""

import json
import os
import tempfile
from pathlib import Path

import remediate
import config
import dd_client


class TestAuditAppend:
    """_audit_append creates file with mode 0600, appends JSON lines."""

    def test_audit_append_creates_file(self):
        """Creates new file if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"

            entry = {
                "ts": "2026-07-21T12:00:00Z",
                "kind": "exclude",
                "template": "login <ID>",
                "monthly_cost_usd": 500.0,
                "disposition": "auto",
                "action": "applied",
            }
            remediate._audit_append(str(audit_path), entry)

            assert audit_path.exists()
            # Verify mode is 0600
            mode = oct(audit_path.stat().st_mode)[-3:]
            assert mode == "600", f"Expected mode 600, got {mode}"

            # Verify content
            lines = audit_path.read_text().strip().split("\n")
            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["ts"] == "2026-07-21T12:00:00Z"
            assert parsed["action"] == "applied"

    def test_audit_append_appends_to_existing(self):
        """Appends to existing file without truncating."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"

            # First append
            e1 = {"ts": "2026-07-21T12:00:00Z", "action": "applied"}
            remediate._audit_append(str(audit_path), e1)

            # Second append
            e2 = {"ts": "2026-07-21T12:01:00Z", "action": "skipped"}
            remediate._audit_append(str(audit_path), e2)

            lines = audit_path.read_text().strip().split("\n")
            assert len(lines) == 2
            assert json.loads(lines[0])["action"] == "applied"
            assert json.loads(lines[1])["action"] == "skipped"

    def test_audit_append_no_secrets(self):
        """Audit entry NEVER contains api_key, app_key, or raw log content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"

            # Entry with only safe fields
            entry = {
                "ts": "2026-07-21T12:00:00Z",
                "kind": "exclude",
                "template": "login <ID>",  # masked, not raw
                "monthly_cost_usd": 500.0,
                "disposition": "auto",
                "action": "applied",
            }
            remediate._audit_append(str(audit_path), entry)

            text = audit_path.read_text()
            assert "api_key" not in text.lower()
            assert "app_key" not in text.lower()
            assert "SECRETKEY" not in text  # no raw creds


class TestPlanRemediation:
    """plan_remediation returns disposition string from config.decide."""

    def test_plan_remediation_returns_disposition(self):
        """Delegates to config.decide, returns disposition."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }

        result = remediate.plan_remediation(finding, policy, actions_today=0)

        assert result == "auto"

    def test_plan_remediation_respects_actions_today(self):
        """Respects actions_today parameter for daily cap."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 2, "auto_only_actions": ["exclude"]},
        }

        # At cap, should downgrade to alert
        result = remediate.plan_remediation(finding, policy, actions_today=2)

        assert result == "alert"


class TestApplyRemediationRecommend:
    """When disposition is "recommend", never write, return applied=False."""

    def test_disposition_recommend_no_write(self):
        """Recommend disposition -> applied False, writer not called."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 50.0,  # below threshold
            "confidence": "high",
            "template": "test",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {"exclusion_filters": [{"name": "test", "filter": {}}]},
            },
        }
        policy = config.default_policy()
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        writer_calls = []
        def mock_writer(*args, **kwargs):
            writer_calls.append((args, kwargs))
            return {}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            result = remediate.apply_remediation(
                finding, policy, creds,
                actions_today=0,
                now_iso="2026-07-21T12:00:00Z",
                writer=mock_writer,
                audit_path=str(audit_path),
            )

            assert result["applied"] is False
            assert result["disposition"] == "recommend"
            assert result["reason"] == "not auto"
            assert len(writer_calls) == 0

            # Verify audit entry exists and says "skipped"
            text = audit_path.read_text()
            assert "skipped" in text


class TestApplyRemediationAlert:
    """When disposition is "alert", never write, return applied=False."""

    def test_disposition_alert_no_write(self):
        """Alert disposition -> applied False, writer not called."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "low",  # below min_confidence_for_auto
            "template": "test",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {"exclusion_filters": [{"name": "test", "filter": {}}]},
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        writer_calls = []
        def mock_writer(*args, **kwargs):
            writer_calls.append((args, kwargs))
            return {}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            result = remediate.apply_remediation(
                finding, policy, creds,
                actions_today=0,
                now_iso="2026-07-21T12:00:00Z",
                writer=mock_writer,
                audit_path=str(audit_path),
            )

            assert result["applied"] is False
            assert result["disposition"] == "alert"
            assert result["reason"] == "not auto"
            assert len(writer_calls) == 0

            # Verify audit says "skipped"
            text = audit_path.read_text()
            assert "skipped" in text


class TestApplyRemediationDryRun:
    """When disposition is "auto" but dry_run=True, return applied=False + would_apply."""

    def test_dry_run_mode_no_write(self):
        """Auto + dry_run -> applied False, dry_run True, would_apply present, writer not called."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "test",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {"name": "test-filter", "filter": {"query": "test", "sample_rate": 1.0}}
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": True,  # DRY RUN
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        writer_calls = []
        def mock_writer(*args, **kwargs):
            writer_calls.append((args, kwargs))
            return {}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            result = remediate.apply_remediation(
                finding, policy, creds,
                actions_today=0,
                now_iso="2026-07-21T12:00:00Z",
                writer=mock_writer,
                audit_path=str(audit_path),
            )

            assert result["applied"] is False
            assert result["dry_run"] is True
            assert result["disposition"] == "auto"
            assert "would_apply" in result
            assert result["would_apply"] == finding["generated_config"]
            assert len(writer_calls) == 0

            # Verify audit says "dry_run"
            text = audit_path.read_text()
            assert "dry_run" in text


class TestApplyRemediationSuccess:
    """When disposition is "auto" and not dry_run, write and audit success."""

    def test_auto_applied_success(self):
        """Auto + not dry_run -> applied True, writer called once, undo record created."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "login <ID>",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {
                            "name": "observabill-test",
                            "filter": {"query": "login", "sample_rate": 1.0},
                            "is_enabled": True,
                        }
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        writer_calls = []
        def mock_writer(endpoint, verb, payload, api_key, app_key, site):
            writer_calls.append({
                "endpoint": endpoint,
                "verb": verb,
                "payload": payload,
                "api_key": api_key,
                "app_key": app_key,
                "site": site,
            })
            return {"status": "success"}

        def mock_reader(endpoint, api_key, app_key, site):
            # Return empty config (no existing filters)
            return {"exclusion_filters": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            result = remediate.apply_remediation(
                finding, policy, creds,
                actions_today=0,
                now_iso="2026-07-21T12:00:00Z",
                writer=mock_writer,
                reader_fn=mock_reader,
                audit_path=str(audit_path),
            )

            # Verify result
            assert result["applied"] is True
            assert result["disposition"] == "auto"
            assert "undo" in result
            assert result["undo"]["endpoint"] == "/api/v1/logs/config/indexes/main"
            assert result["undo"]["verb"] == "PUT"
            assert result["undo"]["filter_name"] == "observabill-test"

            # Verify writer called exactly once with correct args
            assert len(writer_calls) == 1
            call = writer_calls[0]
            assert call["endpoint"] == "/api/v1/logs/config/indexes/main"
            assert call["verb"] == "PUT"
            # Payload should have merged filters (empty existing + 1 new)
            assert len(call["payload"]["exclusion_filters"]) == 1
            assert call["payload"]["exclusion_filters"][0]["name"] == "observabill-test"
            assert call["api_key"] == "key123"
            assert call["app_key"] == "app123"
            assert call["site"] == "us1"

            # Verify audit says "applied"
            text = audit_path.read_text()
            assert "applied" in text
            audit_entry = json.loads(text.strip().split("\n")[0])
            assert audit_entry["action"] == "applied"


class TestApplyRemediationError:
    """When writer raises exception, catch it, return applied=False + error, never propagate."""

    def test_writer_raises_datadog_error(self):
        """Writer raises dd_client.DatadogError -> applied False, error is class name only."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "login <ID>",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {"name": "test", "filter": {"query": "login"}}
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        def mock_writer(*args, **kwargs):
            # Simulate error with potentially secret-containing message
            raise dd_client.DatadogError("HTTP 500 error from /api/v1/... received key=SUPERSECRET123")

        def mock_reader(endpoint, api_key, app_key, site):
            return {"exclusion_filters": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            result = remediate.apply_remediation(
                finding, policy, creds,
                actions_today=0,
                now_iso="2026-07-21T12:00:00Z",
                writer=mock_writer,
                reader_fn=mock_reader,
                audit_path=str(audit_path),
            )

        # Verify result
        assert result["applied"] is False
        assert result["disposition"] == "auto"
        assert result["error"] == "DatadogError"  # CLASS NAME ONLY

        # Verify no exception raised
        assert "error" in result

    def test_writer_raises_auth_error(self):
        """Writer raises dd_client.AuthError -> applied False, error is class name."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "test",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {"exclusion_filters": [{"name": "test", "filter": {"query": "test"}}]},
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        def mock_writer(*args, **kwargs):
            raise dd_client.AuthError("HTTP 401 Unauthorized")

        def mock_reader(endpoint, api_key, app_key, site):
            return {"exclusion_filters": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            result = remediate.apply_remediation(
                finding, policy, creds,
                actions_today=0,
                now_iso="2026-07-21T12:00:00Z",
                writer=mock_writer,
                reader_fn=mock_reader,
                audit_path=str(audit_path),
            )

        assert result["applied"] is False
        assert result["error"] == "AuthError"


class TestSecurityNoKeysInAudit:
    """CRITICAL SECURITY: Audit and return dict NEVER contain api/app keys or raw content."""

    def test_audit_never_contains_credentials(self):
        """After apply, read audit file and verify no secrets present."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "login <ID>",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {"name": "test", "filter": {"query": "login"}}
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "SECRETKEY123", "app_key": "APPSECRET456", "site": "us1"}

        def mock_writer(*args, **kwargs):
            return {"status": "success"}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            remediate.apply_remediation(
                finding, policy, creds,
                actions_today=0,
                now_iso="2026-07-21T12:00:00Z",
                writer=mock_writer,
                audit_path=str(audit_path),
            )

            # Read audit file and verify no secrets
            audit_text = audit_path.read_text()
            assert "SECRETKEY123" not in audit_text
            assert "APPSECRET456" not in audit_text
            assert "api_key" not in audit_text.lower()  # no key names either
            assert "app_key" not in audit_text.lower()

    def test_return_dict_never_contains_credentials(self):
        """Return dict from apply_remediation NEVER includes api/app keys."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "login <ID>",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {"name": "test", "filter": {"query": "login"}}
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "SECRETKEY123", "app_key": "APPSECRET456", "site": "us1"}

        def mock_writer(*args, **kwargs):
            return {"status": "success"}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            result = remediate.apply_remediation(
                finding, policy, creds,
                actions_today=0,
                now_iso="2026-07-21T12:00:00Z",
                writer=mock_writer,
                audit_path=str(audit_path),
            )

            result_str = json.dumps(result)
            assert "SECRETKEY123" not in result_str
            assert "APPSECRET456" not in result_str
            # Also check the undo record if present
            if "undo" in result:
                undo_str = json.dumps(result["undo"])
                assert "SECRETKEY123" not in undo_str
                assert "APPSECRET456" not in undo_str


class TestDailyCapEnforcement:
    """Daily cap: if actions_today >= auto_max_actions_per_day, downgrade to alert."""

    def test_daily_cap_exceeded(self):
        """actions_today >= cap -> disposition alert, no write."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "test",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {"exclusion_filters": []},
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 3, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        writer_calls = []
        def mock_writer(*args, **kwargs):
            writer_calls.append(True)
            return {}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            result = remediate.apply_remediation(
                finding, policy, creds,
                actions_today=3,  # Already at cap
                now_iso="2026-07-21T12:00:00Z",
                writer=mock_writer,
                audit_path=str(audit_path),
            )

            assert result["applied"] is False
            assert result["disposition"] == "alert"
            assert len(writer_calls) == 0


class TestApplyRemediationEmptyQuery:
    """FIX 1: Empty query must NOT apply — drops 100% of logs."""

    def test_apply_with_empty_query_finding_rejected(self):
        """Finding with empty query in generated_config -> applied False, reason unsafe_empty_query."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "<NUM> <UUID> <IP>",  # All placeholders → empty phrase
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {
                            "name": "observabill-empty",
                            "filter": {"query": "", "sample_rate": 1.0},
                            "is_enabled": True,
                        }
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        writer_calls = []
        def mock_writer(*args, **kwargs):
            writer_calls.append(args)
            return {}

        result = remediate.apply_remediation(
            finding, policy, creds,
            actions_today=0,
            now_iso="2026-07-21T12:00:00Z",
            writer=mock_writer,
        )

        # Must refuse to apply
        assert result["applied"] is False
        assert result["reason"] == "unsafe_empty_query"
        assert len(writer_calls) == 0, "Writer should NOT be called for empty query"

    def test_apply_with_whitespace_only_query_rejected(self):
        """Finding with whitespace-only query -> applied False, reason unsafe_empty_query."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {
                            "name": "observabill-whitespace",
                            "filter": {"query": "   ", "sample_rate": 1.0},
                            "is_enabled": True,
                        }
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        writer_calls = []
        def mock_writer(*args, **kwargs):
            writer_calls.append(args)
            return {}

        result = remediate.apply_remediation(
            finding, policy, creds,
            actions_today=0,
            now_iso="2026-07-21T12:00:00Z",
            writer=mock_writer,
        )

        assert result["applied"] is False
        assert result["reason"] == "unsafe_empty_query"
        assert len(writer_calls) == 0


class TestApplyRemediationMustReadMerge:
    """FIX 2: Must READ-MERGE existing filters, never full-replace."""

    def test_apply_merges_with_existing_filters(self):
        """reader_fn returns existing filters; writer PUT contains all: existing + new."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "login <ID>",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {
                            "name": "observabill-login",
                            "filter": {"query": "login", "sample_rate": 1.0},
                            "is_enabled": True,
                        }
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        # Simulate current index state: already has 2 filters
        existing_config = {
            "exclusion_filters": [
                {"name": "cust-filter-A", "filter": {"query": "error", "sample_rate": 1.0}},
                {"name": "cust-filter-B", "filter": {"query": "debug", "sample_rate": 0.9}},
            ]
        }

        def mock_reader(endpoint, api_key, app_key, site):
            return existing_config

        writer_calls = []
        def mock_writer(endpoint, verb, payload, api_key, app_key, site):
            writer_calls.append({
                "endpoint": endpoint,
                "verb": verb,
                "payload": payload,
            })
            return {}

        result = remediate.apply_remediation(
            finding, policy, creds,
            actions_today=0,
            now_iso="2026-07-21T12:00:00Z",
            writer=mock_writer,
            reader_fn=mock_reader,
        )

        # Must succeed
        assert result["applied"] is True
        assert len(writer_calls) == 1

        # Writer payload must contain all 3 filters: the 2 existing + 1 new
        written_payload = writer_calls[0]["payload"]
        written_filters = written_payload["exclusion_filters"]
        assert len(written_filters) == 3, f"Expected 3 filters (2 existing + 1 new), got {len(written_filters)}"

        # Verify all filters are present by name
        filter_names = [f.get("name") for f in written_filters]
        assert "cust-filter-A" in filter_names
        assert "cust-filter-B" in filter_names
        assert "observabill-login" in filter_names

    def test_apply_idempotent_if_filter_name_already_exists(self):
        """If our filter already exists by name, don't duplicate — treat as already applied."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "login <ID>",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {
                            "name": "observabill-login",
                            "filter": {"query": "login", "sample_rate": 1.0},
                            "is_enabled": True,
                        }
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        # Current config already has our filter
        existing_config = {
            "exclusion_filters": [
                {"name": "observabill-login", "filter": {"query": "login", "sample_rate": 1.0}, "is_enabled": True},
            ]
        }

        def mock_reader(endpoint, api_key, app_key, site):
            return existing_config

        writer_calls = []
        def mock_writer(endpoint, verb, payload, api_key, app_key, site):
            writer_calls.append(payload)
            return {}

        result = remediate.apply_remediation(
            finding, policy, creds,
            actions_today=0,
            now_iso="2026-07-21T12:00:00Z",
            writer=mock_writer,
            reader_fn=mock_reader,
        )

        # Idempotent: applied=True, but writer NOT called (no duplicate)
        assert result["applied"] is True
        # For idempotency test: if it's already there, we don't call writer again
        # (or we call it once but it's a no-op). The key is no duplicates.
        written_filters = writer_calls[0]["exclusion_filters"] if writer_calls else existing_config["exclusion_filters"]
        assert len(written_filters) == 1
        assert written_filters[0]["name"] == "observabill-login"

    def test_apply_refuses_if_reader_fails(self):
        """reader_fn raises exception -> applied False, reason cannot_read_current_filters, writer NOT called."""
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "login <ID>",
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "exclusion_filters": [
                        {"name": "observabill-login", "filter": {"query": "login"}}
                    ]
                },
            },
        }
        policy = {
            "enabled": True,
            "dry_run": False,
            "modes": {"exclude": "auto"},
            "thresholds": {"min_cost_usd": 100.0, "min_confidence_for_auto": "high"},
            "guardrails": {"auto_max_actions_per_day": 5, "auto_only_actions": ["exclude"]},
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        def mock_reader(endpoint, api_key, app_key, site):
            raise dd_client.DatadogError("HTTP 403 Forbidden")

        writer_calls = []
        def mock_writer(*args, **kwargs):
            writer_calls.append(args)
            return {}

        result = remediate.apply_remediation(
            finding, policy, creds,
            actions_today=0,
            now_iso="2026-07-21T12:00:00Z",
            writer=mock_writer,
            reader_fn=mock_reader,
        )

        # Must refuse
        assert result["applied"] is False
        assert result["reason"] == "cannot_read_current_filters"
        assert len(writer_calls) == 0


class TestUndoMustRemoveOnlyOurs:
    """FIX 3: undo must remove ONLY our filter, never wipe all."""

    def test_undo_removes_only_our_filter(self):
        """reader_fn returns 3 filters [A, B, ours]; undo removes ours, keeps A+B."""
        undo_record = {
            "endpoint": "/api/v1/logs/config/indexes/main",
            "filter_name": "observabill-login",
            "verb": "PUT",
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        existing_config = {
            "exclusion_filters": [
                {"name": "cust-A", "filter": {"query": "error"}},
                {"name": "cust-B", "filter": {"query": "debug"}},
                {"name": "observabill-login", "filter": {"query": "login"}},
            ]
        }

        def mock_reader(endpoint, api_key, app_key, site):
            return existing_config

        writer_calls = []
        def mock_writer(endpoint, verb, payload, api_key, app_key, site):
            writer_calls.append({
                "endpoint": endpoint,
                "verb": verb,
                "payload": payload,
            })
            return {}

        result = remediate.undo(
            undo_record, creds,
            writer=mock_writer,
            reader_fn=mock_reader,
        )

        # Must succeed
        assert result["undone"] is True
        assert len(writer_calls) == 1

        # Written payload must have only A+B (ours removed)
        written_payload = writer_calls[0]["payload"]
        written_filters = written_payload["exclusion_filters"]
        assert len(written_filters) == 2, f"Expected 2 filters (ours removed), got {len(written_filters)}"

        filter_names = [f.get("name") for f in written_filters]
        assert "cust-A" in filter_names
        assert "cust-B" in filter_names
        assert "observabill-login" not in filter_names

    def test_undo_refuses_if_reader_fails(self):
        """reader_fn raises -> undone False, reason cannot_read, writer NOT called."""
        undo_record = {
            "endpoint": "/api/v1/logs/config/indexes/main",
            "filter_name": "observabill-login",
            "verb": "PUT",
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        def mock_reader(endpoint, api_key, app_key, site):
            raise dd_client.DatadogError("HTTP 403 Forbidden")

        writer_calls = []
        def mock_writer(*args, **kwargs):
            writer_calls.append(args)
            return {}

        result = remediate.undo(
            undo_record, creds,
            writer=mock_writer,
            reader_fn=mock_reader,
        )

        # Must refuse
        assert result["undone"] is False
        assert result["reason"] == "cannot_read"
        assert len(writer_calls) == 0


class TestUndo:
    """undo() calls writer to remove filter, returns undone bool."""

    def test_undo_success(self):
        """undo() with empty current config -> writer called with empty exclusion_filters."""
        undo_record = {
            "endpoint": "/api/v1/logs/config/indexes/main",
            "filter_name": "observabill-test",
            "verb": "PUT",
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        writer_calls = []
        def mock_writer(endpoint, verb, payload, api_key, app_key, site):
            writer_calls.append({
                "endpoint": endpoint,
                "verb": verb,
                "payload": payload,
            })
            return {"status": "success"}

        # Provide a reader_fn that returns empty config (no existing filters)
        def mock_reader(endpoint, api_key, app_key, site):
            return {"exclusion_filters": []}

        result = remediate.undo(
            undo_record, creds,
            writer=mock_writer,
            reader_fn=mock_reader,
        )

        assert result["undone"] is True
        assert "error" not in result
        assert len(writer_calls) == 1
        call = writer_calls[0]
        assert call["endpoint"] == "/api/v1/logs/config/indexes/main"
        assert call["verb"] == "PUT"
        assert call["payload"]["exclusion_filters"] == []

    def test_undo_writer_error(self):
        """undo() catches exception, returns undone False + reason/error, never raises."""
        undo_record = {
            "endpoint": "/api/v1/logs/config/indexes/main",
            "filter_name": "observabill-test",
            "verb": "PUT",
        }
        creds = {"api_key": "key123", "app_key": "app123", "site": "us1"}

        def mock_writer(*args, **kwargs):
            raise dd_client.DatadogError("HTTP 500 error from /api/v1/...")

        def mock_reader(endpoint, api_key, app_key, site):
            return {"exclusion_filters": []}

        result = remediate.undo(
            undo_record, creds,
            writer=mock_writer,
            reader_fn=mock_reader,
        )

        assert result["undone"] is False
        assert result["error"] == "DatadogError"
        # No exception raised
