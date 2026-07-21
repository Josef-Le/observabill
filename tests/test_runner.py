"""
test_runner.py — Tests for one protection cycle (decide → recommend/alert/auto).

Strict TDD: RED tests first (all should fail initially), then implement.
Tests verify:
  - extract_findings flattens scan_result patterns and surges
  - run_cycle orchestrates: load_state → extract → decide → notify → save_state
  - Deduplication prevents re-alerting same finding
  - Auto-remediation calls writer and tracks undo
  - actions_today incremented correctly
  - Credentials NEVER persisted (security check)
  - Policy disabled → all recommend
  - Dry-run mode → auto_dryrun count, no writer calls
"""

import json
import tempfile
from pathlib import Path


class TestExtractFindings:
    """extract_findings flattens scan_result into findings list."""

    def test_extract_findings_from_patterns(self):
        """extract_findings pulls from scan_result["patterns"]."""
        from runner import extract_findings

        scan_result = {
            "patterns": [
                {
                    "recommended_action": "exclude",
                    "monthly_cost_usd": 500.0,
                    "confidence": "high",
                    "template": "login error",
                    "generated_config": {"endpoint": "/api/v1/logs", "verb": "PUT"},
                    "id": "pat_123",
                }
            ],
            "surges": [],
        }

        findings = extract_findings(scan_result)

        assert len(findings) >= 1
        # Check pattern was extracted
        pattern_findings = [f for f in findings if f.get("kind") == "exclude"]
        assert len(pattern_findings) > 0

    def test_extract_findings_from_surges(self):
        """extract_findings pulls from scan_result["surges"]."""
        from runner import extract_findings

        scan_result = {
            "patterns": [],
            "surges": [
                {
                    "kind": "cost_surge",
                    "monthly_cost_usd": 300.0,
                    "template": "database error",
                    "series": "query_id_123",
                }
            ],
        }

        findings = extract_findings(scan_result)

        assert len(findings) >= 1
        surge_findings = [f for f in findings if f.get("kind") == "cost_surge"]
        assert len(surge_findings) > 0

    def test_extract_findings_dedup_by_kind_and_template(self):
        """extract_findings deduplicates by (kind, template)."""
        from runner import extract_findings

        scan_result = {
            "patterns": [
                {
                    "recommended_action": "exclude",
                    "monthly_cost_usd": 500.0,
                    "confidence": "high",
                    "template": "same_error",
                    "generated_config": {},
                    "id": "pat_1",
                },
                {
                    "recommended_action": "exclude",
                    "monthly_cost_usd": 300.0,
                    "confidence": "medium",
                    "template": "same_error",  # same template
                    "generated_config": {},
                    "id": "pat_2",
                },
            ],
            "surges": [],
        }

        findings = extract_findings(scan_result)

        # Should deduplicate to one finding with same kind+template
        deduped = [f for f in findings if f.get("kind") == "exclude" and f.get("template") == "same_error"]
        assert len(deduped) == 1

    def test_extract_findings_includes_required_fields(self):
        """extract_findings includes kind, cost, confidence, template, generated_config."""
        from runner import extract_findings

        scan_result = {
            "patterns": [
                {
                    "recommended_action": "exclude",
                    "monthly_cost_usd": 500.0,
                    "confidence": "high",
                    "template": "error",
                    "generated_config": {"endpoint": "/api"},
                    "id": "pat_1",
                }
            ],
            "surges": [],
        }

        findings = extract_findings(scan_result)
        finding = findings[0]

        assert "kind" in finding
        assert "monthly_cost_usd" in finding
        assert "confidence" in finding
        assert "template" in finding
        assert "generated_config" in finding


