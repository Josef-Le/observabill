"""TDD tests for config.py protection policy engine — RED phase."""
import json
import os
import tempfile
import copy
import pytest

# Import config module (will exist after implementation)
# from config import (
#     DEFAULT_POLICY,
#     default_policy,
#     merge_policy,
#     validate_policy,
#     load_policy,
#     save_policy,
#     _confidence_rank,
#     decide,
# )


class TestDefaultPolicyStructure:
    """Test DEFAULT_POLICY and default_policy()."""

    def test_default_policy_exists_and_disabled(self):
        """DEFAULT_POLICY is a module constant with enabled=False."""
        from config import DEFAULT_POLICY

        assert isinstance(DEFAULT_POLICY, dict)
        assert DEFAULT_POLICY["enabled"] is False
        assert DEFAULT_POLICY["dry_run"] is True

    def test_default_policy_safe_mode_keys(self):
        """DEFAULT_POLICY has all required top-level keys."""
        from config import DEFAULT_POLICY

        assert "enabled" in DEFAULT_POLICY
        assert "dry_run" in DEFAULT_POLICY
        assert "modes" in DEFAULT_POLICY
        assert "thresholds" in DEFAULT_POLICY
        assert "guardrails" in DEFAULT_POLICY
        assert "channels" in DEFAULT_POLICY

    def test_default_policy_modes_all_recommend(self):
        """DEFAULT_POLICY modes default to 'recommend' (safe)."""
        from config import DEFAULT_POLICY

        modes = DEFAULT_POLICY["modes"]
        assert modes.get("exclude") == "recommend"
        assert modes.get("sample") == "recommend"
        assert modes.get("to_metric") == "recommend"
        assert modes.get("review") == "recommend"
        assert modes.get("new_pattern") == "alert"  # surge kinds alert by default
        assert modes.get("cost_surge") == "alert"
        assert modes.get("volume_surge") == "alert"

    def test_default_policy_thresholds(self):
        """DEFAULT_POLICY thresholds are reasonable defaults."""
        from config import DEFAULT_POLICY

        thresholds = DEFAULT_POLICY["thresholds"]
        assert thresholds["min_cost_usd"] == 100.0
        assert thresholds["surge_ratio"] == 1.30
        assert thresholds["wow_growth_pct"] == 15.0
        assert thresholds["new_pattern_min_cost_usd"] == 100.0
        assert thresholds["min_confidence_for_auto"] == "high"

    def test_default_policy_guardrails(self):
        """DEFAULT_POLICY guardrails are safe."""
        from config import DEFAULT_POLICY

        guardrails = DEFAULT_POLICY["guardrails"]
        assert guardrails["auto_max_actions_per_day"] == 5
        assert set(guardrails["auto_only_actions"]) == {"exclude", "sample"}

    def test_default_policy_channels_empty(self):
        """DEFAULT_POLICY channels start empty (must be user-configured)."""
        from config import DEFAULT_POLICY

        assert DEFAULT_POLICY["channels"]["email"] == ""
        assert DEFAULT_POLICY["channels"]["slack_webhook"] == ""

    def test_default_policy_function_returns_deep_copy(self):
        """default_policy() returns independent deep copy of DEFAULT_POLICY."""
        from config import default_policy

        p1 = default_policy()
        p2 = default_policy()

        # Different objects
        assert p1 is not p2
        assert p1["modes"] is not p2["modes"]
        assert p1["thresholds"] is not p2["thresholds"]

        # Equal values
        assert p1 == p2

        # Mutating one does not affect the other
        p1["enabled"] = True
        p1["modes"]["exclude"] = "auto"
        p1["thresholds"]["min_cost_usd"] = 999.0

        assert p2["enabled"] is False
        assert p2["modes"]["exclude"] == "recommend"
        assert p2["thresholds"]["min_cost_usd"] == 100.0

    def test_default_policy_deep_copy_independence(self):
        """default_policy() returns structure independent from DEFAULT_POLICY."""
        from config import DEFAULT_POLICY, default_policy

        p = default_policy()
        p["modes"]["exclude"] = "auto"

        # DEFAULT_POLICY unchanged
        assert DEFAULT_POLICY["modes"]["exclude"] == "recommend"


