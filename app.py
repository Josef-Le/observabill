#!/usr/bin/env python3
"""
ObservaBill — Free Datadog Bill Breakdown Tool
No external dependencies. Python 3.11 standard library only.
Run: python3 app.py
Serves on: http://localhost:8921
"""

# ── stdlib imports ────────────────────────────────────────────────────────────
import http.server
import urllib.parse
import json
import html
import os
import secrets
import time
import threading
from datetime import date, timedelta

# ── dd_client (read-only cost client; write helper added here) ────────────────
import dd_client

# ── savings scanner + UI renderer + fixtures ─────────────────────────────────
import savings
import ui
import fixtures

# ── config, runner, notify (protection policy + watchdog) ──────────────────────
import config
import runner
import notify

# ── configuration ─────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 8921))

# ── protection paths (configurable via env) ──────────────────────────────────
DATA_DIR = os.environ.get("OBSERVABILL_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
POLICY_PATH = os.path.join(DATA_DIR, "policy.json")
CREDS_PATH = os.path.join(DATA_DIR, "creds.json")
PROTECT_STATE_PATH = os.path.join(DATA_DIR, "watchdog_state.json")
WEBHOOK_SECRET = os.environ.get("OBSERVABILL_WEBHOOK_SECRET", "")
# Admin token for /metrics (funnel analytics). Prefer an env-set value; otherwise
# generate a random per-process token (printed once at startup) so the well-known
# default can't expose funnel/referrer data on a public deploy.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or secrets.token_urlsafe(24)

# ── ephemeral apply-session store (keys never in HTML) ────────────────────────
# Maps token -> {api_key, app_key, site, write_key, ts}
# Entries expire after SESSION_TTL seconds (best-effort cleanup on access).
_apply_sessions: dict = {}
_SESSION_TTL = 1800  # 30 minutes

# FIX 5 (HIGH): Concurrent run_cycle protection
# Prevents scheduler + webhook from running cycles simultaneously (daily-cap bypass + state corruption)
_protection_lock = threading.Lock()

# FIX 6 (HIGH): Webhook DoS rate limit
# Tracks last webhook call timestamp; throttles rapid requests
_last_webhook_ts = [0.0]  # list for mutability

# === PART B: Async Scan Job Infrastructure ===
# Maps job_id -> {stage, pct, done, error, html, ts}
# Keys (api_key, app_key, write_key) are NEVER stored — only passed to _run_scan_job
_scan_jobs: dict = {}
_SCAN_JOB_TTL = 600  # 10 minutes; jobs expire after this


def _create_apply_session(api_key: str, app_key: str, site: str, write_key: str,
                          opportunities: "list | None" = None) -> str:
    """Store keys + this scan's opportunities server-side, return an opaque token.

    Opportunities are stashed here (keyed by id → generated_config) so the /apply
    path can act on REAL scan findings, not just demo fixtures. Content in these
    opportunities is already masked/redacted by the engine.
    """
    # Purge stale entries on every write (no background thread)
    now = time.time()
    expired = [t for t, v in _apply_sessions.items() if now - v["ts"] > _SESSION_TTL]
    for t in expired:
        del _apply_sessions[t]
    token = secrets.token_urlsafe(32)
    _apply_sessions[token] = {
        "api_key": api_key,
        "app_key": app_key,
        "site": site,
        "write_key": write_key,
        "opps": {o.get("id"): o for o in (opportunities or []) if o.get("id")},
        "ts": now,
    }
    return token


def _get_apply_session(token: str) -> "dict | None":
    """Return session dict if token exists and is not expired, else None."""
    entry = _apply_sessions.get(token)
    if entry is None:
        return None
    if time.time() - entry["ts"] > _SESSION_TTL:
        del _apply_sessions[token]
        return None
    return entry


# ── protection: credentials store (keys NEVER rendered) ───────────────────────
def _save_protection_creds(api_key: str, app_key: str, write_key: str, site: str) -> None:
    """Save protection credentials to CREDS_PATH with mode 0600.

    Keys are NEVER rendered in HTML or logged anywhere.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    creds_dict = {
        "api_key": api_key,
        "app_key": app_key,
        "write_key": write_key,
        "site": site,
    }
    # Write with mode 0600 (owner read/write only)
    # Ensure parent dir exists first
    os.makedirs(os.path.dirname(CREDS_PATH) or ".", exist_ok=True)
    fd = os.open(CREDS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(creds_dict, f)
    except Exception:
        os.close(fd)
        raise


def _load_protection_creds() -> "dict | None":
    """Load protection credentials from CREDS_PATH, or None if missing/invalid."""
    if not os.path.exists(CREDS_PATH):
        return None
    try:
        with open(CREDS_PATH, "r") as f:
            creds = json.load(f)
        # Validate required keys
        if all(k in creds for k in ["api_key", "app_key", "site"]):
            return creds
    except Exception:
        pass
    return None


# ── CSS theme (clean light-mode — same palette as SupplementAI) ───────────────
CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f8fafc;
    color: #0f172a;
    min-height: 100vh;
    line-height: 1.6;
}
a { color: #1e40af; text-decoration: none; }
a:hover { text-decoration: underline; }

.nav {
    background: #ffffff;
    border-bottom: 1px solid #e2e8f0;
    padding: 14px 32px;
    display: flex;
    align-items: center;
    gap: 12px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}
.nav-logo {
    font-size: 1.3rem;
    font-weight: 700;
    color: #059669;
    letter-spacing: -0.5px;
}
.nav-tag {
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
    color: #059669;
    font-size: 0.7rem;
    padding: 2px 8px;
    border-radius: 20px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.container { max-width: 960px; margin: 0 auto; padding: 40px 24px; }
.container-wide { max-width: 1100px; margin: 0 auto; padding: 40px 24px; }

h1 { font-size: 2rem; font-weight: 700; margin-bottom: 12px; color: #0f172a; }
h2 { font-size: 1.4rem; font-weight: 600; margin-bottom: 16px; color: #0f172a; }
h3 { font-size: 1.1rem; font-weight: 600; margin-bottom: 8px; color: #0f172a; }

.subtitle {
    color: #475569;
    font-size: 1.05rem;
    margin-bottom: 32px;
    max-width: 680px;
}

.card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 28px;
    margin-bottom: 24px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

input[type="text"], input[type="password"], input[type="email"] {
    width: 100%;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    color: #0f172a;
    font-size: 0.95rem;
    padding: 10px 14px;
    outline: none;
    margin-bottom: 12px;
}
input:focus { border-color: #059669; box-shadow: 0 0 0 3px #05966922; }

select {
    width: 100%;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    color: #0f172a;
    font-size: 0.95rem;
    padding: 10px 14px;
    outline: none;
    margin-bottom: 12px;
    appearance: none;
}
select:focus { border-color: #059669; }

.btn {
    display: inline-block;
    padding: 10px 22px;
    border-radius: 6px;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    border: none;
    transition: all 0.15s;
    text-decoration: none;
}
.btn-primary { background: #059669; color: #fff; }
.btn-primary:hover { background: #047857; text-decoration: none; color: #fff; }
.btn-outline {
    background: transparent;
    color: #059669;
    border: 1px solid #e2e8f0;
}
.btn-outline:hover { background: #f0fdf4; text-decoration: none; }
.btn-lg { padding: 14px 32px; font-size: 1.05rem; }
.btn-sample {
    background: #f1f5f9;
    color: #64748b;
    border: 1px solid #e2e8f0;
    font-size: 0.85rem;
    padding: 8px 16px;
}
.btn-sample:hover { background: #e2e8f0; color: #0f172a; text-decoration: none; }

table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th {
    text-align: left;
    padding: 10px 14px;
    background: #f1f5f9;
    color: #64748b;
    font-weight: 600;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid #e2e8f0;
}
td { padding: 10px 14px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }
tr:last-child td { border-bottom: none; }

.total-box {
    background: #f0fdf4;
    border: 1px solid #059669;
    border-radius: 10px;
    padding: 24px 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 24px 0;
}
.total-label { font-size: 1rem; color: #475569; }
.total-amount { font-size: 2.2rem; font-weight: 700; color: #059669; }

.spike-banner {
    background: #fffbeb;
    border: 1px solid #fbbf24;
    border-left: 4px solid #f59e0b;
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 20px;
    color: #78350f;
    font-weight: 600;
}

.upsell-card {
    background: linear-gradient(135deg, #f0fdf4, #ecfdf5);
    border: 2px solid #059669;
    border-radius: 10px;
    padding: 24px 28px;
    margin: 24px 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
}
.upsell-text h3 { color: #047857; font-size: 1.1rem; margin-bottom: 6px; }
.upsell-text p { color: #475569; font-size: 0.9rem; }

.alert-error {
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 6px;
    padding: 14px 18px;
    color: #991b1b;
    font-size: 0.92rem;
    margin-bottom: 20px;
}
.alert-info {
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    border-radius: 6px;
    padding: 12px 16px;
    color: #475569;
    font-size: 0.88rem;
    margin-bottom: 20px;
}

.hint {
    color: #64748b;
    font-size: 0.82rem;
    margin-top: 8px;
}
.section-label {
    text-transform: uppercase;
    font-size: 0.75rem;
    letter-spacing: 1px;
    color: #64748b;
    font-weight: 600;
    margin-bottom: 12px;
}
.divider { border: none; border-top: 1px solid #e2e8f0; margin: 28px 0; }

.trust-block {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 18px 20px;
    margin-bottom: 24px;
    font-size: 0.88rem;
    color: #475569;
    line-height: 1.7;
}

.badge-safe {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
    border-radius: 20px;
    padding: 3px 12px;
    font-size: 0.78rem;
    font-weight: 600;
    color: #059669;
    margin-right: 6px;
    margin-bottom: 4px;
}

.form-label {
    display: block;
    font-size: 0.85rem;
    font-weight: 600;
    color: #374151;
    margin-bottom: 4px;
}
.form-group { margin-bottom: 16px; }

.on-demand-flag {
    display: inline-block;
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 4px;
    padding: 1px 7px;
    font-size: 0.78rem;
    font-weight: 600;
    color: #92400e;
}

.tag-table { margin-top: 16px; }

@media (max-width: 720px) {
    .upsell-card { flex-direction: column; }
    .total-box { flex-direction: column; gap: 8px; }
}
"""

# ── helper: HTML shell wrapper ─────────────────────────────────────────────────
def html_page(title, body, extra_head=""):
    favicon = (
        "data:image/svg+xml,"
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        "<rect width='32' height='32' rx='6' fill='%23059669'/>"
        "<text y='24' x='4' font-size='20' font-family='sans-serif' fill='white' font-weight='bold'>$&#9660;</text>"
        "</svg>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="ObservaBill: Free read-only Datadog bill breakdown. Paste your API key and see exactly which product is burning your budget.">
<title>{html.escape(title)} — ObservaBill</title>
<link rel="icon" href="{favicon}">
<style>{CSS}</style>
{extra_head}
</head>
<body>
<nav class="nav">
    <a href="/" style="text-decoration:none;" class="nav-logo">ObservaBill</a>
    <span class="nav-tag">Free</span>
</nav>
{body}
<footer style="margin-top:40px;padding:16px 0;border-top:1px solid #e2e8f0;text-align:center;font-size:0.8rem;color:#6b7280;">
  &copy; 2026 ObservaBill &middot;
  <a href="/" style="color:#6b7280;margin:0 8px;">Home</a>
  &middot;
  <a href="/pricing" style="color:#6b7280;margin:0 8px;">Pricing</a>
  &middot;
  <a href="/privacy" style="color:#6b7280;margin:0 8px;">Privacy</a>
  &middot;
  <a href="/terms" style="color:#6b7280;margin:0 8px;">Terms</a>
  &middot;
  <a href="/sample" style="color:#6b7280;margin:0 8px;">Sample</a>
  &middot;
  <a href="/reserve" style="color:#6b7280;margin:0 8px;">Get Alerts</a>
</footer>
</body>
</html>"""


# ── helper: format currency ────────────────────────────────────────────────────
def fmt_usd(v):
    if v < 0:
        return f"-${abs(v):,.0f}"
    return f"${v:,.0f}"


# FIX 7 (MED): Safe numeric parsing helpers
def _safe_float(v, default):
    """Parse v as float, return default on ValueError/TypeError."""
    try:
        return float(v) if v else default
    except (ValueError, TypeError):
        return default


def _safe_int(v, default):
    """Parse v as int, return default on ValueError/TypeError."""
    try:
        return int(v) if v else default
    except (ValueError, TypeError):
        return default


# ── helper: persistent store ───────────────────────────────────────────────────
def append_to_store(filename, data):
    """Append JSON line to /var/data/{filename} if disk mounted, else stdout."""
    line = json.dumps(data)
    path = f"/var/data/{filename}"
    try:
        os.makedirs("/var/data", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        print(f"[STORE:{filename}] {line}")


# ── funnel instrumentation ─────────────────────────────────────────────────────
def log_event(event, ref="", **fields):
    """Record one funnel step."""
    rec = {"event": event}
    if ref:
        rec["ref"] = ref
    rec.update(fields)
    append_to_store("funnel.txt", rec)
    print(f"[FUNNEL] {event} ref={ref!r} {fields}")


def read_funnel():
    """Aggregate funnel.txt → (event_counts, ref_counts)."""
    counts, refs = {}, {}
    try:
        with open("/var/data/funnel.txt", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ev = rec.get("event", "?")
                counts[ev] = counts.get(ev, 0) + 1
                r = rec.get("ref")
                if r:
                    refs[r] = refs.get(r, 0) + 1
    except FileNotFoundError:
        pass
    return counts, refs


# ── built-in sample summary ────────────────────────────────────────────────────
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
    {"tags": {"team": ["platform"]}, "costs": {"infra_host_total_cost": 3800.0}},
    {"tags": {"team": ["backend"]}, "costs": {"infra_host_total_cost": 2800.0}},
]


# ── reserve upsell block ───────────────────────────────────────────────────────
def _reserve_upsell_html():
    return """
<div class="upsell-card">
  <div class="upsell-text">
    <h3>Want weekly alerts when a deploy spikes your bill?</h3>
    <p>$99/mo — Slack or email alerts when costs jump + a monthly breakdown report. Reserve a spot now.</p>
  </div>
  <div style="flex-shrink:0;">
    <a href="/reserve" class="btn btn-primary">Reserve $99/mo →</a>
  </div>
</div>"""


# ── render_breakdown: shared renderer for /analyze and /sample ─────────────────
def render_breakdown(summary, tag_attr):
    """
    Render the cost breakdown HTML fragment.

    Parameters
    ----------
    summary  : dict from dd_client.summarize()
    tag_attr : list (configured) | dict ({"configured": False, "rows": []}) | None
    """
    total = summary["total"]
    by_product = sorted(summary["by_product"], key=lambda x: x["total"], reverse=True)
    projected = summary["projected_end_of_month"]
    prev = summary["prev_month_total"]
    delta_pct = summary["delta_pct"]
    spike = summary["spike"]
    on_demand_overage = summary["on_demand_overage"]

    # Spike warning banner
    spike_html = ""
    if spike:
        spike_html = f"""
<div class="spike-banner">
  ⚠ Spike detected — costs are up {delta_pct:+.1f}% vs last month
  (on-demand overage: {fmt_usd(on_demand_overage)}).
  A recent deploy or usage change may be driving this.
</div>"""

    # On-demand overage flag in summary
    overage_flag = ""
    if on_demand_overage > 0:
        overage_flag = f' <span class="on-demand-flag">⚠ on-demand: {fmt_usd(on_demand_overage)}</span>'

    # By-product table rows
    rows = ""
    for p in by_product:
        name = html.escape(p["product_name"])
        committed = fmt_usd(p["committed"])
        od = fmt_usd(p["on_demand"])
        tot = fmt_usd(p["total"])
        od_flag = ' <span class="on-demand-flag">on-demand</span>' if p["on_demand"] > 0 else ""
        rows += f"""
<tr>
  <td><strong>{name}</strong></td>
  <td>{committed}</td>
  <td>{od}{od_flag}</td>
  <td><strong>{tot}</strong></td>
</tr>"""

    # Tag attribution block
    if tag_attr is None or (isinstance(tag_attr, dict) and not tag_attr.get("configured", True)):
        tag_html = """
<div class="alert-info" style="margin-top:20px;">
  <strong>Enable Usage Attribution</strong> for a per-team / per-service cost breakdown.
  Go to <a href="https://docs.datadoghq.com/account_management/billing/usage_attribution/"
  target="_blank" rel="noopener">Datadog → Usage Attribution</a> to configure tag keys
  (e.g. <code>team</code>, <code>service</code>). Usage attribution is not configured for this org.
</div>"""
    else:
        tag_rows = ""
        if isinstance(tag_attr, list):
            for row in tag_attr:
                tags = row.get("tags") or {}
                tag_str = ", ".join(
                    f"{html.escape(k)}: {html.escape(', '.join(v) if isinstance(v, list) else str(v))}"
                    for k, v in tags.items()
                )
                costs = row.get("costs", {})
                cost_str = ", ".join(f"{html.escape(k)}: {fmt_usd(v)}" for k, v in costs.items())
                tag_rows += f"<tr><td>{tag_str}</td><td>{cost_str}</td></tr>"
        tag_html = f"""
<h3 style="margin-top:24px; margin-bottom:12px;">Cost by Team / Service (Usage Attribution)</h3>
<div class="card" style="padding:0; overflow:hidden; margin-bottom:0;">
<table class="tag-table">
  <thead><tr><th>Tags</th><th>Costs</th></tr></thead>
  <tbody>{tag_rows}</tbody>
</table>
</div>"""

    # Delta vs last month
    delta_sign = "+" if delta_pct >= 0 else ""
    delta_color = "#dc2626" if delta_pct > 10 else "#059669"

    return f"""
{spike_html}

<div class="total-box">
  <div>
    <div class="total-label">Estimated Cost This Month{overage_flag}</div>
    <div style="color:#475569; font-size:0.85rem; margin-top:4px;">
      Projected end-of-month: <strong>{fmt_usd(projected)}</strong>
      &nbsp;·&nbsp;
      Last month: <strong>{fmt_usd(prev)}</strong>
      &nbsp;·&nbsp;
      <span style="color:{delta_color}; font-weight:700;">{delta_sign}{delta_pct:.1f}% vs last month</span>
    </div>
  </div>
  <div class="total-amount">{fmt_usd(total)}</div>
</div>

<div class="card" style="padding:0; overflow:hidden; margin-bottom:24px;">
<table>
  <thead>
    <tr>
      <th>Product</th>
      <th>Committed</th>
      <th>On-Demand</th>
      <th>Total</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
</div>

{tag_html}

{_reserve_upsell_html()}"""


# ── page: GET / ────────────────────────────────────────────────────────────────
def page_landing():
    body = """
<div style="background: linear-gradient(to bottom, #ecfdf5, #f8fafc); padding: 48px 24px 40px;">
  <div style="max-width:860px; margin:0 auto;">
    <h1 style="font-size:2.1rem; line-height:1.25; margin-bottom:14px;">
      Cut your Datadog log bill — find the exact lines burning money
    </h1>
    <p class="subtitle">
      ObservaBill samples your live logs, mines the repeated patterns, and shows you which specific log lines to drop, sample, or convert — with the exact query and $/mo. Read-only. Your keys are never stored.
    </p>

    <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:10px; padding:24px; margin-bottom:32px;">
      <h2 style="font-size:1.2rem; margin-bottom:16px; color:#047857;">How it works</h2>
      <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:16px;">
        <div style="background:#fff; border:1px solid #d1fae5; border-radius:8px; padding:16px; text-align:center;">
          <div style="font-size:1.8rem; font-weight:700; color:#059669; margin-bottom:8px;">1</div>
          <p style="font-size:0.9rem; color:#475569;">Paste read-only Datadog keys</p>
        </div>
        <div style="background:#fff; border:1px solid #d1fae5; border-radius:8px; padding:16px; text-align:center;">
          <div style="font-size:1.8rem; font-weight:700; color:#059669; margin-bottom:8px;">2</div>
          <p style="font-size:0.9rem; color:#475569;">We sample + mine your log patterns</p>
        </div>
        <div style="background:#fff; border:1px solid #d1fae5; border-radius:8px; padding:16px; text-align:center;">
          <div style="font-size:1.8rem; font-weight:700; color:#059669; margin-bottom:8px;">3</div>
          <p style="font-size:0.9rem; color:#475569;">Get ranked fixes with exact $ + one-click apply</p>
        </div>
      </div>
    </div>

    <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:24px; margin-bottom:32px;">
      <h2 style="font-size:1.2rem; margin-bottom:8px; color:#1e40af;">Privacy-first</h2>
      <p style="color:#475569; font-size:0.95rem; margin-bottom:8px;">We mine masked templates, never store your raw logs. Read-only by default. Keys live in memory only, never written to disk.</p>
    </div>

    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:24px; margin-bottom:32px;">
      <h2 style="font-size:1.2rem; margin-bottom:16px; color:#0f172a;">Pricing</h2>
      <div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px;">
        <div style="background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:16px;">
          <h3 style="color:#059669; margin-bottom:8px;">Free scan</h3>
          <p style="font-size:0.9rem; color:#475569;">One-time scan, read-only, all findings</p>
        </div>
        <div style="background:#fff; border:2px solid #059669; border-radius:8px; padding:16px;">
          <h3 style="color:#059669; margin-bottom:8px;">Protection — $99/mo</h3>
          <p style="font-size:0.9rem; color:#475569;">Continuous hourly watchdog, alerts, one-click remediation</p>
          <a href="/pricing" style="color:#059669; font-size:0.85rem; text-decoration:none; font-weight:600;">Learn more →</a>
        </div>
      </div>
    </div>

    <div class="trust-block">
      <span class="badge-safe">✓ Read-only by default</span>
      <span class="badge-safe">✓ Keys never stored</span>
      <span class="badge-safe">✓ No account needed</span>
      <br>
      <strong>Read-only by default.</strong> Keys used for this request only, never stored.
      ObservaBill requires <code>usage_read</code> + <code>billing_read</code> scopes only.
      Your API key and Application key are never written to disk, logged, or stored in any way.
      <br><br>
      <strong>How to create a read-only key:</strong> In Datadog, go to
      <a href="https://app.datadoghq.com/organization-settings/api-keys" target="_blank" rel="noopener">
      Organization Settings → API Keys</a> → New Key. For the Application Key, go to
      <a href="https://app.datadoghq.com/organization-settings/application-keys" target="_blank" rel="noopener">
      Application Keys</a> → New Key and scope it to <code>usage_read billing_read</code>.
    </div>

    <div class="card">
      <div class="section-label">Scan your Datadog for wasted spend</div>
      <form action="/scan" method="POST">
        <div class="form-group">
          <label class="form-label" for="api_key">Datadog API Key</label>
          <input type="password" id="api_key" name="api_key" placeholder="Your Datadog API key"
                 autocomplete="off" spellcheck="false" required>
        </div>
        <div class="form-group">
          <label class="form-label" for="app_key">Application Key</label>
          <input type="password" id="app_key" name="app_key" placeholder="Your Datadog Application key"
                 autocomplete="off" spellcheck="false" required>
        </div>
        <div class="form-group">
          <label class="form-label" for="site">Datadog Site</label>
          <select id="site" name="site">
            <option value="us1">US1 — api.datadoghq.com (default)</option>
            <option value="us3">US3 — api.us3.datadoghq.com</option>
            <option value="us5">US5 — api.us5.datadoghq.com</option>
            <option value="eu">EU — api.datadoghq.eu</option>
            <option value="ap1">AP1 — api.ap1.datadoghq.com</option>
            <option value="ap2">AP2 — api.ap2.datadoghq.com</option>
            <option value="uk1">UK1 — api.uk1.datadoghq.com</option>
          </select>
        </div>

        <details style="margin-bottom:16px;">
          <summary style="cursor:pointer; font-size:0.85rem; color:#64748b; font-weight:600; user-select:none;">
            Advanced: write key (enables one-click Apply)
          </summary>
          <div style="margin-top:10px; padding:14px 16px; background:#fffbeb; border:1px solid #fde68a; border-radius:8px;">
            <div class="alert-info" style="margin-bottom:10px; background:#fef3c7; border-color:#fde68a; color:#92400e;">
              ⚠ Warning: a write key grants Datadog write access. This allows ObservaBill to apply
              remediations on your behalf via the Apply button. Use a scoped key with only the
              permissions you intend to grant.
            </div>
            <label class="form-label" for="write_key">Write Key (optional)</label>
            <input type="password" id="write_key" name="write_key"
                   placeholder="Datadog write-capable Application key (optional)"
                   autocomplete="off" spellcheck="false">
            <p class="hint">Leave blank for read-only mode. The write key is used only for Apply actions, never stored.</p>
          </div>
        </details>

        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
          <button type="submit" class="btn btn-primary btn-lg">Scan for wasted spend →</button>
          <a href="/sample" class="btn btn-sample">Try with sample data</a>
        </div>
      </form>
    </div>

    <div class="upsell-card" style="margin-top:0;">
      <div class="upsell-text">
        <h3>Want weekly Slack/email alerts when a deploy spikes your bill?</h3>
        <p>$99/mo — get notified before the overage compounds + a monthly report. Reserve a spot →</p>
      </div>
      <div style="flex-shrink:0;">
        <a href="/reserve" class="btn btn-primary">Reserve $99/mo →</a>
      </div>
    </div>

    <p style="margin-top:16px; font-size:0.82rem; color:#64748b;">
      Want the raw bill by product?
      <a href="/breakdown" style="color:#059669;">See bill breakdown →</a>
    </p>
  </div>
</div>"""
    return html_page("Find Wasted Spend in Datadog — Free", body)


# ── PART B: Async Scan Job Functions ──────────────────────────────────────────

def _run_scan_job(job_id, api_key, app_key, site, write_key):
    """Run savings.scan in background; update _scan_jobs[job_id] with results.

    SECURITY: api_key, app_key, write_key are LOCAL params only — never stored
    in _scan_jobs. Keys are consumed here for the scan and apply-token creation only.

    Parameters
    ----------
    job_id    : unique job identifier (generated in page_scan)
    api_key   : Datadog API key (never stored)
    app_key   : Datadog Application key (never stored)
    site      : Datadog site (e.g. "us1")
    write_key : optional Datadog write key (never stored)
    """
    try:
        # Run the scan with progress callback
        def progress_cb(stage, pct):
            if job_id in _scan_jobs:
                _scan_jobs[job_id]["stage"] = stage
                _scan_jobs[job_id]["pct"] = pct

        scan_result = savings.scan(api_key, app_key, site, progress_cb=progress_cb)

        # Create apply token if write_key provided
        apply_token = ""
        if write_key:
            apply_token = _create_apply_session(
                api_key, app_key, site, write_key,
                opportunities=scan_result.get("opportunities", []),
            )

        # Render dashboard
        dashboard_html = ui.render_dashboard(
            scan_result, write_enabled=bool(write_key), apply_token=apply_token
        )
        body = f"""
<div style="margin-top:16px; padding:0 12px;">
  <div style="max-width:1100px; margin:0 auto; display:flex; align-items:center;
              justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:8px;">
    <p style="color:#475569; font-size:0.9rem;">
      Site: <strong>{html.escape(site)}</strong> &nbsp;·&nbsp; Read-only — keys not stored.
      &nbsp;<a href="/breakdown" style="color:#059669; font-size:0.85rem;">See raw bill →</a>
    </p>
    <a href="/" class="btn btn-outline" style="font-size:0.85rem; padding:7px 16px;">← New Scan</a>
  </div>
  {dashboard_html}
</div>"""
        extra_head = f"<style>{ui.DASHBOARD_CSS}</style>"
        html_result = html_page("Savings Scan", body, extra_head=extra_head)

        # Mark job complete with HTML
        if job_id in _scan_jobs:
            _scan_jobs[job_id]["html"] = html_result
            _scan_jobs[job_id]["done"] = True
            _scan_jobs[job_id]["pct"] = 100
            _scan_jobs[job_id]["stage"] = "Done"

    except dd_client.AuthError:
        msg = "Authentication Failed — those keys were rejected. Check they're valid and have usage_read + billing_read scopes."
        if job_id in _scan_jobs:
            _scan_jobs[job_id]["error"] = msg
            _scan_jobs[job_id]["done"] = True

    except dd_client.PermissionError:
        msg = "Permission Denied — the app key is missing required permissions. Create a new Application Key with usage_read and billing_read scopes."
        if job_id in _scan_jobs:
            _scan_jobs[job_id]["error"] = msg
            _scan_jobs[job_id]["done"] = True

    except dd_client.RateLimitError:
        msg = "Rate Limited — Datadog rate-limited this request. Try again in a minute."
        if job_id in _scan_jobs:
            _scan_jobs[job_id]["error"] = msg
            _scan_jobs[job_id]["done"] = True

    except dd_client.DatadogError:
        msg = "Couldn't Reach Datadog — check your network and try again."
        if job_id in _scan_jobs:
            _scan_jobs[job_id]["error"] = msg
            _scan_jobs[job_id]["done"] = True

    except Exception:
        msg = "Unexpected Error — something went wrong. Please try again."
        if job_id in _scan_jobs:
            _scan_jobs[job_id]["error"] = msg
            _scan_jobs[job_id]["done"] = True


def page_scan_status(job_id):
    """GET /scan/status?id=<job_id> — return JSON with stage, pct, done, error.

    SECURITY: never includes keys. If job missing or expired, returns
    {error: "expired", done: true}.
    """
    if job_id not in _scan_jobs:
        return (json.dumps({"error": "expired", "done": True}), 200)

    job = _scan_jobs[job_id]
    now = time.time()

    # Purge expired jobs (last check before returning)
    if now - job.get("ts", 0) > _SCAN_JOB_TTL:
        del _scan_jobs[job_id]
        return (json.dumps({"error": "expired", "done": True}), 200)

    # Return only stage, pct, done, error — NEVER keys
    return (
        json.dumps({
            "stage": job.get("stage", ""),
            "pct": job.get("pct", 0),
            "done": job.get("done", False),
            "error": job.get("error"),  # None if no error
        }),
        200,
    )


def page_scan_result(job_id):
    """GET /scan/result?id=<job_id> — return completed dashboard HTML or error page.

    SECURITY: never includes keys. If not done, redirect to progress page.
    """
    if job_id not in _scan_jobs:
        return _error_page("Session Expired", "Job not found. Please re-run the scan.")

    job = _scan_jobs[job_id]
    now = time.time()

    # Check expiry
    if now - job.get("ts", 0) > _SCAN_JOB_TTL:
        del _scan_jobs[job_id]
        return _error_page("Session Expired", "Job expired. Please re-run the scan.")

    # If still running, show a redirect page
    if not job.get("done", False):
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="1; url=/scan/progress?id={html.escape(job_id)}"></head>
<body>Loading...</body></html>"""

    # If error, show error page
    if job.get("error"):
        return _error_page("Scan Failed", job["error"])

    # Return the rendered dashboard HTML
    return job.get("html", "")


# ── page: POST /scan ──────────────────────────────────────────────────────────
def page_scan(api_key, app_key, site, write_key=None):
    """
    POST /scan: Start an async scan job. Return a progress page that polls status.

    SECURITY: api_key, app_key, and write_key are NEVER logged, stored, or echoed.
    Keys are passed directly to _run_scan_job (which is local params) and never
    appear in _scan_jobs dict or HTML.
    """
    log_event("scan", site=site)  # NEVER log any key

    # Purge expired jobs (cleanup before creating new one)
    now = time.time()
    expired = [jid for jid, j in _scan_jobs.items() if now - j.get("ts", 0) > _SCAN_JOB_TTL]
    for jid in expired:
        del _scan_jobs[jid]

    # Create job ID and initialize job record
    job_id = secrets.token_urlsafe(16)
    _scan_jobs[job_id] = {
        "stage": "Starting…",
        "pct": 0,
        "done": False,
        "error": None,
        "html": None,
        "ts": now,
    }

    # Start async scan in background thread
    t = threading.Thread(
        target=_run_scan_job,
        args=(job_id, api_key, app_key, site, write_key or ""),
        daemon=True,
    )
    t.start()

    # Return progress page with polling JS
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scanning — ObservaBill</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f8fafc;
    color: #0f172a;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
}}
.progress-container {{
    max-width: 500px;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 40px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    text-align: center;
}}
h1 {{
    font-size: 1.5rem;
    font-weight: 700;
    margin-bottom: 12px;
    color: #0f172a;
}}
#stage {{
    color: #475569;
    font-size: 1rem;
    margin-bottom: 24px;
    min-height: 24px;
}}
.progress-bar {{
    width: 100%;
    height: 8px;
    background: #e2e8f0;
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 16px;
}}
.progress-fill {{
    height: 100%;
    background: #059669;
    width: 0%;
    transition: width 0.3s ease;
}}
#pct {{
    color: #64748b;
    font-size: 0.9rem;
}}
</style>
</head>
<body>
<div class="progress-container">
  <h1>Scanning Your Datadog…</h1>
  <div id="stage">Starting…</div>
  <div class="progress-bar">
    <div class="progress-fill" id="fill"></div>
  </div>
  <div id="pct">0%</div>