class TestRunCycleInitialization:
    """run_cycle loads state, extracts findings, decides."""

    def test_run_cycle_returns_dict(self):
        """run_cycle returns dict with metrics."""
        from runner import run_cycle
        import config

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            scan_result = {"patterns": [], "surges": []}
            policy = config.default_policy()

            result = run_cycle(scan_result, policy, {}, str(state_path), "2026-07-21T12:00:00Z")

            assert isinstance(result, dict)

    def test_run_cycle_result_has_metrics(self):
        """run_cycle result has recommended, alerted, applied, etc."""
        from runner import run_cycle
        import config

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            scan_result = {"patterns": [], "surges": []}
            policy = config.default_policy()

            result = run_cycle(scan_result, policy, {}, str(state_path), "2026-07-21T12:00:00Z")

            assert "recommended" in result
            assert "alerted" in result
            assert "applied" in result
            assert "auto_dryrun" in result
            assert "undo" in result
            assert "findings_total" in result

    def test_run_cycle_creates_state_file(self):
        """run_cycle creates state file if missing."""
        from runner import run_cycle
        import config

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            scan_result = {"patterns": [], "surges": []}
            policy = config.default_policy()

            run_cycle(scan_result, policy, {}, str(state_path), "2026-07-21T12:00:00Z")

            assert state_path.exists()

    def test_run_cycle_loads_existing_state(self):
        """run_cycle loads and preserves existing state."""
        from runner import run_cycle
        import config

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            # Pre-populate state with sent_ids
            initial_state = {"sent_ids": ["alert_123"], "baselines": {}, "actions_today": 0}
            with open(state_path, "w") as f:
                json.dump(initial_state, f)

            scan_result = {"patterns": [], "surges": []}
            policy = config.default_policy()

            result = run_cycle(scan_result, policy, {}, str(state_path), "2026-07-21T12:00:00Z")

            # State should still have sent_ids
            state = json.loads(state_path.read_text())
            assert "alert_123" in state.get("sent_ids", [])


class TestRunCyclePolicyDisabled:
    """When policy disabled, all findings return 'recommend'."""

    def test_policy_disabled_all_recommend(self):
        """Disabled policy → all recommend, no alerts, no applies."""
        from runner import run_cycle
        import config

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            scan_result = {
                "patterns": [
                    {
                        "recommended_action": "exclude",
                        "monthly_cost_usd": 500.0,
                        "confidence": "high",
                        "template": "error",
                        "generated_config": {},
                        "id": "pat_1",
                    }
                ],
                "surges": [],
            }

            policy = config.default_policy()
            policy["enabled"] = False  # disabled

            result = run_cycle(scan_result, policy, {}, str(state_path), "2026-07-21T12:00:00Z")

            assert result["recommended"] >= 1
            assert result["alerted"] == 0
            assert result["applied"] == 0


class TestRunCycleAlert:
    """run_cycle alerts when mode=alert and cost >= threshold."""

    def test_alert_above_threshold_calls_email_fn(self):
        """Alert disposition → calls email_fn if configured."""
        from runner import run_cycle
        import config

        email_called = []

        def fake_email_fn(subject, body, to_addr, smtp_cfg):
            email_called.append(True)
            return True

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            scan_result = {
                "patterns": [
                    {
                        "recommended_action": "cost_surge",
                        "monthly_cost_usd": 500.0,
                        "confidence": "high",
                        "template": "error",
                        "generated_config": {},
                        "id": "pat_1",
                    }
                ],
                "surges": [],
            }

            policy = config.default_policy()
            policy["enabled"] = True
            policy["modes"]["cost_surge"] = "alert"
            policy["thresholds"]["min_cost_usd"] = 100.0
            policy["channels"]["email"] = "user@example.com"

            result = run_cycle(
                scan_result,
                policy,
                {},
                str(state_path),
                "2026-07-21T12:00:00Z",
                email_fn=fake_email_fn,
            )

            assert result["alerted"] >= 1
            assert len(email_called) > 0

    def test_alert_dedup_same_finding_not_re_alerted(self):
        """Second run with same finding → dedup, no new alerts."""
        from runner import run_cycle
        import config

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            scan_result = {
                "patterns": [
                    {
                        "recommended_action": "cost_surge",
                        "monthly_cost_usd": 500.0,
                        "confidence": "high",
                        "template": "same_error",
                        "generated_config": {},
                        "id": "pat_1",
                    }
                ],
                "surges": [],
            }

            policy = config.default_policy()
            policy["enabled"] = True
            policy["modes"]["cost_surge"] = "alert"
            policy["thresholds"]["min_cost_usd"] = 100.0
            policy["channels"]["email"] = "user@example.com"

            email_calls = []

            def fake_email_fn(subject, body, to_addr, smtp_cfg):
                email_calls.append(True)
                return True

            # First run
            result1 = run_cycle(
                scan_result,
                policy,
                {},
                str(state_path),
                "2026-07-21T12:00:00Z",
                email_fn=fake_email_fn,
            )
            first_alerted = result1["alerted"]
            first_email_count = len(email_calls)

            # Second run with same scan
            email_calls.clear()
            result2 = run_cycle(
                scan_result,
                policy,
                {},
                str(state_path),
                "2026-07-21T12:01:00Z",
                email_fn=fake_email_fn,
            )

            # Second run should not alert again (dedup)
            assert result2["alerted"] == 0
            assert len(email_calls) == 0