class TestMergePolicy:
    """Test merge_policy() for user overrides over defaults."""

    def test_merge_policy_empty_user_returns_defaults(self):
        """merge_policy({}) returns defaults."""
        from config import merge_policy, default_policy

        result = merge_policy({})
        expected = default_policy()

        assert result == expected

    def test_merge_policy_partial_override_top_level(self):
        """merge_policy({enabled: True}) overrides top-level key."""
        from config import merge_policy

        user = {"enabled": True}
        result = merge_policy(user)

        assert result["enabled"] is True
        assert result["dry_run"] is True  # Still default
        assert result["modes"]["exclude"] == "recommend"  # Still default

    def test_merge_policy_partial_override_modes(self):
        """merge_policy can override specific modes."""
        from config import merge_policy

        user = {"modes": {"exclude": "auto", "sample": "alert"}}
        result = merge_policy(user)

        assert result["modes"]["exclude"] == "auto"
        assert result["modes"]["sample"] == "alert"
        assert result["modes"]["to_metric"] == "recommend"  # Default
        assert result["modes"]["review"] == "recommend"  # Default

    def test_merge_policy_partial_override_thresholds(self):
        """merge_policy can override specific thresholds."""
        from config import merge_policy

        user = {"thresholds": {"min_cost_usd": 250.0}}
        result = merge_policy(user)

        assert result["thresholds"]["min_cost_usd"] == 250.0
        assert result["thresholds"]["surge_ratio"] == 1.30  # Default
        assert result["thresholds"]["wow_growth_pct"] == 15.0  # Default

    def test_merge_policy_partial_override_guardrails(self):
        """merge_policy can override guardrails."""
        from config import merge_policy

        user = {"guardrails": {"auto_max_actions_per_day": 10}}
        result = merge_policy(user)

        assert result["guardrails"]["auto_max_actions_per_day"] == 10
        assert result["guardrails"]["auto_only_actions"] == ["exclude", "sample"]  # Default

    def test_merge_policy_partial_override_channels(self):
        """merge_policy can set email/slack."""
        from config import merge_policy

        user = {"channels": {"email": "alerts@example.com"}}
        result = merge_policy(user)

        assert result["channels"]["email"] == "alerts@example.com"
        assert result["channels"]["slack_webhook"] == ""  # Default

    def test_merge_policy_unknown_mode_key_dropped(self):
        """merge_policy drops unknown keys from modes."""
        from config import merge_policy

        user = {"modes": {"exclude": "auto", "unknown_kind": "alert"}}
        result = merge_policy(user)

        assert "exclude" in result["modes"]
        assert "unknown_kind" not in result["modes"]

    def test_merge_policy_unknown_threshold_key_dropped(self):
        """merge_policy drops unknown keys from thresholds."""
        from config import merge_policy

        user = {"thresholds": {"min_cost_usd": 200.0, "unknown_threshold": 999.0}}
        result = merge_policy(user)

        assert "min_cost_usd" in result["thresholds"]
        assert "unknown_threshold" not in result["thresholds"]

    def test_merge_policy_unknown_guardrail_key_dropped(self):
        """merge_policy drops unknown keys from guardrails."""
        from config import merge_policy

        user = {"guardrails": {"auto_max_actions_per_day": 5, "unknown_guard": True}}
        result = merge_policy(user)

        assert "auto_max_actions_per_day" in result["guardrails"]
        assert "unknown_guard" not in result["guardrails"]

    def test_merge_policy_unknown_channel_key_dropped(self):
        """merge_policy drops unknown keys from channels."""
        from config import merge_policy

        user = {"channels": {"email": "test@example.com", "unknown_channel": "value"}}
        result = merge_policy(user)

        assert "email" in result["channels"]
        assert "unknown_channel" not in result["channels"]

    def test_merge_policy_deep_nesting_independence(self):
        """merge_policy returns independent nested structures."""
        from config import merge_policy

        user = {"modes": {"exclude": "auto"}}
        result = merge_policy(user)

        # Mutate result
        result["modes"]["sample"] = "auto"

        # Remerge with same user dict
        result2 = merge_policy(user)
        assert result2["modes"]["sample"] == "recommend"