</div>
<script>
const jobId = {json.dumps(job_id)};
const pollIntervalMs = 1200;

async function pollStatus() {{
    try {{
        const resp = await fetch('/scan/status?id=' + encodeURIComponent(jobId));
        const data = await resp.json();

        document.getElementById('stage').textContent = data.stage || '';
        document.getElementById('pct').textContent = Math.round(data.pct) + '%';
        document.getElementById('fill').style.width = Math.min(100, data.pct) + '%';

        if (data.done) {{
            if (data.error) {{
                alert('Error: ' + data.error);
                window.location = '/';
            }} else {{
                window.location = '/scan/result?id=' + encodeURIComponent(jobId);
            }}
        }} else {{
            setTimeout(pollStatus, pollIntervalMs);
        }}
    }} catch (e) {{
        console.error('Poll failed:', e);
        setTimeout(pollStatus, pollIntervalMs);
    }}
}}

pollStatus();
</script>
</body>
</html>"""


# ── page: POST /apply ─────────────────────────────────────────────────────────
def page_apply(opp_id, apply_token, confirm):
    """
    Gated write action. Requires a valid apply_token (issued by POST /scan) and confirm=True.

    SECURITY: api_key, app_key, write_key are NEVER accepted from the client form,
    NEVER echoed in HTML, NEVER logged. They are retrieved exclusively from the
    server-side _apply_sessions store keyed by apply_token.
    """
    # Resolve session — keys stay server-side
    session = _get_apply_session(apply_token) if apply_token else None

    if session is None:
        # No token / expired / bogus — friendly message, zero writes
        body = """