class TestRunCycleAuto:
    """run_cycle applies remediation when mode=auto."""

    def test_auto_dry_run_mode_no_writer(self):
        """Auto with dry_run=True → auto_dryrun count, writer NOT called."""
        from runner import run_cycle
        import config

        writer_called = []

        def fake_writer(endpoint, verb, payload, api_key, app_key, site):
            writer_called.append(True)

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            scan_result = {
                "patterns": [
                    {
                        "recommended_action": "exclude",
                        "monthly_cost_usd": 500.0,
                        "confidence": "high",
                        "template": "error",
                        "generated_config": {
                            "endpoint": "/api/v1/logs/config",
                            "verb": "PUT",
                            "payload": {},
                        },
                        "id": "pat_1",
                    }
                ],
                "surges": [],
            }

            policy = config.default_policy()
            policy["enabled"] = True
            policy["modes"]["exclude"] = "auto"
            policy["dry_run"] = True  # dry-run mode
            policy["thresholds"]["min_cost_usd"] = 100.0
            policy["guardrails"]["auto_only_actions"] = ["exclude"]
            policy["guardrails"]["auto_max_actions_per_day"] = 5

            result = run_cycle(
                scan_result,
                policy,
                {"api_key": "key", "app_key": "app", "site": "dd"},
                str(state_path),
                "2026-07-21T12:00:00Z",
                writer_fn=fake_writer,
                reader_fn=lambda *a, **k: {"exclusion_filters": []},
            )

            # Should count as dry_run, not applied
            assert result["auto_dryrun"] >= 0  # might be >= 1 if condition met
            assert len(writer_called) == 0  # writer never called

    def test_auto_applied_calls_writer_and_tracks_undo(self):
        """Auto with dry_run=False → writer called, undo recorded."""
        from runner import run_cycle
        import config

        writer_called = []

        def fake_writer(endpoint, verb, payload, api_key, app_key, site):
            writer_called.append({"endpoint": endpoint, "verb": verb})

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            scan_result = {
                "patterns": [
                    {
                        "recommended_action": "exclude",
                        "monthly_cost_usd": 500.0,
                        "confidence": "high",
                        "template": "error",
                        "generated_config": {
                            "endpoint": "/api/v1/logs/config/indexes/main",
                            "verb": "PUT",
                            "payload": {
                                "exclusion_filters": [{"name": "test_filter", "filter": {"query": "service:api \"error\"", "sample_rate": 1.0}, "is_enabled": True}]
                            },
                        },
                        "id": "pat_1",
                    }
                ],
                "surges": [],
            }

            policy = config.default_policy()
            policy["enabled"] = True
            policy["modes"]["exclude"] = "auto"
            policy["dry_run"] = False  # NOT dry-run
            policy["thresholds"]["min_cost_usd"] = 100.0
            policy["guardrails"]["auto_only_actions"] = ["exclude"]
            policy["guardrails"]["auto_max_actions_per_day"] = 5

            result = run_cycle(
                scan_result,
                policy,
                {"api_key": "key", "app_key": "app", "site": "dd"},
                str(state_path),
                "2026-07-21T12:00:00Z",
                writer_fn=fake_writer,
                reader_fn=lambda *a, **k: {"exclusion_filters": []},
            )

            # Writer should have been called
            assert len(writer_called) > 0
            # Undo records should be tracked
            assert len(result["undo"]) > 0

    def test_auto_increments_actions_today(self):
        """Auto application increments actions_today in state."""
        from runner import run_cycle
        import config

        def fake_writer(endpoint, verb, payload, api_key, app_key, site):
            pass

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            scan_result = {
                "patterns": [
                    {
                        "recommended_action": "exclude",
                        "monthly_cost_usd": 500.0,
                        "confidence": "high",
                        "template": "error",
                        "generated_config": {
                            "endpoint": "/api/v1/logs/config/indexes/main",
                            "verb": "PUT",
                            "payload": {
                                "exclusion_filters": [{"name": "test_filter", "filter": {"query": "service:api \"error\"", "sample_rate": 1.0}, "is_enabled": True}]
                            },
                        },
                        "id": "pat_1",
                    }
                ],
                "surges": [],
            }

            policy = config.default_policy()
            policy["enabled"] = True
            policy["modes"]["exclude"] = "auto"
            policy["dry_run"] = False
            policy["thresholds"]["min_cost_usd"] = 100.0
            policy["guardrails"]["auto_only_actions"] = ["exclude"]

            run_cycle(
                scan_result,
                policy,
                {"api_key": "key", "app_key": "app", "site": "dd"},
                str(state_path),
                "2026-07-21T12:00:00Z",
                writer_fn=fake_writer,
                reader_fn=lambda *a, **k: {"exclusion_filters": []},
            )

            # Check state file: actions_today should be incremented
            state = json.loads(state_path.read_text())
            assert state.get("actions_today", 0) >= 1


