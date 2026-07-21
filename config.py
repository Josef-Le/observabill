"""
config.py — Protection policy engine for ObservaBill.

Each customer configures per finding type whether ObservaBill should:
  - RECOMMEND: dashboard only, never alert/auto
  - ALERT: notify via email/Slack
  - AUTO: auto-remediate (if guardrails allow)

All changes are safe-by-default: policy disabled, dry_run on, nothing auto.
"""
import json
import os
import copy


DEFAULT_POLICY = {
    "enabled": False,  # Master switch — when False, all decisions return "recommend"
    "dry_run": True,  # When True, AUTO decisions are simulated, never written
    "modes": {
        # Disposition per finding type
        "exclude": "recommend",      # exclude recommendation
        "sample": "recommend",       # sample recommendation
        "to_metric": "recommend",    # convert-to-metric recommendation
        "review": "recommend",       # review recommendation
        "new_pattern": "alert",      # surge kinds
        "cost_surge": "alert",
        "volume_surge": "alert",
    },
    "thresholds": {
        "min_cost_usd": 100.0,       # Ignore findings below this $/mo for alert/auto
        "surge_ratio": 1.30,         # Cost surge if current >= baseline * ratio
        "wow_growth_pct": 15.0,      # Week-over-week growth threshold
        "new_pattern_min_cost_usd": 100.0,  # Separate threshold for new patterns
        "min_confidence_for_auto": "high",  # high | medium | low
    },
    "guardrails": {
        "auto_max_actions_per_day": 5,
        "auto_only_actions": ["exclude", "sample"],  # Only these may ever auto-apply
    },
    "channels": {
        "email": "",        # Email address for alerts (must be user-configured)
        "slack_webhook": "",  # Slack webhook URL (must be user-configured)
    },
}


def default_policy() -> dict:
    """
    Returns a DEEP copy of DEFAULT_POLICY.

    Each call returns an independent copy so modifications don't leak.
    """
    return copy.deepcopy(DEFAULT_POLICY)


def merge_policy(user: dict) -> dict:
    """
    Deep-merge user dict over DEFAULT_POLICY.

    Unknown keys in nested dicts (modes, thresholds, guardrails, channels)
    are dropped; only known keys survive.

    Args:
        user: Partial policy dict to merge over defaults

    Returns:
        Merged policy dict with all keys from defaults + user overrides
    """
    result = default_policy()

    if not user:
        return result

    # Top-level scalar keys: enabled, dry_run
    if "enabled" in user:
        result["enabled"] = user["enabled"]
    if "dry_run" in user:
        result["dry_run"] = user["dry_run"]

    # modes: merge only known keys
    if "modes" in user and isinstance(user["modes"], dict):
        known_modes = set(result["modes"].keys())
        for key, value in user["modes"].items():
            if key in known_modes:
                result["modes"][key] = value

    # thresholds: merge only known keys
    if "thresholds" in user and isinstance(user["thresholds"], dict):
        known_thresholds = set(result["thresholds"].keys())
        for key, value in user["thresholds"].items():
            if key in known_thresholds:
                result["thresholds"][key] = value

    # guardrails: merge only known keys
    if "guardrails" in user and isinstance(user["guardrails"], dict):
        known_guardrails = set(result["guardrails"].keys())
        for key, value in user["guardrails"].items():
            if key in known_guardrails:
                result["guardrails"][key] = value

    # channels: merge only known keys
    if "channels" in user and isinstance(user["channels"], dict):
        known_channels = set(result["channels"].keys())
        for key, value in user["channels"].items():
            if key in known_channels:
                result["channels"][key] = value

    return result


def validate_policy(policy: dict) -> tuple[bool, list[str]]:
    """
    Validate policy dict for correctness.

    Checks:
      - Every modes value ∈ {"recommend", "alert", "auto"}
      - Thresholds numeric and >= 0
      - min_confidence_for_auto ∈ {"high", "medium", "low"}
      - auto_only_actions ⊆ {"exclude", "sample", "to_metric"}
      - channels are strings

    Args:
        policy: Policy dict to validate

    Returns:
        (ok, errors): (bool, list[str]) — ok=True if valid, errors=list of error messages
    """
    errors = []

    valid_mode_values = {"recommend", "alert", "auto"}
    for key, value in policy.get("modes", {}).items():
        if value not in valid_mode_values:
            errors.append(f"modes[{key}]={value} must be one of {valid_mode_values}")

    thresholds = policy.get("thresholds", {})
    numeric_keys = {
        "min_cost_usd",
        "surge_ratio",
        "wow_growth_pct",
        "new_pattern_min_cost_usd",
    }
    for key in numeric_keys:
        if key in thresholds:
            value = thresholds[key]
            try:
                num_val = float(value)
                if num_val < 0:
                    errors.append(f"thresholds[{key}]={value} must be >= 0")
            except (TypeError, ValueError):
                errors.append(f"thresholds[{key}]={value} must be numeric")

    confidence = thresholds.get("min_confidence_for_auto")
    if confidence not in {"high", "medium", "low"}:
        errors.append(
            f"thresholds[min_confidence_for_auto]={confidence} must be high|medium|low"
        )

    valid_auto_only = {"exclude", "sample", "to_metric"}
    guardrails = policy.get("guardrails", {})
    auto_only_actions = guardrails.get("auto_only_actions", [])
    if isinstance(auto_only_actions, list):
        for action in auto_only_actions:
            if action not in valid_auto_only:
                errors.append(
                    f"guardrails[auto_only_actions] contains {action}, "
                    f"must be subset of {valid_auto_only}"
                )

    channels = policy.get("channels", {})
    for key, value in channels.items():
        if not isinstance(value, str):
            errors.append(f"channels[{key}]={value} must be string")

    return (len(errors) == 0, errors)