<div class="container" style="max-width:560px;">
  <div style="margin-top:64px;">
    <div class="alert-error">
      <h2 style="color:#991b1b; margin-bottom:8px;">Session Expired</h2>
      <p>Your session expired — re-run the scan to apply this fix.</p>
    </div>
    <a href="/" class="btn btn-outline">← Back to Scan</a>
  </div>
</div>"""
        return html_page("Session Expired", body), 200

    api_key = session["api_key"]
    app_key = session["app_key"]
    site = session["site"]
    write_key = session["write_key"]

    # Resolve opportunity server-side from THIS session's real scan first,
    # then fall back to demo fixtures (never trust client payload for the config).
    opp = _find_opp_by_id(opp_id, session=session)
    if opp is None:
        return _error_page(
            "Opportunity Not Found",
            "Could not find the opportunity to apply. Please re-run the scan.",
        ), 200

    if not confirm:
        # Show confirmation step — embed ONLY opp_id + apply_token, NO keys
        gc = opp.get("generated_config", {})
        endpoint = html.escape(gc.get("endpoint", ""))
        verb = html.escape(gc.get("verb", ""))
        title = html.escape(opp.get("title", opp_id))
        body = f"""
<div class="container" style="max-width:560px;">
  <div style="margin-top:48px;">
    <h1>Confirm Apply</h1>
    <p class="subtitle" style="margin-bottom:20px;">
      You are about to apply: <strong>{title}</strong>
    </p>
    <div class="card" style="background:#fffbeb; border-color:#fde68a;">
      <p style="font-size:0.9rem; color:#78350f;">
        This will execute <code>{verb} {endpoint}</code> against your Datadog account
        using the write key you provided. This action cannot be undone automatically.
      </p>
    </div>
    <form method="POST" action="/apply" style="margin-top:20px;">
      <input type="hidden" name="opp_id" value="{html.escape(opp_id)}">
      <input type="hidden" name="apply_token" value="{html.escape(apply_token)}">
      <input type="hidden" name="confirm" value="1">
      <div style="display:flex; gap:10px;">
        <button type="submit" class="btn btn-primary">Apply to Datadog →</button>
        <a href="/" class="btn btn-outline">Cancel</a>
      </div>
    </form>
  </div>
