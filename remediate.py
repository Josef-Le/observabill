"""
remediate.py — Auto-remediation executor for ObservaBill.

Applies exclusion/sampling filters to Datadog logs only when:
  - policy enables the action
  - disposition is "auto" (not recommend/alert/ignore)
  - dry_run is False
  - all guardrails pass

Safe-by-default: never writes in tests, never propagates exceptions,
never logs credentials or raw log content.

Public functions:
  _audit_append(audit_path, entry) -> None
  plan_remediation(finding, policy, actions_today=0) -> str
  apply_remediation(finding, policy, creds, actions_today=0, now_iso="", writer=None, audit_path=None) -> dict
  undo(undo_record, creds, writer=None) -> dict
"""

from __future__ import annotations

import json
import os
from typing import Callable, Any

import config
import dd_client


# ===========================================================================
# Audit logging — safe-by-default, never includes credentials or raw content
# ===========================================================================

def _audit_append(audit_path: str, entry: dict) -> None:
    """Append one JSON line to audit_path, creating with mode 0600 if needed.

    Entry must contain ONLY masked/safe fields:
      ts, kind, template (masked), query (masked), monthly_cost_usd,
      disposition, action (applied/skipped/dry_run/error).

    SECURITY: Never include api_key, app_key, or raw log content.

    Parameters
    ----------
    audit_path : str — file path to append to
    entry : dict — JSON-serializable entry with safe fields only
    """
    # Create file with mode 0600 if it doesn't exist
    if not os.path.exists(audit_path):
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)

    # Append JSON line
    with open(audit_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ===========================================================================
# Thin wrapper around config.decide
# ===========================================================================

def plan_remediation(finding: dict, policy: dict, actions_today: int = 0) -> str:
    """Return disposition from config.decide.

    Parameters
    ----------
    finding : dict — contains kind, monthly_cost_usd, confidence
    policy : dict — policy from config.default_policy() or config.load_policy()
    actions_today : int — number of auto actions already executed today

    Returns
    -------
    str — disposition ∈ {"auto", "alert", "recommend", "ignore"}
    """
    return config.decide(finding, policy, actions_today)


# ===========================================================================
# Main remediation executor
# ===========================================================================

def apply_remediation(
    finding: dict,
    policy: dict,
    creds: dict,
    actions_today: int = 0,
    now_iso: str = "",
    writer: Callable | None = None,
    reader_fn: Callable | None = None,
    audit_path: str | None = None,
) -> dict:
    """Apply a remediation filter to Datadog logs if policy allows.

    Behavior:
      1. Compute disposition via config.decide()
      2. If not "auto": return early, audit "skipped", no write
      3. If "auto" but dry_run: return early, audit "dry_run", no write
      4. If "auto" and not dry_run:
         - Call reader_fn to get current index config (READ-MERGE safety)
         - Merge our filter with existing filters (never full-replace)
         - Call writer() with the merged config
         - On success: audit "applied", return undo record
         - On exception: audit "error", return error dict, NEVER propagate

    SECURITY: Never include credentials or raw log content in return dict or audit.

    Parameters
    ----------
    finding : dict
        Contains: kind (str), monthly_cost_usd (float), confidence (str),
                  template (str, masked), generated_config (dict with
                  endpoint/verb/payload), and optionally query (str, masked).
    policy : dict
        From config.default_policy() or config.load_policy().
    creds : dict
        {"api_key": str, "app_key": str, "site": str}.
    actions_today : int
        Number of auto actions already executed today (default 0).
    now_iso : str
        Timestamp for audit entry (e.g. "2026-07-21T12:00:00Z").
    writer : Callable | None
        Function(endpoint, verb, payload, api_key, app_key, site) -> dict.
        Defaults to dd_client.write. Injectable for tests.
    reader_fn : Callable | None
        Function(endpoint, api_key, app_key, site) -> dict (current index config).
        Defaults to dd_client.read. Injectable for tests. CRITICAL for safety:
        allows merging with existing filters instead of full-replace.
    audit_path : str | None
        Path to append audit entries to. If None, no audit logging.

    Returns
    -------
    dict with keys:
      - applied (bool): True if filter was applied to Datadog
      - disposition (str): "auto", "alert", "recommend", or "ignore"
      - reason (str, optional): Why not applied (e.g. "not auto", "cannot_read_current_filters")
      - dry_run (bool, optional): True if in dry-run mode
      - would_apply (dict, optional): The generated_config that would be applied
      - undo (dict, optional): Undo record for reversal {endpoint, filter_name, verb}
      - audit_ts (str, optional): ISO timestamp of audit entry
      - error (str, optional): Exception class name (NOT message)
    """
    if writer is None:
        writer = dd_client.write
    if reader_fn is None:
        reader_fn = dd_client.read

    # Step 1: Get disposition
    disposition = config.decide(finding, policy, actions_today)

    # Step 2: If not "auto", skip and audit
    if disposition != "auto":
        if audit_path:
            _audit_append(audit_path, {
                "ts": now_iso,
                "kind": finding.get("kind", "unknown"),
                "template": finding.get("template", ""),
                "monthly_cost_usd": finding.get("monthly_cost_usd", 0.0),
                "disposition": disposition,
                "action": "skipped",
            })
        return {
            "applied": False,
            "disposition": disposition,
            "reason": "not auto",
        }

    # Step 3: If dry_run, return early
    if policy.get("dry_run", True):
        if audit_path:
            _audit_append(audit_path, {
                "ts": now_iso,
                "kind": finding.get("kind", "unknown"),
                "template": finding.get("template", ""),
                "monthly_cost_usd": finding.get("monthly_cost_usd", 0.0),
                "disposition": disposition,
                "action": "dry_run",
            })
        return {
            "applied": False,
            "dry_run": True,
            "disposition": disposition,
            "would_apply": finding.get("generated_config"),
        }

    # Step 4: Apply the remediation
    gc = finding.get("generated_config", {})

    # FIX 1: Check if generated_config is None or query is empty/whitespace
    if gc is None:
        if audit_path:
            _audit_append(audit_path, {
                "ts": now_iso,
                "kind": finding.get("kind", "unknown"),
                "template": finding.get("template", ""),
                "monthly_cost_usd": finding.get("monthly_cost_usd", 0.0),
                "disposition": disposition,
                "action": "skipped_unsafe",
            })
        return {
            "applied": False,
            "disposition": disposition,
            "reason": "unsafe_empty_query",
        }

    # FIX 1: Extract and validate query
    endpoint = gc.get("endpoint", "")
    verb = gc.get("verb", "PUT")
    payload = gc.get("payload", {})

    # Check if the query in the filter is empty or whitespace-only
    exclusion_filters = payload.get("exclusion_filters", [])
    if exclusion_filters and isinstance(exclusion_filters, list) and len(exclusion_filters) > 0:
        filter_obj = exclusion_filters[0]
        if isinstance(filter_obj, dict):
            query = filter_obj.get("filter", {}).get("query", "")
            if not query or not query.strip():
                if audit_path:
                    _audit_append(audit_path, {
                        "ts": now_iso,
                        "kind": finding.get("kind", "unknown"),
                        "template": finding.get("template", ""),
                        "monthly_cost_usd": finding.get("monthly_cost_usd", 0.0),
                        "disposition": disposition,
                        "action": "skipped_unsafe",
                    })
                return {
                    "applied": False,
                    "disposition": disposition,
                    "reason": "unsafe_empty_query",
                }

    try:
        # Extract filter_name from payload for undo record
        filter_name = ""
        if exclusion_filters and isinstance(exclusion_filters, list):
            if len(exclusion_filters) > 0 and isinstance(exclusion_filters[0], dict):
                filter_name = exclusion_filters[0].get("name", "")

        # FIX 2: READ-MERGE instead of full-replace
        # Get current index config and merge our filter with existing ones
        try:
            current_config = reader_fn(endpoint, creds["api_key"], creds["app_key"], creds["site"])
        except Exception:
            # reader_fn failed -> refuse to write, never blind-overwrite
            if audit_path:
                _audit_append(audit_path, {
                    "ts": now_iso,
                    "kind": finding.get("kind", "unknown"),
                    "template": finding.get("template", ""),
                    "monthly_cost_usd": finding.get("monthly_cost_usd", 0.0),
                    "disposition": disposition,
                    "action": "skipped_unsafe",
                })
            return {
                "applied": False,
                "disposition": disposition,
                "reason": "cannot_read_current_filters",
            }

        if current_config is None:
            # reader_fn returned None -> refuse to write
            if audit_path:
                _audit_append(audit_path, {
                    "ts": now_iso,
                    "kind": finding.get("kind", "unknown"),
                    "template": finding.get("template", ""),
                    "monthly_cost_usd": finding.get("monthly_cost_usd", 0.0),
                    "disposition": disposition,
                    "action": "skipped_unsafe",
                })
            return {
                "applied": False,
                "disposition": disposition,
                "reason": "cannot_read_current_filters",
            }

        # Get existing exclusion filters from current config
        existing_filters = current_config.get("exclusion_filters", [])

        # Check if our filter already exists by name (idempotency)
        our_filter = exclusion_filters[0] if exclusion_filters else {}
        our_filter_name = our_filter.get("name", "")

        filter_already_exists = False
        if our_filter_name:
            for existing in existing_filters:
                if existing.get("name") == our_filter_name:
                    filter_already_exists = True
                    break

        # Build merged payload
        if filter_already_exists:
            # Idempotent: already applied, don't duplicate
            merged_filters = existing_filters
        else:
            # Merge: existing + our new filter
            merged_filters = existing_filters + [our_filter]

        # Prepare merged payload: preserve all fields except exclusion_filters
        merged_payload = dict(current_config)
        merged_payload["exclusion_filters"] = merged_filters

        # Call writer with the FULL merged config
        writer(endpoint, verb, merged_payload, creds["api_key"], creds["app_key"], creds["site"])

        # Success: build undo record
        undo_record = {
            "endpoint": endpoint,
            "filter_name": filter_name,
            "verb": verb,
        }

        # Audit "applied"
        if audit_path:
            _audit_append(audit_path, {
                "ts": now_iso,
                "kind": finding.get("kind", "unknown"),
                "template": finding.get("template", ""),
                "monthly_cost_usd": finding.get("monthly_cost_usd", 0.0),
                "disposition": disposition,
                "action": "applied",
            })

        return {
            "applied": True,
            "disposition": disposition,
            "undo": undo_record,
            "audit_ts": now_iso,
        }

    except Exception as exc:
        # Catch ANY exception, audit, return error class name only
        exc_class_name = exc.__class__.__name__
        if audit_path:
            _audit_append(audit_path, {
                "ts": now_iso,
                "kind": finding.get("kind", "unknown"),
                "template": finding.get("template", ""),
                "monthly_cost_usd": finding.get("monthly_cost_usd", 0.0),
                "disposition": disposition,
                "action": "error",
                "error_type": exc_class_name,
            })

        return {
            "applied": False,
            "disposition": disposition,
            "error": exc_class_name,
        }


# ===========================================================================
# Undo (reversal)
# ===========================================================================

def undo(
    undo_record: dict,
    creds: dict,
    writer: Callable | None = None,
    reader_fn: Callable | None = None,
) -> dict:
    """Best-effort reversal of an applied remediation.

    FIX 3: READ-MERGE to remove ONLY our filter, never wipe all.

    Calls reader_fn to get current filters, builds a new list without our filter,
    then calls writer with the filtered config. Never sends empty filter list.

    Parameters
    ----------
    undo_record : dict
        From apply_remediation() result: {endpoint, filter_name, verb}
    creds : dict
        {"api_key": str, "app_key": str, "site": str}
    writer : Callable | None
        Defaults to dd_client.write
    reader_fn : Callable | None
        Defaults to dd_client.read. CRITICAL for safety: reads current config
        so we can remove ONLY our filter, not all.

    Returns
    -------
    dict with keys:
      - undone (bool): True if reversal succeeded
      - reason (str, optional): Why it failed (e.g. "cannot_read")
      - error (str, optional): Exception class name if it failed
    """
    if writer is None:
        writer = dd_client.write
    if reader_fn is None:
        reader_fn = dd_client.read

    endpoint = undo_record.get("endpoint", "")
    verb = undo_record.get("verb", "PUT")
    filter_name = undo_record.get("filter_name", "")

    try:
        # FIX 3: READ current config to get existing filters
        current_config = reader_fn(endpoint, creds["api_key"], creds["app_key"], creds["site"])

        if current_config is None:
            return {
                "undone": False,
                "reason": "cannot_read",
            }

        # Get existing filters and remove ONLY ours
        existing_filters = current_config.get("exclusion_filters", [])
        remaining_filters = [
            f for f in existing_filters
            if f.get("name") != filter_name
        ]

        # Prepare payload with remaining filters (preserve all other fields)
        payload = dict(current_config)
        payload["exclusion_filters"] = remaining_filters

        # Call writer with the filtered config
        writer(endpoint, verb, payload, creds["api_key"], creds["app_key"], creds["site"])
        return {"undone": True}

    except Exception as exc:
        # Best-effort: never propagate, return error class name
        return {
            "undone": False,
            "reason": "cannot_read",
            "error": exc.__class__.__name__,
        }