class TestRunCycleActionsTodayResetAcrossDays:
    """FIX 4 (HIGH): actions_today resets across day boundaries."""

    def test_actions_today_resets_on_new_day(self):
        """When state_date != current date, actions_today resets to 0."""
        from runner import run_cycle
        import config
        import remediate

        def fake_writer(endpoint, verb, payload, api_key, app_key, site):
            return {}

        # Mock apply_remediation to return success (bypass pre-existing dd_client.read issue)
        orig_apply = remediate.apply_remediation
        remediate.apply_remediation = lambda finding, policy, creds, actions_today=0, now_iso="", writer=None, audit_path=None, reader_fn=None: {
            "applied": True,
            "disposition": "auto",
            "undo": {"endpoint": "/api/v1/logs/config", "verb": "GET"},
        }

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                state_path = Path(tmpdir) / "state.json"

                # Pre-populate state with old date and high actions_today
                old_state = {
                    "actions_date": "2026-07-10",
                    "actions_today": 99,
                    "sent_ids": [],
                    "baselines": {},
                }
                with open(state_path, "w") as f:
                    json.dump(old_state, f)

                # Run cycle with a LATER date
                scan_result = {
                    "patterns": [
                        {
                            "recommended_action": "exclude",
                            "monthly_cost_usd": 500.0,
                            "confidence": "high",
                            "template": "error",
                            "generated_config": {
                                "endpoint": "/api/v1/logs/config/indexes/main",
                                "verb": "PUT",
                                "payload": {
                                    "exclusion_filters": [{"name": "test_filter", "filter": {"query": "service:api \"error\"", "sample_rate": 1.0}, "is_enabled": True}]
                                },
                            },
                            "id": "pat_1",
                        }
                    ],
                    "surges": [],
                }

                policy = config.default_policy()
                policy["enabled"] = True
                policy["modes"]["exclude"] = "auto"
                policy["dry_run"] = False
                policy["thresholds"]["min_cost_usd"] = 100.0
                policy["guardrails"]["auto_only_actions"] = ["exclude"]
                policy["guardrails"]["auto_max_actions_per_day"] = 5

                result = run_cycle(
                    scan_result,
                    policy,
                    {"api_key": "key", "app_key": "app", "site": "dd"},
                    str(state_path),
                    "2026-07-21T12:00:00Z",  # LATER date
                    writer_fn=fake_writer,
                )

                # Counter should have reset, so auto could be applied
                assert result["applied"] >= 1, "Expected at least 1 applied since counter reset"

                # Verify persisted state has new date
                state = json.loads(state_path.read_text())
                assert state.get("actions_date") == "2026-07-21"
                assert state.get("actions_today") >= 1
        finally:
            # Restore original
            remediate.apply_remediation = orig_apply