class TestValidatePolicy:
    """Test validate_policy() for correctness checks."""

    def test_validate_policy_valid_default(self):
        """validate_policy accepts default policy."""
        from config import default_policy, validate_policy

        ok, errors = validate_policy(default_policy())

        assert ok is True
        assert errors == []

    def test_validate_policy_bad_mode_value(self):
        """validate_policy rejects invalid mode value."""
        from config import default_policy, validate_policy

        policy = default_policy()
        policy["modes"]["exclude"] = "invalid_mode"

        ok, errors = validate_policy(policy)

        assert ok is False
        assert any("modes" in e.lower() or "exclude" in e.lower() for e in errors)

    def test_validate_policy_all_bad_modes_reported(self):
        """validate_policy reports ALL bad modes."""
        from config import default_policy, validate_policy

        policy = default_policy()
        policy["modes"]["exclude"] = "bad1"
        policy["modes"]["sample"] = "bad2"

        ok, errors = validate_policy(policy)

        assert ok is False
        assert len(errors) >= 2

    def test_validate_policy_negative_threshold(self):
        """validate_policy rejects negative thresholds."""
        from config import default_policy, validate_policy

        policy = default_policy()
        policy["thresholds"]["min_cost_usd"] = -10.0

        ok, errors = validate_policy(policy)

        assert ok is False
        assert any("threshold" in e.lower() or "negative" in e.lower() for e in errors)

    def test_validate_policy_non_numeric_threshold(self):
        """validate_policy rejects non-numeric thresholds."""
        from config import default_policy, validate_policy

        policy = default_policy()
        policy["thresholds"]["surge_ratio"] = "not_a_number"

        ok, errors = validate_policy(policy)

        assert ok is False

    def test_validate_policy_bad_confidence(self):
        """validate_policy rejects invalid min_confidence_for_auto."""
        from config import default_policy, validate_policy

        policy = default_policy()
        policy["thresholds"]["min_confidence_for_auto"] = "ultra_high"

        ok, errors = validate_policy(policy)

        assert ok is False
        assert any("confidence" in e.lower() for e in errors)

    def test_validate_policy_bad_confidence_valid_options(self):
        """validate_policy accepts high/medium/low for min_confidence_for_auto."""
        from config import default_policy, validate_policy

        policy = default_policy()

        for conf in ["high", "medium", "low"]:
            policy["thresholds"]["min_confidence_for_auto"] = conf
            ok, errors = validate_policy(policy)
            assert ok is True, f"Should accept confidence={conf}, got errors={errors}"

    def test_validate_policy_bad_auto_only_action(self):
        """validate_policy rejects auto_only_actions not in allowed set."""
        from config import default_policy, validate_policy

        policy = default_policy()
        policy["guardrails"]["auto_only_actions"] = ["exclude", "invalid_action"]

        ok, errors = validate_policy(policy)

        assert ok is False
        assert any("auto_only" in e.lower() or "invalid_action" in e.lower() for e in errors)

    def test_validate_policy_all_bad_auto_only_actions_reported(self):
        """validate_policy reports ALL invalid auto_only_actions."""
        from config import default_policy, validate_policy

        policy = default_policy()
        policy["guardrails"]["auto_only_actions"] = ["bad1", "bad2", "bad3"]

        ok, errors = validate_policy(policy)

        assert ok is False
        assert len(errors) >= 3

    def test_validate_policy_channels_are_strings(self):
        """validate_policy ensures channels are strings."""
        from config import default_policy, validate_policy

        policy = default_policy()
        policy["channels"]["email"] = 123  # Not a string

        ok, errors = validate_policy(policy)

        assert ok is False
        assert any("channel" in e.lower() or "string" in e.lower() for e in errors)

    def test_validate_policy_multiple_errors_all_reported(self):
        """validate_policy reports ALL errors found."""
        from config import default_policy, validate_policy

        policy = default_policy()
        policy["modes"]["exclude"] = "bad"
        policy["thresholds"]["min_cost_usd"] = -50.0
        policy["thresholds"]["min_confidence_for_auto"] = "invalid"
        policy["channels"]["email"] = 456

        ok, errors = validate_policy(policy)

        assert ok is False
        assert len(errors) >= 4  # At least 4 distinct errors