</div>"""
        return html_page("Confirm Apply", body), 200

    # Execute the write using server-side keys from the session
    gc = savings.build_apply_request(opp)
    title = html.escape(opp.get("title", opp_id))

    # log apply event — lever only, NEVER any key
    log_event("apply", lever=opp.get("lever", opp_id))

    try:
        dd_client.write(gc["endpoint"], gc["verb"], gc["payload"], api_key, app_key, site)
        body = f"""
<div class="container" style="max-width:560px;">
  <div style="margin-top:64px; text-align:center;">
    <div style="font-size:3rem; margin-bottom:16px;">✅</div>
    <h1>Applied</h1>
    <p class="subtitle" style="margin:0 auto;">
      <strong>{title}</strong> was applied to your Datadog account ({html.escape(site)}).
      Changes may take a few minutes to take effect.
    </p>
    <a href="/" class="btn btn-outline" style="margin-top:24px;">← Back to Home</a>
  </div>
</div>"""
        return html_page("Applied", body), 200

    except dd_client.AuthError:
        return _error_page(
            "Write Authentication Failed",
            "The write key was rejected — check it has write permissions.",
        ), 200
    except dd_client.DatadogError as exc:
        print(f"[apply-error] {type(exc).__name__}: {exc}")
        return _error_page(
            "Apply Failed",
            "Couldn't apply the change. Datadog returned an error — check the write key permissions.",
        ), 200
    except Exception as exc:
        print(f"[apply-error] {type(exc).__name__}: {exc}")
        return _error_page(
            "Apply Failed",
            "Something went wrong during apply — please try again.",
        ), 200


# ── page: POST /analyze (now at /breakdown too) ───────────────────────────────
def page_analyze(api_key, app_key, site):
    """
    Call dd_client, render breakdown.
    SECURITY: api_key and app_key are NEVER passed to log_event, append_to_store,
    or echoed in the HTML response.
    """
    # log the analyze event — site only, NEVER keys
    log_event("analyze", site=site)

    try:
        estimated = dd_client.get_estimated_cost(api_key, app_key, site)
        projected = dd_client.get_projected_cost(api_key, app_key, site)
        # Previous month = first day of last month
        today = date.today()
        first_this_month = today.replace(day=1)
        first_prev_month = (first_this_month - timedelta(days=1)).replace(day=1)
        prev_historical = dd_client.get_historical_cost(
            api_key, app_key, site,
            start_month=first_prev_month.strftime("%Y-%m-%d"),
            end_month=first_this_month.strftime("%Y-%m-%d"),
        )
        summary = dd_client.summarize(estimated, projected, prev_historical)

        # Best-effort: usage attribution
        try:
            attr_raw = dd_client.get_monthly_cost_attribution(
                api_key, app_key, site,
                start_month=first_prev_month.strftime("%Y-%m"),
            )
            tag_attr = dd_client.parse_tag_attribution(
                attr_raw,
                dims=["infra_host_total_cost", "apm_host_total_cost", "logs_total_cost"],
            )
        except Exception:
            tag_attr = None

        breakdown_html = render_breakdown(summary, tag_attr)
        body = f"""
