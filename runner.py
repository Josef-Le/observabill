"""
runner.py — One protection cycle: decide → recommend/alert/auto.

Orchestrates: load_state → extract_findings → decide → notify/remediate → save_state.
Never includes credentials in output, always injectable for testing.

Public functions:
  extract_findings(scan_result) -> list[dict]
  run_cycle(scan_result, policy, creds, state_path, now_iso, email_fn=None, slack_fn=None, writer_fn=None) -> dict
"""

from typing import Callable, Optional
import config
import remediate
import watchdog
import notify


def extract_findings(scan_result: dict) -> list[dict]:
    """
    Flatten scan_result patterns and surges into findings list.

    Deduplicates by (kind, template).

    Args:
        scan_result: dict with "patterns" and "surges" keys

    Returns:
        list of finding dicts, each with: kind, monthly_cost_usd, confidence,
        template, generated_config, id
    """
    findings_map = {}  # (kind, template) -> finding

    # Extract from patterns
    for pattern in scan_result.get("patterns", []):
        kind = pattern.get("recommended_action", "unknown")
        template = pattern.get("template", "")
        cost = pattern.get("monthly_cost_usd", 0.0)
        confidence = pattern.get("confidence", "medium")
        gen_config = pattern.get("generated_config")
        pattern_id = pattern.get("id")

        key = (kind, template)
        if key not in findings_map:
            findings_map[key] = {
                "kind": kind,
                "monthly_cost_usd": cost,
                "confidence": confidence,
                "template": template,
                "generated_config": gen_config,
                "id": pattern_id,
            }

    # Extract from surges
    for surge in scan_result.get("surges", []):
        kind = surge.get("kind", "unknown")
        template = surge.get("template", surge.get("series", ""))
        cost = surge.get("monthly_cost_usd", 0.0)
        confidence = "medium"  # surges default to medium confidence
        gen_config = None

        key = (kind, template)
        if key not in findings_map:
            findings_map[key] = {
                "kind": kind,
                "monthly_cost_usd": cost,
                "confidence": confidence,
                "template": template,
                "generated_config": gen_config,
                "id": None,
            }

    return list(findings_map.values())


def run_cycle(
    scan_result: dict,
    policy: dict,
    creds: dict,
    state_path: str,
    now_iso: str,
    email_fn: Callable = None,
    slack_fn: Callable = None,
    writer_fn: Callable = None,
    reader_fn: Callable = None,
) -> dict:
    """
    Execute one protection cycle.

    1. Load state (sent_ids, baselines, actions_today)
    2. Extract findings from scan_result
    3. For each finding:
       - Decide disposition (recommend/alert/auto)
       - Alert: send notification, dedup on alert_id
       - Auto: remediate.apply_remediation (dry-run safe), track undo
       - Count actions
    4. Persist state with updated baselines, sent_ids, actions_today
    5. Return metrics dict

    Args:
        scan_result: dict with "patterns" and "surges"
        policy: dict from config.default_policy()
        creds: dict with api_key, app_key, site (NEVER persisted)
        state_path: path to persist watchdog state
        now_iso: ISO timestamp for this run
        email_fn: Callable(subject, body, to_addr, smtp_cfg) -> bool (defaults to notify.send_email)
        slack_fn: Callable(webhook_url, text) -> bool (defaults to notify.send_slack)
        writer_fn: Callable(endpoint, verb, payload, api_key, app_key, site) -> dict (defaults to dd_client.write)

    Returns:
        dict with keys: recommended, alerted, applied, auto_dryrun, undo (list), findings_total
    """
    # Defaults
    if email_fn is None:
        email_fn = notify.send_email
    if slack_fn is None:
        slack_fn = notify.send_slack

    # Load state
    state = watchdog.load_state(state_path)
    sent_ids = set(state.get("sent_ids", []))
    baselines = state.get("baselines", {})

    # FIX 4 (HIGH): actions_today resets across days
    # Derive today's date from now_iso (YYYY-MM-DD format)
    today = now_iso[:10]  # Extract YYYY-MM-DD
    state_date = state.get("actions_date")

    if state_date != today:
        # Date changed, reset counter
        actions_today = 0
    else:
        # Same day, load existing counter
        actions_today = state.get("actions_today", 0)

    # Extract findings
    findings = extract_findings(scan_result)

    # Track counts
    counts = {
        "recommended": 0,
        "alerted": 0,
        "applied": 0,
        "auto_dryrun": 0,
        "undo": [],
        "findings_total": len(findings),
    }

    # Process each finding
    for finding in findings:
        disposition = config.decide(finding, policy, actions_today)

        if disposition == "recommend":
            counts["recommended"] += 1

        elif disposition == "alert":
            # Build alert_id to dedup
            alert_id = watchdog.fingerprint(finding.get("kind", "") + "|" + finding.get("template", ""))

            if alert_id not in sent_ids:
                # Format and deliver alert
                alert = notify.format_finding_alert(finding, "alert")
                delivered = notify.deliver(alert, policy.get("channels", {}), email_fn, slack_fn)

                # Track delivery
                if delivered:
                    sent_ids.add(alert_id)
                    counts["alerted"] += 1

        elif disposition == "auto":
            # Dedup ACTUALLY-APPLIED fixes so we don't re-apply/re-notify the same
            # pattern every cycle (a distinct "auto|" namespace from alert ids).
            auto_id = watchdog.fingerprint("auto|" + finding.get("template", ""))
            if auto_id in sent_ids:
                continue  # already remediated this pattern

            res = remediate.apply_remediation(
                finding,
                policy,
                creds,
                actions_today=actions_today,
                now_iso=now_iso,
                writer=writer_fn,
                reader_fn=reader_fn,
                audit_path=state_path + ".audit",
            )

            if res.get("applied"):
                # Successfully applied
                actions_today += 1
                counts["applied"] += 1
                sent_ids.add(auto_id)  # dedup future cycles

                # Track undo record
                if res.get("undo"):
                    counts["undo"].append(res["undo"])

                # Send notification that auto-fix was applied
                alert = notify.format_finding_alert(finding, "auto_applied")
                notify.deliver(alert, policy.get("channels", {}), email_fn, slack_fn)

            elif res.get("dry_run"):
                # Dry-run mode (not actually applied) — do NOT dedup, keep simulating
                counts["auto_dryrun"] += 1

    # Update baselines and persist state (only safe data, no credentials)
    baselines = watchdog.update_baselines(baselines, scan_result, now_iso)

    new_state = {
        "baselines": baselines,
        "sent_ids": list(sent_ids),
        "actions_today": actions_today,
        "actions_date": today,  # FIX 4: persist today's date for next-day reset
    }
    watchdog.save_state(state_path, new_state)

    return counts
