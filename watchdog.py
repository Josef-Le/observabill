"""Near-real-time waste watchdog — detects new/surging noisy log patterns and alerts."""
import hashlib
import json
import os
import re
from typing import Any, Callable, Optional


def _scrub_template_for_alert(template: str) -> str:
    """
    Minimal inline scrubbing of template before embedding in alert body.

    Replaces JWT tokens and long opaque sequences with placeholders.
    Complements template masking; this is belt-and-suspenders.

    Args:
        template: Template string that may contain secrets.

    Returns:
        Template with JWT and long IDs replaced.
    """
    if not template:
        return template
    # JWT tokens: eyJ[...].eyJ[...].sig
    s = re.sub(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", "<JWT>", template)
    # Long AWS keys and tokens (16+ chars of letters/digits)
    s = re.sub(r"\b(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{16,}\b", "<ID>", s)
    return s


def fingerprint(template: str) -> str:
    """
    Generate a stable 16-char hex fingerprint for a template string.

    Args:
        template: Log pattern template (may contain placeholders like <id>).

    Returns:
        16-character hex string (SHA256 prefix).
    """
    return hashlib.sha256(template.encode()).hexdigest()[:16]


def load_state(path: str) -> dict:
    """
    Load watchdog state from JSON file.

    Args:
        path: File path to load from.

    Returns:
        Parsed dict, or {} if file missing or empty.
    """
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: str, state: dict) -> None:
    """
    Save watchdog state to JSON file with mode 0600 (user read/write only).

    Args:
        path: File path to write to.
        state: Dict to serialize.
    """
    # Create file with secure permissions using os.open
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
    except Exception:
        os.close(fd)
        raise


def check_for_alerts(
    scan_result: dict,
    baselines: dict,
    min_new_cost: float = 100.0,
    surge_ratio: float = 1.30,
) -> list:
    """
    Detect new patterns, cost surges, and volume anomalies.

    Args:
        scan_result: Output from scan (has pattern_leaderboard and surges).
        baselines: Persisted baselines dict (shape: {"templates": {...}, "updated": iso}).
        min_new_cost: Minimum monthly cost to alert on new patterns.
        surge_ratio: Cost multiplier threshold for surges (e.g., 1.30 = 30% increase).

    Returns:
        List of alert dicts, each with alert_id, kind, template, fingerprint, monthly_cost_usd, etc.
    """
    alerts = []

    # Track which fingerprints we've already alerted on in this run
    processed_fps = set()

    # Process pattern_leaderboard (new patterns + cost surges)
    for row in scan_result.get("pattern_leaderboard", []):
        template = row.get("template", "")
        monthly_cost = row.get("monthly_cost_usd", 0.0)
        monthly_events = row.get("monthly_events", 0)

        fp = fingerprint(template)
        processed_fps.add(fp)

        baseline_templates = baselines.get("templates", {})

        # Check if pattern is NEW
        if fp not in baseline_templates:
            if monthly_cost >= min_new_cost:
                alert_id = fingerprint(f"new_pattern|{template}")
                alerts.append(
                    {
                        "alert_id": alert_id,
                        "kind": "new_pattern",
                        "template": template,
                        "fingerprint": fp,
                        "monthly_cost_usd": monthly_cost,
                        "onset": scan_result.get("scan_time", ""),
                    }
                )
        else:
            # Pattern exists in baseline — check for cost surge
            baseline_cost = baseline_templates[fp].get("monthly_cost_usd", 1.0)
            ratio = monthly_cost / max(baseline_cost, 1.0)

            if ratio >= surge_ratio and monthly_cost >= min_new_cost:
                alert_id = fingerprint(f"cost_surge|{template}")
                alerts.append(
                    {
                        "alert_id": alert_id,
                        "kind": "cost_surge",
                        "template": template,
                        "fingerprint": fp,
                        "monthly_cost_usd": monthly_cost,
                        "baseline_cost": baseline_cost,
                        "ratio": ratio,
                    }
                )

    # Process surges (volume anomalies)
    for surge in scan_result.get("surges", []):
        monthly_cost = surge.get("monthly_cost_usd", 0.0)

        # Only alert if cost >= min threshold
        if monthly_cost >= 50.0:  # Hardcoded threshold for volume_surge
            template = surge.get("template", surge.get("series_id", ""))
            alert_id = fingerprint(f"volume_surge|{template}")
            alerts.append(
                {
                    "alert_id": alert_id,
                    "kind": "volume_surge",
                    "template": template,
                    "monthly_cost_usd": monthly_cost,
                }
            )

    return alerts


def dedup_alerts(alerts: list, sent_ids: set) -> list:
    """
    Filter out alerts already sent.

    Args:
        alerts: List of alert dicts (each must have alert_id).
        sent_ids: Set of alert IDs already notified.

    Returns:
        List of fresh alerts not in sent_ids.
    """
    return [a for a in alerts if a.get("alert_id") not in sent_ids]