<div class="container-wide">
  <div style="margin-top:40px; margin-bottom:24px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;">
    <div>
      <h1>Your Datadog Bill Breakdown</h1>
      <p style="color:#475569;">Site: <strong>{html.escape(site)}</strong> &nbsp;·&nbsp; Read-only — keys not stored.</p>
    </div>
    <a href="/" class="btn btn-outline">← New Analysis</a>
  </div>
  {breakdown_html}
</div>"""
        return html_page("Bill Breakdown", body)

    except dd_client.AuthError:
        return _error_page(
            "Authentication Failed",
            "Those keys were rejected — check they're valid and have "
            "<code>usage_read</code> + <code>billing_read</code> scopes.",
        )
    except dd_client.PermissionError:
        return _error_page(
            "Permission Denied",
            "The app key is missing <code>usage_read</code> or <code>billing_read</code> permission. "
            "Create a new Application Key with those scopes in Datadog.",
        )
    except dd_client.RateLimitError:
        return _error_page(
            "Rate Limited",
            "Datadog rate-limited this request — try again in a minute.",
        )
    except dd_client.DatadogError as exc:
        # NEVER render exception content to the user (structural anti-key-leak guard).
        # Detail goes to the server log only; dd_client guarantees keys aren't in messages.
        print(f"[analyze-error] {type(exc).__name__}: {exc}")
        return _error_page(
            "Couldn't Reach Datadog",
            "Couldn't reach Datadog. Check your network and try again.",
        )
    except Exception as exc:
        print(f"[analyze-error] {type(exc).__name__}: {exc}")
        return _error_page(
            "Unexpected Error",
            "Something went wrong — please try again.",
        )


def _error_page(title, message):
    body = f"""
<div class="container" style="max-width:640px;">
  <div style="margin-top:64px;">
    <div class="alert-error">
      <h2 style="color:#991b1b; margin-bottom:8px;">{html.escape(title)}</h2>
      <p>{message}</p>
    </div>
    <a href="/" class="btn btn-outline">← Try again</a>
  </div>
</div>"""
    return html_page(title, body)


# ── page: GET /sample ──────────────────────────────────────────────────────────
def page_sample():
    """Render the savings dashboard with fixtures.SAMPLE_SCAN (no API key required)."""
    log_event("sample_view")
    dashboard_html = ui.render_dashboard(fixtures.SAMPLE_SCAN, write_enabled=False)
    body = f"""
<div style="margin-top:16px; padding:0 12px;">
  <div style="max-width:1100px; margin:0 auto; display:flex; align-items:center;
              justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:8px;">
    <p style="color:#475569; font-size:0.9rem;">
      Sample data — realistic Datadog org with $3,500/mo in recoverable waste.
      <a href="/" style="color:#059669;">Scan your own account →</a>
    </p>
    <a href="/" class="btn btn-outline" style="font-size:0.85rem; padding:7px 16px;">← Scan My Account</a>
  </div>
  {dashboard_html}
  {_reserve_upsell_html()}