class TestSaveAndLoadPolicy:
    """Test save_policy() and load_policy()."""

    def test_save_policy_writes_json_0600(self):
        """save_policy writes file with mode 0600."""
        from config import default_policy, save_policy

        policy = default_policy()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "policy.json")
            save_policy(path, policy)

            # File exists
            assert os.path.exists(path)

            # Mode is 0600
            mode = os.stat(path).st_mode
            perms = oct(mode)[-3:]
            assert perms == "600", f"Expected 0600, got {perms}"

    def test_save_policy_writes_valid_json(self):
        """save_policy writes valid JSON that can be parsed."""
        from config import default_policy, save_policy

        policy = default_policy()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "policy.json")
            save_policy(path, policy)

            with open(path) as f:
                loaded = json.load(f)

            assert loaded == policy

    def test_save_policy_raises_on_invalid_policy(self):
        """save_policy raises ValueError if policy is invalid."""
        from config import default_policy, save_policy

        policy = default_policy()
        policy["modes"]["exclude"] = "invalid_mode"

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "policy.json")

            with pytest.raises(ValueError) as exc_info:
                save_policy(path, policy)

            # Error message contains useful info
            assert "invalid" in str(exc_info.value).lower()

    def test_load_policy_missing_file_returns_defaults(self):
        """load_policy returns default_policy() if file missing."""
        from config import load_policy, default_policy

        result = load_policy("/nonexistent/path/policy.json")

        assert result == default_policy()

    def test_load_policy_invalid_json_returns_defaults(self):
        """load_policy returns default_policy() if JSON invalid."""
        from config import load_policy, default_policy

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "policy.json")
            with open(path, "w") as f:
                f.write("not valid json {{{")

            result = load_policy(path)

            assert result == default_policy()

    def test_load_policy_valid_file_returns_merged_policy(self):
        """load_policy loads and merges file over defaults."""
        from config import load_policy, default_policy, save_policy

        user_policy = {"enabled": True, "modes": {"exclude": "auto"}}

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "policy.json")
            with open(path, "w") as f:
                json.dump(user_policy, f)

            result = load_policy(path)

            assert result["enabled"] is True
            assert result["modes"]["exclude"] == "auto"
            assert result["modes"]["sample"] == "recommend"  # Still default
            assert result["dry_run"] is True  # Still default

    def test_save_load_roundtrip(self):
        """save_policy and load_policy roundtrip correctly."""
        from config import default_policy, save_policy, load_policy

        original = default_policy()
        original["enabled"] = True
        original["modes"]["exclude"] = "auto"
        original["thresholds"]["min_cost_usd"] = 250.0

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "policy.json")
            save_policy(path, original)
            loaded = load_policy(path)

            assert loaded == original


class TestConfidenceRank:
    """Test _confidence_rank() helper."""

    def test_confidence_rank_high(self):
        """_confidence_rank('high') returns 3."""
        from config import _confidence_rank

        assert _confidence_rank("high") == 3

    def test_confidence_rank_medium(self):
        """_confidence_rank('medium') returns 2."""
        from config import _confidence_rank

        assert _confidence_rank("medium") == 2

    def test_confidence_rank_low(self):
        """_confidence_rank('low') returns 1."""
        from config import _confidence_rank

        assert _confidence_rank("low") == 1

    def test_confidence_rank_invalid(self):
        """_confidence_rank(invalid) returns 0."""
        from config import _confidence_rank

        assert _confidence_rank("ultra_high") == 0
        assert _confidence_rank("") == 0
        assert _confidence_rank(None) == 0