def load_policy(path: str) -> dict:
    """
    Load policy from JSON file, merge over defaults.

    If file missing or invalid JSON, returns default_policy().

    Args:
        path: File path to load from

    Returns:
        Merged policy dict (user config + defaults)
    """
    if not os.path.exists(path):
        return default_policy()

    try:
        with open(path, "r") as f:
            user_policy = json.load(f)
    except (json.JSONDecodeError, IOError):
        return default_policy()

    if not isinstance(user_policy, dict):
        return default_policy()

    return merge_policy(user_policy)


def save_policy(path: str, policy: dict) -> None:
    """
    Validate and save policy to JSON file with mode 0600.

    Args:
        path: File path to write to
        policy: Policy dict to save

    Raises:
        ValueError: If policy validation fails
    """
    ok, errors = validate_policy(policy)
    if not ok:
        raise ValueError(f"Invalid policy: {'; '.join(errors)}")

    # Write with mode 0600 (owner read/write only)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(policy, f, indent=2)
    except Exception:
        os.close(fd)
        raise


def _confidence_rank(confidence: str) -> int:
    """
    Map confidence string to numeric rank.

    Args:
        confidence: "high", "medium", "low", or invalid

    Returns:
        int: high=3, medium=2, low=1, invalid=0
    """
    ranks = {"high": 3, "medium": 2, "low": 1}
    return ranks.get(confidence, 0)


def decide(finding: dict, policy: dict, actions_today: int = 0) -> str:
    """
    Decide disposition for a finding based on policy.

    Returns disposition ∈ {"auto", "alert", "recommend", "ignore"}:
      - "auto": auto-remediate (if policy allows)
      - "alert": notify user
      - "recommend": surface in dashboard only
      - "ignore": don't surface (reserved for future)

    Logic:
      1. If policy disabled: return "recommend"
      2. If mode="recommend": return "recommend"
      3. If mode="alert": check cost threshold; return "alert" if above, else "recommend"
      4. If mode="auto":
         a. If kind not in auto_only_actions: downgrade to "alert"
         b. If confidence < min_confidence_for_auto: downgrade to "alert"
         c. If actions_today >= auto_max_actions_per_day: downgrade to "alert"
         d. Otherwise return "auto"

    Args:
        finding: dict with keys: kind, monthly_cost_usd (float, default 0),
                 confidence (str, default "medium")
        policy: Policy dict from default_policy() or load_policy()
        actions_today: Number of auto actions already executed today

    Returns:
        str: One of {"auto", "alert", "recommend", "ignore"}
    """
    # If policy disabled, always recommend (safe)
    if not policy.get("enabled", False):
        return "recommend"

    kind = finding.get("kind", "unknown")
    mode = policy.get("modes", {}).get(kind, "recommend")
    cost = finding.get("monthly_cost_usd", 0)
    confidence = finding.get("confidence", "medium")

    # Mode = recommend -> always recommend
    if mode == "recommend":
        return "recommend"

    # For alert/auto, check cost threshold
    thresholds = policy.get("thresholds", {})

    # new_pattern uses its own threshold
    if kind == "new_pattern":
        threshold = thresholds.get("new_pattern_min_cost_usd", 100.0)
    else:
        threshold = thresholds.get("min_cost_usd", 100.0)

    if cost < threshold:
        return "recommend"

    # Cost is above threshold; mode is alert or auto
    if mode == "alert":
        return "alert"

    # Mode = auto; apply guardrails
    if mode == "auto":
        guardrails = policy.get("guardrails", {})
        auto_only = guardrails.get("auto_only_actions", [])

        # Check if kind is in auto_only whitelist
        if kind not in auto_only:
            return "alert"

        # Check confidence
        min_confidence = thresholds.get("min_confidence_for_auto", "high")
        if _confidence_rank(confidence) < _confidence_rank(min_confidence):
            return "alert"

        # Check daily cap
        max_per_day = guardrails.get("auto_max_actions_per_day", 5)
        if actions_today >= max_per_day:
            return "alert"

        # All guardrails passed
        return "auto"

    # Fallback (should not reach if mode is in {recommend, alert, auto})
    return "recommend"