</div>"""
    extra_head = f"<style>{ui.DASHBOARD_CSS}</style>"
    return html_page("Sample Savings Scan", body, extra_head=extra_head)


# ── page: GET /metrics ─────────────────────────────────────────────────────────
def page_metrics(token):
    if token != ADMIN_TOKEN:
        return html_page(
            "Forbidden",
            '<div class="container"><p style="margin-top:48px;">Forbidden. '
            'Append <code>?token=YOUR_ADMIN_TOKEN</code>.</p></div>',
        )
    counts, refs = read_funnel()
    visits = counts.get("visit", 0)
    order = ["visit", "sample_view", "scan", "apply", "reserve"]
    rows = ""
    for ev in order:
        c = counts.get(ev, 0)
        pct = f"{(c / visits * 100):.1f}%" if visits else "—"
        rows += f'<tr><td>{ev}</td><td style="text-align:right;">{c}</td><td style="text-align:right;">{pct}</td></tr>'
    for ev, c in counts.items():
        if ev not in order:
            rows += f'<tr><td>{html.escape(ev)}</td><td style="text-align:right;">{c}</td><td style="text-align:right;">—</td></tr>'
    ref_rows = "".join(
        f'<tr><td>{html.escape(k)}</td><td style="text-align:right;">{v}</td></tr>'
        for k, v in sorted(refs.items(), key=lambda x: -x[1])
    ) or '<tr><td colspan="2" style="color:#64748b;">no referrers logged yet</td></tr>'
    body = f"""
<div class="container"><div style="margin-top:40px;">
  <h1>Funnel Metrics</h1>
  <p style="color:#64748b; margin-bottom:20px;">Live read for the ad probe. Kill if ~10 visits yield 0 reserve. Green on any reserve.</p>
  <div class="card"><table>
    <thead><tr><th>Funnel step</th><th style="text-align:right;">Count</th><th style="text-align:right;">% of visits</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
  <h2 style="margin-top:12px;">By referrer / UTM (?ref=)</h2>
  <div class="card"><table>
    <thead><tr><th>ref</th><th style="text-align:right;">visits</th></tr></thead>
    <tbody>{ref_rows}</tbody></table></div>
</div></div>"""
    return html_page("Metrics", body)


# ── handle_reserve: email capture (reusable for both GET /reserve and POST) ───
def handle_reserve(email, note):
    """Capture reserve email, return thank-you HTML."""
    log_event("reserve")
    append_to_store("reserve.txt", {"email": email, "note": note})
    print(f"[RESERVE] email={email!r} note={note!r}")
    body = """
<div class="container" style="max-width:560px;">
  <div style="margin-top:64px; text-align:center;">
    <div style="font-size:3rem; margin-bottom:16px;">✅</div>
    <h1>Reserved</h1>
    <p class="subtitle" style="margin:0 auto;">
      Thanks! You're on the list for $99/mo weekly bill-spike alerts + monthly report.
      We'll reach out when your slot is ready.
    </p>
    <a href="/" class="btn btn-outline" style="margin-top:24px;">← Home</a>
  </div>
</div>"""
    return html_page("Reserved", body)


# ── page: GET /reserve (form) ─────────────────────────────────────────────────
def page_reserve_form():
    body = """
<div class="container" style="max-width:560px;">
  <div style="margin-top:64px;">
    <h1>Get Weekly Bill-Spike Alerts</h1>
    <p class="subtitle">$99/mo — Slack or email alerts when a deploy spikes your Datadog bill, plus a monthly breakdown report.</p>
    <div class="card">
      <form method="POST" action="/reserve">
        <div class="form-group">
          <label class="form-label" for="res_email">Email address</label>
          <input type="email" id="res_email" name="email" placeholder="you@company.com" required>
        </div>
        <div class="form-group">
          <label class="form-label" for="res_note">Tell us a bit about your setup (optional)</label>
          <input type="text" id="res_note" name="note" placeholder="e.g. ~50 hosts, weekly bill around $8k">
        </div>
        <button type="submit" class="btn btn-primary btn-lg" style="width:100%;">Reserve my spot →</button>
      </form>
    </div>
  </div>
</div>"""
    return html_page("Reserve Alerts", body)


# ── page: GET /pricing ────────────────────────────────────────────────────────
def page_pricing():
    """Pricing tier page (Free scan vs $99/mo Protection)."""
    body = """
<div class="container" style="max-width:960px;">
  <div style="margin-top:48px; margin-bottom:40px;">
    <h1>Simple, Transparent Pricing</h1>
    <p class="subtitle">Choose the plan that fits your team.</p>
  </div>

  <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:28px; margin-bottom:48px;">
    <div class="card">
      <h2 style="color:#059669; margin-bottom:12px;">Free Scan</h2>
      <div style="font-size:2rem; font-weight:700; color:#0f172a; margin-bottom:16px;">$0</div>
      <ul style="list-style:none; margin-bottom:24px; color:#475569;">
        <li style="margin-bottom:10px;">✓ One-time scan</li>
        <li style="margin-bottom:10px;">✓ Read-only access</li>
        <li style="margin-bottom:10px;">✓ All findings + fix queries</li>
        <li style="margin-bottom:10px;">✓ No account needed</li>
        <li style="margin-bottom:10px;">✓ Download CSV export</li>
      </ul>
      <a href="/" class="btn btn-primary" style="width:100%;">Start Scanning →</a>
    </div>

    <div class="card" style="border:2px solid #059669; box-shadow: 0 8px 24px rgba(5,150,105,0.15);">
      <div style="background:#f0fdf4; border-radius:6px; padding:8px 14px; display:inline-block; margin-bottom:12px;">
        <span style="color:#059669; font-weight:700; font-size:0.8rem;">POPULAR</span>
      </div>
      <h2 style="color:#059669; margin-bottom:12px;">Protection</h2>
      <div style="font-size:2rem; font-weight:700; color:#0f172a; margin-bottom:4px;">$99<span style="font-size:0.5em; color:#64748b;">/month</span></div>
      <p style="color:#64748b; font-size:0.9rem; margin-bottom:16px;">Billed monthly. Cancel anytime.</p>
      <ul style="list-style:none; margin-bottom:24px; color:#475569;">
        <li style="margin-bottom:10px;">✓ Continuous hourly watchdog</li>
        <li style="margin-bottom:10px;">✓ Surge alerts (email + Slack)</li>
        <li style="margin-bottom:10px;">✓ One-click remediations</li>
        <li style="margin-bottom:10px;">✓ Monthly breakdown report</li>
        <li style="margin-bottom:10px;">✓ Auto-protection guardrails</li>
      </ul>
      <a href="/reserve" class="btn btn-primary" style="width:100%;">Reserve Your Spot →</a>
    </div>
  </div>

  <div class="card" style="background:#f8fafc; border-color:#d1fae5;">
    <h3>Frequently Asked</h3>
    <div style="margin-top:16px;">
      <p style="color:#0f172a; font-weight:600; margin-bottom:6px;">Can I try Protection first?</p>
      <p style="color:#475569; margin-bottom:16px;">Yes! Reserve a spot and we'll give you a trial period to verify the tool works with your Datadog account.</p>

      <p style="color:#0f172a; font-weight:600; margin-bottom:6px;">Is there a contract?</p>
      <p style="color:#475569; margin-bottom:16px;">No. Monthly billing with cancel-anytime. Your remediation guardrails stay in your hands.</p>

      <p style="color:#0f172a; font-weight:600; margin-bottom:6px;">What if I only need one scan?</p>
      <p style="color:#475569;">The Free Scan is perfect for one-time audits. No account, no commitment.</p>
    </div>
  </div>

  <div style="text-align:center; margin-top:40px;">
    <a href="/" class="btn btn-outline">← Back to Scan</a>
  </div>
</div>"""
    return html_page("Pricing", body)


# ── page: GET /privacy ─────────────────────────────────────────────────────────
def page_privacy():
    """Privacy policy."""
    body = """
<div class="container" style="max-width:860px;">
  <div style="margin-top:48px; margin-bottom:40px;">
    <h1>Privacy Policy</h1>
  </div>

  <div class="card">
    <h2>Read-Only Access</h2>
    <p>ObservaBill accesses your Datadog account using read-only API scopes (<code>usage_read</code> and <code>billing_read</code>) only. We never request write access unless you explicitly provide a write key for remediation.</p>

    <h2 style="margin-top:24px;">Keys Are Never Stored</h2>
    <p>Your API keys, Application keys, and write keys exist in memory only during your scan. They are never written to disk, logged, or persisted in any database. Once your scan completes, all keys are discarded.</p>

    <h2 style="margin-top:24px;">Log Data is Sampled, Not Stored</h2>
    <p>ObservaBill samples your live logs to detect cost patterns and anomalies. We create masked templates (removing sensitive values) and process these in memory. Raw log data is never persisted. Only aggregated findings and template metadata are retained.</p>

    <h2 style="margin-top:24px;">Findings Are Yours</h2>
    <p>Scan results (cost summaries, pattern templates, and recommendations) are returned to you immediately and deleted after your session expires. You can download findings as CSV or print them for your records.</p>

    <h2 style="margin-top:24px;">TLS Encryption</h2>
    <p>All communication with Datadog and ObservaBill uses TLS 1.2+ encryption in transit.</p>

    <h2 style="margin-top:24px;">Questions?</h2>
    <p>Contact us at <a href="mailto:privacy@observabill.com" style="color:#059669;">privacy@observabill.com</a> with privacy concerns.</p>
  </div>

  <div style="text-align:center; margin-top:40px;">
    <a href="/" class="btn btn-outline">← Back to Home</a>
  </div>