class TestDecide:
    """Test decide() disposition engine."""

    def test_decide_disabled_returns_recommend(self):
        """decide returns 'recommend' when policy disabled, regardless of mode."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = False
        policy["modes"]["exclude"] = "auto"  # Even with auto mode

        finding = {"kind": "exclude", "monthly_cost_usd": 500.0, "confidence": "high"}

        result = decide(finding, policy)

        assert result == "recommend"

    def test_decide_mode_recommend_returns_recommend(self):
        """decide returns 'recommend' when mode is 'recommend'."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "recommend"

        finding = {"kind": "exclude", "monthly_cost_usd": 500.0}

        result = decide(finding, policy)

        assert result == "recommend"

    def test_decide_mode_alert_above_threshold(self):
        """decide returns 'alert' for alert mode above threshold."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "alert"
        policy["thresholds"]["min_cost_usd"] = 100.0

        finding = {"kind": "exclude", "monthly_cost_usd": 500.0}

        result = decide(finding, policy)

        assert result == "alert"

    def test_decide_mode_alert_below_threshold(self):
        """decide returns 'recommend' for alert mode below threshold."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "alert"
        policy["thresholds"]["min_cost_usd"] = 100.0

        finding = {"kind": "exclude", "monthly_cost_usd": 50.0}

        result = decide(finding, policy)

        assert result == "recommend"

    def test_decide_mode_auto_eligible_returns_auto(self):
        """decide returns 'auto' when all conditions met."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "auto"
        policy["thresholds"]["min_cost_usd"] = 100.0
        policy["thresholds"]["min_confidence_for_auto"] = "high"
        policy["guardrails"]["auto_max_actions_per_day"] = 5
        policy["guardrails"]["auto_only_actions"] = ["exclude", "sample"]

        finding = {"kind": "exclude", "monthly_cost_usd": 500.0, "confidence": "high"}

        result = decide(finding, policy, actions_today=0)

        assert result == "auto"

    def test_decide_mode_auto_below_threshold(self):
        """decide returns 'recommend' for auto mode below threshold."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "auto"
        policy["thresholds"]["min_cost_usd"] = 100.0

        finding = {"kind": "exclude", "monthly_cost_usd": 50.0, "confidence": "high"}

        result = decide(finding, policy, actions_today=0)

        assert result == "recommend"

    def test_decide_mode_auto_kind_not_in_whitelist(self):
        """decide returns 'alert' if kind not in auto_only_actions."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["review"] = "auto"
        policy["guardrails"]["auto_only_actions"] = ["exclude", "sample"]

        finding = {"kind": "review", "monthly_cost_usd": 500.0, "confidence": "high"}

        result = decide(finding, policy, actions_today=0)

        assert result == "alert"

    def test_decide_mode_auto_low_confidence(self):
        """decide returns 'alert' if confidence below min_confidence_for_auto."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "auto"
        policy["thresholds"]["min_confidence_for_auto"] = "high"

        finding = {"kind": "exclude", "monthly_cost_usd": 500.0, "confidence": "medium"}

        result = decide(finding, policy, actions_today=0)

        assert result == "alert"

    def test_decide_mode_auto_daily_cap_hit(self):
        """decide returns 'alert' if daily action cap reached."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "auto"
        policy["guardrails"]["auto_max_actions_per_day"] = 5

        finding = {"kind": "exclude", "monthly_cost_usd": 500.0, "confidence": "high"}

        result = decide(finding, policy, actions_today=5)

        assert result == "alert"

    def test_decide_mode_auto_just_under_daily_cap(self):
        """decide returns 'auto' if just under daily cap."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "auto"
        policy["guardrails"]["auto_max_actions_per_day"] = 5

        finding = {"kind": "exclude", "monthly_cost_usd": 500.0, "confidence": "high"}

        result = decide(finding, policy, actions_today=4)

        assert result == "auto"

    def test_decide_default_confidence_medium(self):
        """decide defaults confidence to 'medium' if missing."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "auto"
        policy["thresholds"]["min_confidence_for_auto"] = "high"

        finding = {"kind": "exclude", "monthly_cost_usd": 500.0}
        # No confidence key

        result = decide(finding, policy, actions_today=0)

        # Should return alert because medium < high
        assert result == "alert"

    def test_decide_new_pattern_uses_new_pattern_min_cost(self):
        """decide uses new_pattern_min_cost_usd for new_pattern kind."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["new_pattern"] = "alert"
        policy["thresholds"]["min_cost_usd"] = 100.0
        policy["thresholds"]["new_pattern_min_cost_usd"] = 500.0

        # Below general threshold, but below new_pattern threshold too
        finding = {"kind": "new_pattern", "monthly_cost_usd": 250.0}

        result = decide(finding, policy)

        assert result == "recommend"

    def test_decide_new_pattern_above_special_threshold(self):
        """decide returns 'alert' for new_pattern above new_pattern_min_cost_usd."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["new_pattern"] = "alert"
        policy["thresholds"]["new_pattern_min_cost_usd"] = 500.0

        finding = {"kind": "new_pattern", "monthly_cost_usd": 600.0}

        result = decide(finding, policy)

        assert result == "alert"

    def test_decide_nonexistent_kind_defaults_recommend(self):
        """decide returns 'recommend' if kind not in modes dict."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True

        finding = {"kind": "unknown_kind", "monthly_cost_usd": 500.0}

        result = decide(finding, policy)

        assert result == "recommend"

    def test_decide_never_raises(self):
        """decide never raises, always returns valid disposition."""
        from config import decide, default_policy

        policy = default_policy()
        finding = {}

        # Should not raise
        result = decide(finding, policy)

        assert result in {"auto", "alert", "recommend", "ignore"}

    def test_decide_all_surges_use_min_cost_usd(self):
        """decide uses min_cost_usd for all surge kinds (cost/volume)."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["cost_surge"] = "alert"
        policy["modes"]["volume_surge"] = "alert"
        policy["thresholds"]["min_cost_usd"] = 100.0

        for kind in ["cost_surge", "volume_surge"]:
            # Below threshold
            finding = {"kind": kind, "monthly_cost_usd": 50.0}
            result = decide(finding, policy)
            assert result == "recommend", f"Failed for {kind} below threshold"

            # Above threshold
            finding = {"kind": kind, "monthly_cost_usd": 500.0}
            result = decide(finding, policy)
            assert result == "alert", f"Failed for {kind} above threshold"

    def test_decide_dispositions_are_valid(self):
        """decide only returns valid dispositions."""
        from config import decide, default_policy

        valid = {"auto", "alert", "recommend", "ignore"}
        policy = default_policy()

        test_cases = [
            ({"kind": "exclude", "monthly_cost_usd": 100.0}, 0),
            ({"kind": "sample", "monthly_cost_usd": 500.0}, 2),
            ({"kind": "new_pattern", "monthly_cost_usd": 1000.0}, 0),
        ]

        for finding, actions_today in test_cases:
            result = decide(finding, policy, actions_today)
            assert result in valid, f"Invalid disposition {result}"


class TestDecideTruthTable:
    """Comprehensive truth table verification for decide()."""

    def test_decide_truth_table_disabled_policy(self):
        """Verify decision matrix when policy disabled."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = False

        # All should return "recommend" regardless of mode
        for mode in ["recommend", "alert", "auto"]:
            policy["modes"]["exclude"] = mode
            result = decide(
                {"kind": "exclude", "monthly_cost_usd": 1000.0, "confidence": "high"},
                policy,
                actions_today=0,
            )
            assert result == "recommend"

    def test_decide_truth_table_enabled_recommend_mode(self):
        """Verify decision matrix for recommend mode."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "recommend"

        # Cost doesn't matter
        for cost in [10.0, 100.0, 1000.0]:
            result = decide(
                {"kind": "exclude", "monthly_cost_usd": cost, "confidence": "high"},
                policy,
            )
            assert result == "recommend"

    def test_decide_truth_table_enabled_alert_mode(self):
        """Verify decision matrix for alert mode."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "alert"
        policy["thresholds"]["min_cost_usd"] = 100.0

        # Above threshold -> alert
        result = decide(
            {"kind": "exclude", "monthly_cost_usd": 500.0}, policy
        )
        assert result == "alert"

        # Below threshold -> recommend
        result = decide(
            {"kind": "exclude", "monthly_cost_usd": 50.0}, policy
        )
        assert result == "recommend"

    def test_decide_truth_table_auto_all_conditions(self):
        """Verify decision matrix for auto mode with all conditions."""
        from config import decide, default_policy

        policy = default_policy()
        policy["enabled"] = True
        policy["modes"]["exclude"] = "auto"
        policy["thresholds"]["min_cost_usd"] = 100.0
        policy["thresholds"]["min_confidence_for_auto"] = "high"
        policy["guardrails"]["auto_max_actions_per_day"] = 5
        policy["guardrails"]["auto_only_actions"] = ["exclude", "sample"]

        # All conditions met
        result = decide(
            {"kind": "exclude", "monthly_cost_usd": 500.0, "confidence": "high"},
            policy,
            actions_today=0,
        )
        assert result == "auto"

        # Cost below threshold
        result = decide(
            {"kind": "exclude", "monthly_cost_usd": 50.0, "confidence": "high"},
            policy,
            actions_today=0,
        )
        assert result == "recommend"

        # Confidence insufficient
        result = decide(
            {"kind": "exclude", "monthly_cost_usd": 500.0, "confidence": "low"},
            policy,
            actions_today=0,
        )
        assert result == "alert"

        # Daily cap reached
        result = decide(
            {"kind": "exclude", "monthly_cost_usd": 500.0, "confidence": "high"},
            policy,
            actions_today=5,
        )
        assert result == "alert"

        # Kind not in auto_only_actions
        policy["modes"]["review"] = "auto"
        result = decide(
            {"kind": "review", "monthly_cost_usd": 500.0, "confidence": "high"},
            policy,
            actions_today=0,
        )
        assert result == "alert"