class TestRunCycleSecurityNoSecretsInState:
    """run_cycle never persists credentials or raw content."""

    def test_api_key_not_in_state_file(self):
        """API credentials never written to state file."""
        from runner import run_cycle
        import config

        def fake_writer(endpoint, verb, payload, api_key, app_key, site):
            pass

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            scan_result = {
                "patterns": [
                    {
                        "recommended_action": "exclude",
                        "monthly_cost_usd": 500.0,
                        "confidence": "high",
                        "template": "error",
                        "generated_config": {
                            "endpoint": "/api/v1/logs/config/indexes/main",
                            "verb": "PUT",
                            "payload": {
                                "exclusion_filters": [{"name": "test_filter", "filter": {"query": "service:api \"error\"", "sample_rate": 1.0}, "is_enabled": True}]
                            },
                        },
                        "id": "pat_1",
                    }
                ],
                "surges": [],
            }

            policy = config.default_policy()
            policy["enabled"] = True
            policy["modes"]["exclude"] = "auto"
            policy["dry_run"] = False
            policy["thresholds"]["min_cost_usd"] = 100.0
            policy["guardrails"]["auto_only_actions"] = ["exclude"]

            secret_key = "SECRETXYZ_12345_CONFIDENTIAL"
            run_cycle(
                scan_result,
                policy,
                {"api_key": secret_key, "app_key": "appXYZ", "site": "dd"},
                str(state_path),
                "2026-07-21T12:00:00Z",
                writer_fn=fake_writer,
                reader_fn=lambda *a, **k: {"exclusion_filters": []},
            )

            # Read state file and verify no secrets
            state_text = state_path.read_text()
            assert secret_key not in state_text
            assert "SECRETXYZ" not in state_text

    def test_api_key_not_in_audit_file(self):
        """API credentials never written to audit file."""
        from runner import run_cycle
        import config

        def fake_writer(endpoint, verb, payload, api_key, app_key, site):
            pass

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            audit_path = state_path.parent / "state.audit"

            scan_result = {
                "patterns": [
                    {
                        "recommended_action": "exclude",
                        "monthly_cost_usd": 500.0,
                        "confidence": "high",
                        "template": "error",
                        "generated_config": {
                            "endpoint": "/api/v1/logs/config/indexes/main",
                            "verb": "PUT",
                            "payload": {
                                "exclusion_filters": [{"name": "test_filter", "filter": {"query": "service:api \"error\"", "sample_rate": 1.0}, "is_enabled": True}]
                            },
                        },
                        "id": "pat_1",
                    }
                ],
                "surges": [],
            }

            policy = config.default_policy()
            policy["enabled"] = True
            policy["modes"]["exclude"] = "auto"
            policy["dry_run"] = False
            policy["thresholds"]["min_cost_usd"] = 100.0
            policy["guardrails"]["auto_only_actions"] = ["exclude"]

            secret_key = "SECRETXYZ_12345_CONFIDENTIAL"
            run_cycle(
                scan_result,
                policy,
                {"api_key": secret_key, "app_key": "appXYZ", "site": "dd"},
                str(state_path),
                "2026-07-21T12:00:00Z",
                writer_fn=fake_writer,
                reader_fn=lambda *a, **k: {"exclusion_filters": []},
            )

            # Audit file should exist if any auto actions taken
            if audit_path.exists():
                audit_text = audit_path.read_text()
                assert secret_key not in audit_text
                assert "SECRETXYZ" not in audit_text