</div>"""
    return html_page("Privacy Policy", body)


# ── page: GET /terms ───────────────────────────────────────────────────────────
def page_terms():
    """Terms of Service."""
    body = """
<div class="container" style="max-width:860px;">
  <div style="margin-top:48px; margin-bottom:40px;">
    <h1>Terms of Service</h1>
  </div>

  <div class="card">
    <h2>Use License</h2>
    <p>ObservaBill is provided as-is for auditing and optimizing your Datadog costs. You retain full ownership of your Datadog account and all data within it.</p>

    <h2 style="margin-top:24px;">No Warranty</h2>
    <p>ObservaBill is provided without warranty. Findings are estimates based on sampled data and historical trends. Always test cost optimizations in a non-production environment first. You are responsible for validating any changes to your Datadog configuration.</p>

    <h2 style="margin-top:24px;">Read-Only Tool</h2>
    <p>By default, ObservaBill operates in read-only mode. If you choose to enable one-click Apply functionality, you must provide a write-capable key scoped to the specific remediation endpoints. You remain responsible for all changes made to your Datadog account.</p>

    <h2 style="margin-top:24px;">Your Responsibility</h2>
    <p>You are responsible for:</p>
    <ul style="margin-left:20px; color:#475569;">
      <li>Managing your Datadog API and Application keys securely</li>
      <li>Verifying all cost recommendations before applying them</li>
      <li>Monitoring your Datadog bill during and after optimizations</li>
      <li>Complying with your organization's change control policies</li>
    </ul>

    <h2 style="margin-top:24px;">Limitation of Liability</h2>
    <p>ObservaBill is not liable for any indirect, incidental, or consequential damages arising from the use of this tool, including but not limited to lost revenue or data loss due to misconfigured cost optimizations.</p>

    <h2 style="margin-top:24px;">Termination</h2>
    <p>We reserve the right to suspend or terminate your access if you violate these terms or use the tool for unauthorized purposes.</p>
  </div>

  <div style="text-align:center; margin-top:40px;">
    <a href="/" class="btn btn-outline">← Back to Home</a>
  </div>
</div>"""
    return html_page("Terms of Service", body)


# ── page: GET /breakdown — secondary bill-breakdown form ──────────────────────
def page_breakdown_form():
    """Old-style bill breakdown entry form (now secondary, not the primary landing)."""
    body = """
<div style="background: linear-gradient(to bottom, #f0fdf4, #f8fafc); padding: 48px 24px 40px;">
  <div style="max-width:860px; margin:0 auto;">
    <h1 style="font-size:1.8rem; margin-bottom:12px;">Raw Bill Breakdown by Product</h1>
    <p class="subtitle">
      See the raw Datadog cost breakdown by product. For savings opportunities and one-click fixes,
      <a href="/">use the Savings Scan →</a>
    </p>
    <div class="card">
      <div class="section-label">Break down your Datadog bill</div>
      <form action="/analyze" method="POST">
        <div class="form-group">
          <label class="form-label" for="api_key">Datadog API Key</label>
          <input type="password" id="api_key" name="api_key" placeholder="Your Datadog API key"
                 autocomplete="off" spellcheck="false" required>
        </div>
        <div class="form-group">
          <label class="form-label" for="app_key">Application Key</label>
          <input type="password" id="app_key" name="app_key" placeholder="Your Datadog Application key"
                 autocomplete="off" spellcheck="false" required>
        </div>
        <div class="form-group">
          <label class="form-label" for="site">Datadog Site</label>
          <select id="site" name="site">
            <option value="us1">US1 — api.datadoghq.com (default)</option>
            <option value="us3">US3 — api.us3.datadoghq.com</option>
            <option value="us5">US5 — api.us5.datadoghq.com</option>
            <option value="eu">EU — api.datadoghq.eu</option>
            <option value="ap1">AP1 — api.ap1.datadoghq.com</option>
            <option value="ap2">AP2 — api.ap2.datadoghq.com</option>
            <option value="uk1">UK1 — api.uk1.datadoghq.com</option>
          </select>
        </div>
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
          <button type="submit" class="btn btn-primary btn-lg">Break down my bill →</button>
          <a href="/" class="btn btn-sample">← Back to Scan</a>
        </div>
      </form>
    </div>
  </div>
</div>"""
    return html_page("Bill Breakdown by Product", body)


# ── page: GET /protection ──────────────────────────────────────────────────────
def page_protection_get():
    """
    Render the protection settings page.

    Loads current policy, loads (masked) creds presence, and renders the
    protection configuration form wrapped in html_page.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    policy = config.load_policy(POLICY_PATH)
    policy["_has_creds"] = _load_protection_creds() is not None

    # Load audit lines (last 20 lines of watchdog_state.json.audit)
    # FIX 8 (LOW): audit renderer reads 'ts'/'kind' (fallback to 'timestamp'/'finding_kind')
    audit_lines = []
    audit_file = PROTECT_STATE_PATH + ".audit"
    if os.path.exists(audit_file):
        try:
            with open(audit_file, "r") as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        # Convert record to a short human-readable string (masked)
                        # Read ts (fallback timestamp), kind (fallback finding_kind), action
                        timestamp = record.get("ts") or record.get("timestamp", "?")
                        action = record.get("action", "?")
                        finding_kind = record.get("kind") or record.get("finding_kind", "?")
                        audit_str = f"{action} {finding_kind} on {timestamp}"
                        audit_lines.append(audit_str)
                    except Exception:
                        pass
            # Keep only last 20
            audit_lines = audit_lines[-20:]
        except Exception:
            pass

    body = ui.render_protection_page(policy, audit_lines=audit_lines)
    return html_page("Protection Settings", body, extra_head=f"<style>{ui.DASHBOARD_CSS}</style>")