def update_baselines(baselines: dict, scan_result: dict, now_iso: str) -> dict:
    """
    Update baselines with current scan results.

    Preserves first_seen for existing patterns, updates last_seen and costs.
    Only stores masked template + numeric data — never raw content or keys.

    Args:
        baselines: Existing baselines dict.
        scan_result: Current scan result with pattern_leaderboard.
        now_iso: ISO timestamp for last_seen and updated fields.

    Returns:
        New baselines dict (does not mutate input).
    """
    new_baselines = {
        "templates": dict(baselines.get("templates", {})),
        "updated": now_iso,
    }

    for row in scan_result.get("pattern_leaderboard", []):
        template = row.get("template", "")
        monthly_cost = row.get("monthly_cost_usd", 0.0)
        monthly_events = row.get("monthly_events", 0)

        fp = fingerprint(template)

        # Preserve first_seen if exists, otherwise set to now
        if fp in new_baselines["templates"]:
            first_seen = new_baselines["templates"][fp].get("first_seen", now_iso)
        else:
            first_seen = now_iso

        # Store only masked template + numbers (no sample_redacted, no keys)
        new_baselines["templates"][fp] = {
            "template": template,
            "monthly_cost_usd": monthly_cost,
            "monthly_events": monthly_events,
            "first_seen": first_seen,
            "last_seen": now_iso,
        }

    return new_baselines


def format_alert(alert: dict, exclude_base_url: str = "") -> dict:
    """
    Format an alert as email subject + body.

    Args:
        alert: Alert dict from check_for_alerts.
        exclude_base_url: Optional base URL for one-click exclude link.

    Returns:
        Dict with "subject" and "body" keys (no secrets).
    """
    kind = alert.get("kind", "unknown")
    template = alert.get("template", "")
    monthly_cost = alert.get("monthly_cost_usd", 0.0)
    fingerprint_val = alert.get("fingerprint", "")

    # Scrub the template before embedding in the body (belt-and-suspenders)
    scrubbed_template = _scrub_template_for_alert(template)

    if kind == "new_pattern":
        subject = f"ObservaBill Alert: New High-Cost Pattern Detected"
        body = f"""A new noisy log pattern has been detected costing ${monthly_cost:.2f}/month.

Pattern: {scrubbed_template}
Pattern ID: {fingerprint_val}
Monthly Cost: ${monthly_cost:.2f}

This pattern may indicate a systemic issue (e.g., missing index, inefficient query, or unhandled error).
Please investigate and optimize to reduce cloud costs.
"""
    elif kind == "cost_surge":
        baseline_cost = alert.get("baseline_cost", 1.0)
        ratio = alert.get("ratio", 1.0)
        subject = f"ObservaBill Alert: Cost Surge Detected ({ratio:.1f}x)"
        body = f"""A known pattern has surged in cost, indicating increased volume or activity.

Pattern: {scrubbed_template}
Pattern ID: {fingerprint_val}
Current Monthly Cost: ${monthly_cost:.2f}
Previous Monthly Cost: ${baseline_cost:.2f}
Surge Ratio: {ratio:.2f}x

This may indicate a new bottleneck or increased user load. Please investigate.
"""
    elif kind == "volume_surge":
        subject = f"ObservaBill Alert: Volume Surge Detected"
        body = f"""A volume anomaly has been detected costing ${monthly_cost:.2f}/month.

Pattern/Series: {scrubbed_template}
Monthly Cost: ${monthly_cost:.2f}

Please verify this increase is expected.
"""
    else:
        subject = f"ObservaBill Alert: {kind}"
        body = f"Alert Details:\n{json.dumps(alert, indent=2)}"

    # Add one-click link if exclude_base_url provided
    if exclude_base_url:
        body += f"\n\n---\nView & Exclude: {exclude_base_url}/patterns/{fingerprint_val}"

    return {"subject": subject, "body": body}


def run_watchdog_once(
    scan_fn: Callable,
    state_path: str,
    now_iso: str,
    notify_fn: Optional[Callable] = None,
    exclude_base_url: str = "",
) -> list:
    """
    Run one iteration of the watchdog.

    1. Load state and baselines.
    2. Call scan_fn() to get latest scan_result.
    3. Detect new/surging patterns.
    4. Dedup against sent_ids.
    5. Notify on fresh alerts.
    6. Update baselines and persist state.
    7. Return fired alerts.

    Args:
        scan_fn: Callable that returns {"pattern_leaderboard": [...], "surges": [...], "scan_time": ...}.
        state_path: Path to persist watchdog state (confidential data only, mode 0600).
        now_iso: ISO timestamp for this run.
        notify_fn: Optional callable(formatted_alert) to send notifications.
        exclude_base_url: Optional base URL for alert links.

    Returns:
        List of fired alerts.
    """
    # Load existing state
    state = load_state(state_path)
    baselines = state.get("baselines", {})
    sent_ids = set(state.get("sent_ids", []))

    # Run scan
    scan_result = scan_fn()

    # Detect alerts
    all_alerts = check_for_alerts(scan_result, baselines)

    # Dedup
    fresh_alerts = dedup_alerts(all_alerts, sent_ids)

    # Notify and track
    for alert in fresh_alerts:
        formatted = format_alert(alert, exclude_base_url=exclude_base_url)
        if notify_fn:
            notify_fn(formatted)
        sent_ids.add(alert["alert_id"])

    # Update baselines (includes all patterns from scan, not just new ones)
    baselines = update_baselines(baselines, scan_result, now_iso)

    # Persist state
    new_state = {
        "baselines": baselines,
        "sent_ids": list(sent_ids),
    }
    save_state(state_path, new_state)

    return fresh_alerts