# ── page: POST /protection ─────────────────────────────────────────────────────
def page_protection_post(form: dict) -> str:
    """
    Handle POST /protection: save policy and optionally update credentials.

    Form dict keys:
      - enabled: presence means enabled=True
      - dry_run: presence means dry_run=True
      - mode_exclude, mode_sample, ...: "recommend"|"alert"|"auto"
      - email, slack_webhook: strings
      - api_key, app_key, write_key, site: optional creds
      - threshold_*, guardrail_* keys for config

    Returns: HTML page (same as GET, with implicit "saved" indicator or banner)

    FIX 7 (MED): Safely parse numeric inputs; catch config.save_policy errors
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    try:
        # Build policy from form with safe numeric parsing
        policy_dict = {
            "enabled": "enabled" in form,
            "dry_run": "dry_run" in form,
            "modes": {
                "exclude": form.get("mode_exclude", ["recommend"])[0],
                "sample": form.get("mode_sample", ["recommend"])[0],
                "to_metric": form.get("mode_to_metric", ["recommend"])[0],
                "review": form.get("mode_review", ["recommend"])[0],
                "new_pattern": form.get("mode_new_pattern", ["alert"])[0],
                "cost_surge": form.get("mode_cost_surge", ["alert"])[0],
                "volume_surge": form.get("mode_volume_surge", ["alert"])[0],
            },
            "thresholds": {
                "min_cost_usd": _safe_float(form.get("min_cost_usd", ["100.0"])[0], 100.0),
                "surge_ratio": _safe_float(form.get("surge_ratio", ["1.30"])[0], 1.30),
                "wow_growth_pct": _safe_float(form.get("wow_growth_pct", ["15.0"])[0], 15.0),
                "new_pattern_min_cost_usd": _safe_float(form.get("new_pattern_min_cost_usd", ["100.0"])[0], 100.0),
                "min_confidence_for_auto": form.get("min_confidence_for_auto", ["high"])[0],
            },
            "guardrails": {
                "auto_max_actions_per_day": _safe_int(form.get("auto_max_actions_per_day", ["5"])[0], 5),
                "auto_only_actions": [a for a in ["exclude", "sample", "to_metric"] if f"auto_only_{a}" in form],
            },
            "channels": {
                "email": form.get("email", [""])[0],
                "slack_webhook": form.get("slack_webhook", [""])[0],
            },
        }

        # Save policy (may raise ValueError on invalid modes)
        config.save_policy(POLICY_PATH, policy_dict)

        # If api_key + app_key provided, save creds (blank keys = keep existing)
        api_key = form.get("api_key", [""])[0]
        app_key = form.get("app_key", [""])[0]
        if api_key and app_key:
            write_key = form.get("write_key", [""])[0]
            site = form.get("site", ["us1"])[0]
            _save_protection_creds(api_key, app_key, write_key, site)

        # Re-render the page to show success
        return page_protection_get()

    except ValueError as e:
        # Invalid settings — return friendly error page
        error_msg = f"Invalid settings: {str(e)}"
        return _error_page("Invalid Protection Settings", error_msg)
    except Exception as e:
        # Catch any other exception (should not happen with safe parsing)
        print(f"[protection-error] {type(e).__name__}: {e}")
        return _error_page("Settings Error", "Could not save settings. Please try again.")


# ── page: POST /webhook/datadog ────────────────────────────────────────────────
def page_webhook_datadog(query_params: dict, body: str) -> "tuple[str, int]":
    """
    Handle POST /webhook/datadog: one protection cycle if enabled + creds present.

    Security:
      - Requires WEBHOOK_SECRET to match query parameter 'secret'
      - Never renders any credential in response
      - On error, returns 200 (no-op) to avoid log spam in Datadog
      - Rate-limited: throttles rapid calls (< 60s apart)
      - Thread-safe: serialized with _protection_lock to prevent concurrent cycles

    Args:
        query_params: dict from urllib.parse.parse_qs (keys are lists)
        body: request body (unused, but included for symmetry)

    Returns:
        (json_response_string, status_code) tuple
    """
    # Extract and validate secret
    secret = query_params.get("secret", [""])[0]
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        return ('{"status":"forbidden"}', 403)

    # FIX 6 (HIGH): Rate limit — throttle rapid requests
    now = time.time()
    if now - _last_webhook_ts[0] < 60:
        # Too soon after last call — throttle
        return ('{"status":"throttled"}', 200)
    _last_webhook_ts[0] = now

    # Load policy and creds
    policy = config.load_policy(POLICY_PATH)
    creds = _load_protection_creds()

    # If disabled or no creds, return early (no-op)
    if not policy.get("enabled", False) or not creds:
        return ('{"status":"disabled"}', 200)

    # FIX 5 (HIGH): Thread-safe cycle execution
    # Serialize with lock to prevent scheduler + webhook racing
    with _protection_lock:
        # Run one cycle
        try:
            scan_result = savings.scan(creds["api_key"], creds["app_key"], creds["site"])
            now_iso = time.time()  # Simple epoch for now (runner uses isoformat string)
            # Format as ISO string
            from datetime import datetime
            now_iso = datetime.utcnow().isoformat() + "Z"

            runner.run_cycle(
                scan_result,
                policy,
                creds,
                PROTECT_STATE_PATH,
                now_iso,
                email_fn=None,
                slack_fn=None,
                writer_fn=dd_client.write,
            )
            return ('{"status":"ok"}', 200)
        except Exception as exc:
            # Log internally, never leak to response
            print(f"[webhook-error] {type(exc).__name__}: {exc}")
            return ('{"status":"error"}', 200)  # 200 to not trigger retries


# ── background scheduler (optional, only if env var set) ──────────────────────
def start_protection_scheduler():
    """
    Start a background daemon thread that runs protection cycles every hour.

    Only started if OBSERVABILL_ENABLE_SCHEDULER=1 env var is set.
    Never started at import or during normal test runs.
    Thread-safe: uses _protection_lock to serialize cycles (prevents webhook racing).
    """
    def scheduler_loop():
        """Run cycles every 3600 seconds (1 hour)."""
        while True:
            time.sleep(3600)
            try:
                policy = config.load_policy(POLICY_PATH)
                creds = _load_protection_creds()
                if not policy.get("enabled", False) or not creds:
                    continue

                # FIX 5 (HIGH): Thread-safe cycle execution with lock
                with _protection_lock:
                    scan_result = savings.scan(creds["api_key"], creds["app_key"], creds["site"])
                    from datetime import datetime
                    now_iso = datetime.utcnow().isoformat() + "Z"
                    runner.run_cycle(
                        scan_result,
                        policy,
                        creds,
                        PROTECT_STATE_PATH,
                        now_iso,
                        email_fn=None,
                        slack_fn=None,
                        writer_fn=dd_client.write,
                    )
            except Exception as exc:
                print(f"[scheduler-error] {type(exc).__name__}: {exc}")

    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()


# ── helper: look up opportunity by id from sample fixtures ────────────────────
def _find_opp_by_id(opp_id: str, session: "dict | None" = None) -> "dict | None":
    """
    Look up a SavingsOpportunity dict by id.

    Real scans: the opportunity (with its generated_config) is stashed in the
    server-side apply session at scan time — checked first so Apply works on
    REAL findings. Demo mode: falls back to fixtures.SAMPLE_SCAN.
    """
    if session:
        opp = session.get("opps", {}).get(opp_id)
        if opp is not None:
            return opp
    for opp in fixtures.SAMPLE_SCAN.get("opportunities", []):
        if opp.get("id") == opp_id:
            return opp
    return None


# ── server factory ─────────────────────────────────────────────────────────────
def make_server(port):
    """Create and return the HTTPServer (does not start serving)."""
    return http.server.HTTPServer(("", port), ObservaBillHandler)


# ── HTTP handler ───────────────────────────────────────────────────────────────
class ObservaBillHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")

    def send_html(self, content, status=200):
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, data, status=200):
        encoded = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        elif path == "/":
            ref = qs.get("ref", [""])[0]
            if not ref:
                referer = self.headers.get("Referer", "")
                host = self.headers.get("Host", "")
                if referer and host and host not in referer:
                    ref = referer
            log_event("visit", ref=ref)
            self.send_html(page_landing())

        elif path == "/pricing":
            self.send_html(page_pricing())

        elif path == "/privacy":
            self.send_html(page_privacy())

        elif path == "/terms":
            self.send_html(page_terms())

        elif path == "/sample":
            # page_sample() calls log_event("sample_view") internally
            self.send_html(page_sample())

        elif path == "/breakdown":
            # Secondary: old bill-breakdown landing form
            self.send_html(page_breakdown_form())

        elif path == "/reserve":
            self.send_html(page_reserve_form())

        elif path == "/metrics":
            self.send_html(page_metrics(qs.get("token", [""])[0]))

        elif path == "/protection":
            self.send_html(page_protection_get())

        elif path == "/scan/status":
            job_id = qs.get("id", [""])[0]
            body, status = page_scan_status(job_id)
            self.send_json(json.loads(body), status)

        elif path == "/scan/result":
            job_id = qs.get("id", [""])[0]
            result = page_scan_result(job_id)
            self.send_html(result)

        else:
            self.send_html(
                '<div style="font-family:sans-serif;padding:40px;background:#f8fafc;color:#0f172a;min-height:100vh;">'
                '<h1>404</h1><p><a href="/" style="color:#059669;">← Home</a></p></div>',
                404,
            )

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        params = urllib.parse.parse_qs(body, keep_blank_values=True)

        if path == "/scan":
            api_key = params.get("api_key", [""])[0]
            app_key = params.get("app_key", [""])[0]
            site = params.get("site", ["us1"])[0]
            write_key = params.get("write_key", [""])[0] or None
            self.send_html(page_scan(api_key, app_key, site, write_key=write_key))

        elif path == "/apply":
            # SECURITY: never read api_key/app_key/write_key from the client form.
            # All keys are retrieved server-side via the apply_token.
            opp_id = params.get("opp_id", [""])[0]
            apply_token = params.get("apply_token", [""])[0]
            confirm_val = params.get("confirm", [""])[0]
            confirm = bool(confirm_val and confirm_val.strip() not in ("", "0"))

            html_content, status = page_apply(
                opp_id=opp_id,
                apply_token=apply_token,
                confirm=confirm,
            )
            self.send_html(html_content, status)

        elif path == "/analyze":
            # Legacy: redirect to /breakdown form or handle directly
            api_key = params.get("api_key", [""])[0]
            app_key = params.get("app_key", [""])[0]
            site = params.get("site", ["us1"])[0]
            self.send_html(page_analyze(api_key, app_key, site))

        elif path == "/reserve":
            email = params.get("email", [""])[0]
            note = params.get("note", [""])[0]
            self.send_html(handle_reserve(email, note))

        elif path == "/protection":
            self.send_html(page_protection_post(params))

        elif path == "/webhook/datadog":
            qs = urllib.parse.parse_qs(parsed.query)
            response_body, status = page_webhook_datadog(qs, body)
            self.send_html(response_body, status)

        else:
            self.send_html("Not found", 404)


# ── entrypoint ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    server = make_server(PORT)

    # Start background scheduler if enabled
    if os.environ.get("OBSERVABILL_ENABLE_SCHEDULER") == "1":
        start_protection_scheduler()

    print(f"""
╔══════════════════════════════════════════════════╗
║            ObservaBill — Dev Server              ║
║  http://localhost:{PORT}                           ║
║  Click path: / → /analyze → (breakdown)           ║
║              → /sample (no keys needed)           ║
║              → /protection (settings)             ║
╚══════════════════════════════════════════════════╝
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
