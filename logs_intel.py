"""
logs_intel.py — ObservaBill v2 log-CONTENT intelligence engine.

Python 3.11 stdlib only. Read-only. No third-party dependencies.

This is what makes ObservaBill more than a GROUP BY: instead of counting logs
by service, we sample the ACTUAL log content, mine the repeated message
TEMPLATES, classify each as noise vs signal, estimate its $ , detect volume
anomalies / newly-emerged noisy patterns, and flag field bloat — each producing
a specific "exclude / sample / convert / trim THIS pattern" recommendation with
a redacted sample, the exact query, the $/mo, a confidence, and why it's safe.

PRIVACY (load-bearing):
  - Raw log lines are held in memory only for the duration of a scan.
  - They are NEVER written to disk, logged, echoed, or placed in any returned
    structure. Only MASKED templates + counts + a single REDACTED sample leave
    this module.
  - api_key/app_key are sent only in request headers (via savings._make_headers)
    and never appear in returned data or exceptions.

Public API
----------
sample_logs(query, frm, to, api_key, app_key, site, cap=5000) -> list[event]
mine_templates(messages) -> list[{template, count, sample_redacted, ...}]
classify_template(template, level, service, ...) -> {is_noise, recommendation, ...}
estimate_template_cost(template_count, sample_size, service_monthly_events, price) -> {...}
build_pattern_opportunity(...) -> SavingsOpportunity dict
detect_pattern_opportunities(cost_map, api_key, app_key, site, prices, metric_catalog) -> list[opp]
detect_volume_anomalies(series, dates=None, prices=None) -> list[anomaly]
detect_field_bloat(events, prices=None) -> list[{field_name, ...}]
fetch_metric_catalog(api_key, app_key, site) -> list[str]   # log-based metric filter queries
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from typing import Any

import dd_client  # base_url + typed errors


# ===========================================================================
# Token masking — turn a variable log line into a stable template
# ===========================================================================

# Order matters: most specific patterns first.
_EMAIL_TOKEN_RE = re.compile(r"^[\w.+\-]+@[\w\-]+\.[\w.\-]+$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?$")
_IPV6_RE = re.compile(r"^(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$")
_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$|^[0-9a-fA-F]{16,}$")
_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"          # ISO datetime
    r"|^\d{2}:\d{2}:\d{2}(?:\.\d+)?$"                    # bare time
)
_NUM_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:ms|s|us|ns|kb|mb|gb|b|%)?$", re.IGNORECASE)
_PATH_RE = re.compile(r"^/(?:[\w.\-]+/){1,}[\w.\-]*$|^/[\w.\-]+$")
_URL_RE = re.compile(r"^[a-z]+://", re.IGNORECASE)
# Opaque id: mixed letters+digits, reasonably long, not a normal word.
_ID_RE = re.compile(r"^(?=.*\d)(?=.*[a-zA-Z])[\w\-]{8,}$")


def _mask_token(tok: str) -> str:
    """Map a single whitespace-delimited token to a placeholder, or itself.

    Deterministic; the ORDER of checks is chosen so the most-specific shape wins.
    """
    if not tok:
        return tok
    # strip surrounding punctuation that commonly wraps values: (), [], {}, quotes, trailing ,;:
    core = tok.strip("()[]{}\"'`,;")
    if not core:
        return tok
    if _EMAIL_TOKEN_RE.match(core):
        return "<EMAIL>"
    if _UUID_RE.match(core):
        return "<UUID>"
    if _IPV4_RE.match(core) or _IPV6_RE.match(core):
        return "<IP>"
    if _TS_RE.match(core):
        return "<TS>"
    if _HEX_RE.match(core):
        return "<HEX>"
    if _NUM_RE.match(core):
        return "<NUM>"
    if _URL_RE.match(core):
        return "<URL>"
    if _PATH_RE.match(core):
        return "<PATH>"
    if _ID_RE.match(core):
        return "<ID>"
    return tok


def _tokenize(message: str) -> list[str]:
    return message.split()


def _masked_tokens(message: str) -> list[str]:
    return [_mask_token(t) for t in _tokenize(message)]


# ===========================================================================
# PII redaction — for the single human-visible sample per template
# ===========================================================================

_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
_IP_INLINE_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_UUID_INLINE_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_CARD_RE = re.compile(r"\b\d{13,19}\b")
_CARD_SPACED_RE = re.compile(r"\b\d{4}[ \-]\d{4}[ \-]\d{4}[ \-]\d{3,4}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,2}[ \-.])?\(?\d{3}\)?[ \-.]\d{3}[ \-.]\d{4}(?!\d)")
# JWT tokens start with eyJ (base64-encoded eyJ header) and have 3 parts separated by dots
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
# long opaque secret/token-ish blobs (mixed case + digits, >=12)
_SECRET_RE = re.compile(r"\b(?=[\w\-]*\d)(?=[\w\-]*[a-z])(?=[\w\-]*[A-Z])[\w\-]{12,}\b")
# any long alphanumeric run (hex keys, base64, AWS keys, tokens) regardless of case
_LONGTOKEN_RE = re.compile(r"\b(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{16,}\b")
_BEARER_RE = re.compile(
    r"(?i)\b(bearer|token|apikey|api[_-]?key|dd[_-]?api[_-]?key|access[_-]?key|"
    r"secret|password|passwd|pwd|auth|credential)\b[=:\s\"']+(?!<)(?:\S+)")
# IPv4-mapped IPv6 like ::ffff:10.0.0.1
_IP6MAP_RE = re.compile(r"::ffff:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", re.IGNORECASE)


def _redact(text: str) -> str:
    """Scrub obvious PII / secrets from a human-visible sample string.

    Applied to the ONE redacted sample kept per template. Conservative — it is
    safe to over-redact a sample; the template already carries the shape.
    """
    if not text:
        return text
    s = text
    s = _JWT_RE.sub("<JWT>", s)       # JWT tokens (eyJ[...].eyJ[...].sig format) - do before BEARER_RE
    s = _BEARER_RE.sub(lambda m: m.group(0).split(m.group(1))[0] + m.group(1) + " <REDACTED>", s)
    s = _EMAIL_RE.sub("<EMAIL>", s)
    s = _UUID_INLINE_RE.sub("<UUID>", s)
    s = _IP6MAP_RE.sub("<IP>", s)
    s = _IP_INLINE_RE.sub("<IP>", s)
    s = _SSN_RE.sub("<SSN>", s)
    s = _PHONE_RE.sub("<PHONE>", s)
    s = _CARD_SPACED_RE.sub("<NUM>", s)
    s = _CARD_RE.sub("<NUM>", s)
    s = _LONGTOKEN_RE.sub("<ID>", s)   # hex/base64/AWS keys of any case
    s = _SECRET_RE.sub("<ID>", s)
    return s


# ===========================================================================
# Template mining — simplified Drain (mask -> group -> wildcard-merge)
# ===========================================================================

# Two masked messages of equal length merge into one template if at least this
# fraction of positions already agree (the rest become <*> wildcards).
_SIM_THRESHOLD = 0.5
# Cap distinct templates returned (ranked by count).
_MAX_TEMPLATES = 40


def _merge_template(existing: list[str], incoming: list[str]) -> list[str]:
    """Return a token list where positions that differ become '<*>'."""
    return [a if a == b else "<*>" for a, b in zip(existing, incoming)]


def _similarity(a: list[str], b: list[str]) -> float:
    """Fraction of aligned positions that are equal (a,b same length)."""
    if not a:
        return 1.0
    same = sum(1 for x, y in zip(a, b) if x == y)
    return same / len(a)


def mine_templates(messages: list[str]) -> list[dict]:
    """Cluster raw log messages into templates.

    Returns a list (ranked by count desc) of:
      {template, count, sample_redacted, tokens}
    where `template` is the masked+wildcarded string.

    Raw messages are consumed here and never returned.
    """
    # Bucket by (token_count, first_masked_token) — the Drain "log key" — so we
    # only compare messages that could plausibly share a template.
    buckets: dict[tuple, list[dict]] = defaultdict(list)

    for msg in messages:
        if not msg:
            continue
        masked = _masked_tokens(msg)
        if not masked:
            continue
        key = (len(masked), masked[0])
        clusters = buckets[key]
        placed = False
        for cl in clusters:
            if _similarity(cl["tokens"], masked) >= _SIM_THRESHOLD:
                cl["tokens"] = _merge_template(cl["tokens"], masked)
                cl["count"] += 1
                placed = True
                break
        if not placed:
            clusters.append({
                "tokens": masked,
                "count": 1,
                "sample_redacted": _redact(msg)[:240],
            })

    # Flatten
    out: list[dict] = []
    for clusters in buckets.values():
        for cl in clusters:
            out.append({
                "template": " ".join(cl["tokens"]),
                "count": cl["count"],
                "sample_redacted": cl["sample_redacted"],
                "tokens": cl["tokens"],
            })

    out.sort(key=lambda c: c["count"], reverse=True)
    return out[:_MAX_TEMPLATES]


def template_terms(template: str) -> list[str]:
    """Extract the stable literal words from a template (drop placeholders)."""
    return [
        t.lower() for t in template.split()
        if not (t.startswith("<") and t.endswith(">"))
        and len(t) >= 3 and t.isalpha()
    ]


# ===========================================================================
# Classification — noise vs signal + recommended action
# ===========================================================================

_NOISE_LEVELS = {"debug", "trace", "verbose", "fine", "finest"}
_SIGNAL_LEVELS = {"error", "err", "critical", "crit", "fatal", "alert", "emergency"}

_HEALTH_RE = re.compile(r"(?i)\b(health(z|check)?|readiness|liveness|ping|heartbeat|/-/ready|/-/healthy)\b")
_ACCESS_2XX_RE = re.compile(r"(?i)\b(get|post|put|head|options)\b.*\b<num>\b")
_SUCCESS_RE = re.compile(r"(?i)\b(success(fully)?|completed|ok|done|processed|ack(nowledged)?|finished|200)\b")
_DEPRECATION_RE = re.compile(r"(?i)deprecat|deprecationwarning")
_BOILERPLATE_RE = re.compile(r"(?i)\b(starting|started|initializ|shutting down|listening on|bound to|registered|loaded config)\b")
_ERROR_WORDS_RE = re.compile(r"(?i)\b(error|exception|failed|failure|refused|timeout|timed out|traceback|panic|fatal|denied|unavailable|unreachable)\b")


def _is_metered(template: str, service: str, metric_catalog: list[str] | None,
                terms: list[str] | None) -> bool:
    """True if a log-based metric already meters this pattern (team cares -> don't blind-drop)."""
    if not metric_catalog:
        return False
    terms = terms or template_terms(template)
    svc = (service or "").lower()
    for q in metric_catalog:
        ql = q.lower()
        if svc and f"service:{svc}" in ql:
            # metric scoped to this service — likely relevant
            if not terms:
                return True
            if any(t in ql for t in terms):
                return True
        # term overlap even without service scope
        if terms and sum(1 for t in terms if t in ql) >= 2:
            return True
    return False


def classify_template(
    template: str,
    level: str = "",
    service: str = "",
    sample: str = "",
    metric_catalog: list[str] | None = None,
    template_terms: list[str] | None = None,
) -> dict:
    """Score a template as noise vs signal and recommend an action.

    Returns:
      {is_noise, recommendation, confidence, why_safe, noise_score, metered, signal}
    recommendation ∈ {exclude, sample, to_metric, trim_fields, keep}
    signal ∈ {error, noise, neutral}
    """
    lvl = (level or "").strip().lower()
    text = f"{template} {sample}".lower()
    terms = template_terms
    metered = _is_metered(template, service, metric_catalog, terms)

    score = 0.0
    reasons: list[str] = []

    # Strong SIGNAL first: real errors are never noise.
    if lvl in _SIGNAL_LEVELS or _ERROR_WORDS_RE.search(text):
        # error-level or error-y content -> keep, unless it's clearly a deprecation nag
        if _DEPRECATION_RE.search(text):
            return {
                "is_noise": True,
                "recommendation": "exclude",
                "confidence": "medium",
                "why_safe": "Deprecation warning — actionable at the source, not worth indexing at volume; fix the call site and drop the log.",
                "noise_score": 0.7,
                "metered": metered,
                "signal": "noise",
            }
        return {
            "is_noise": False,
            "recommendation": "keep",
            "confidence": "high",
            "why_safe": "Contains error/failure signal — keep indexed for debugging and alerting.",
            "noise_score": 0.0,
            "metered": metered,
            "signal": "error",
        }

    # Noise signals
    if lvl in _NOISE_LEVELS:
        score += 0.6
        reasons.append(f"{lvl}-level log")
    if _HEALTH_RE.search(text):
        score += 0.5
        reasons.append("health/readiness probe traffic")
    if _DEPRECATION_RE.search(text):
        score += 0.5
        reasons.append("deprecation warning (fix at source)")
    if _ACCESS_2XX_RE.search(template.lower()):
        score += 0.35
        reasons.append("2xx access-log traffic")
    if _SUCCESS_RE.search(text):
        score += 0.5
        reasons.append("routine success/ack confirmation repeated at volume")
    if _BOILERPLATE_RE.search(text):
        score += 0.25
        reasons.append("framework/startup boilerplate")

    is_noise = score >= 0.5

    # Recommendation + signal
    if not is_noise:
        rec = "keep"
        conf = "high"
        why = "No strong noise signal detected — kept indexed by default (conservative)."
        sig = "neutral"  # TASK A: neutral for operational logs that aren't noise/error
    else:
        if lvl in _NOISE_LEVELS or _DEPRECATION_RE.search(text) or _HEALTH_RE.search(text):
            rec = "exclude"
        elif _SUCCESS_RE.search(text) or _ACCESS_2XX_RE.search(template.lower()):
            rec = "sample"   # keep a fraction; success/access still useful at low rate
        else:
            rec = "sample"
        conf = "high" if score >= 0.8 else "medium"
        why = (
            "Safe to " + ("drop" if rec == "exclude" else "down-sample")
            + " — " + "; ".join(reasons)
            + ". Carries no unique error signal, so debugging/alerting is unaffected."
        )
        sig = "noise"  # TASK A: noise for patterns classified as noisy

    # Metered override: the team already meters this -> don't blind-exclude.
    if metered and is_noise:
        if rec == "exclude":
            rec = "to_metric"
        conf = "low"
        why += (" NOTE: a log-based metric already tracks this pattern — the team "
                "relies on it, so convert to/keep the metric rather than dropping blindly.")

    return {
        "is_noise": is_noise,
        "recommendation": rec,
        "confidence": conf,
        "why_safe": why,
        "noise_score": round(min(score, 1.0), 2),
        "metered": metered,
        "signal": sig,
    }


# ===========================================================================
# Cost estimation
# ===========================================================================

def estimate_template_cost(
    template_count: int,
    sample_size: int,
    service_monthly_events: float,
    price_per_million: float,
) -> dict:
    """Extrapolate a template's share of its service's monthly indexed volume to $.

    template's share = template_count / sample_size
    monthly_events   = share * service_monthly_events
    monthly_cost_usd = monthly_events / 1e6 * price_per_million
    """
    if sample_size <= 0 or template_count <= 0:
        return {"share": 0.0, "monthly_events": 0, "monthly_cost_usd": 0.0}
    share = template_count / sample_size
    monthly_events = share * service_monthly_events
    cost = monthly_events / 1_000_000 * price_per_million
    return {
        "share": round(share, 4),
        "monthly_events": int(monthly_events),
        "monthly_cost_usd": round(cost, 2),
    }


# ===========================================================================
# Build a pattern opportunity (ScanResult-compatible SavingsOpportunity)
# ===========================================================================

def _safe_name(*parts: str) -> str:
    raw = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9\-]+", "-", raw.lower()).strip("-")[:80] or "pattern"


# Literal terms lifted from a masked template become a phrase match in the query.
def _query_phrase_from_template(template: str) -> str:
    """Build a short, specific query phrase from a template's literal words."""
    words = [w for w in template.split() if not (w.startswith("<") and w.endswith(">"))]
    # take the first run of up to 4 literal words as a quoted phrase
    phrase_words: list[str] = []
    for w in words:
        cw = re.sub(r'["\\]', "", w)
        if cw:
            phrase_words.append(cw)
        if len(phrase_words) >= 4:
            break
    return " ".join(phrase_words)


def build_pattern_opportunity(
    service: str,
    template: str,
    sample_redacted: str,
    level: str,
    template_count: int,
    sample_size: int,
    service_monthly_events: float,
    price_per_million: float,
    status: str = "",
    target_index: str = "main",
    metric_catalog: list[str] | None = None,
    classification: dict | None = None,
    services: list | None = None,
) -> dict:
    r"""Assemble a SavingsOpportunity for one noisy template.

    Reuses the existing ScanResult contract (id, lever, category, evidence,
    generated_config, needs_write_scope, ...) so the UI + /apply work unchanged,
    and adds v2 fields: template, sample_redacted, recommended_action, why_safe.

    When `services` is provided (v3 pattern-first mode):
      - Store opp["services"] = services
      - Use phrase-only query (NO service: prefix) so ONE filter kills pattern across all services
      - Set title to just the template string
    When `services` is None (legacy mode):
      - Keep EXACTLY today's behavior (unchanged)

    FIX 1: If phrase/query is empty or whitespace-only, set apply_safe=False,
    generated_config=None, and needs_write_scope=False. Never generate a filter
    that matches all logs.
    """
    cls = classification or classify_template(
        template, level=level, service=service, sample=sample_redacted,
        metric_catalog=metric_catalog,
    )
    cost = estimate_template_cost(template_count, sample_size, service_monthly_events, price_per_million)

    phrase = _query_phrase_from_template(template)

    # FIX 1: Check if phrase/query is empty or whitespace-only
    is_empty_query = not phrase or not phrase.strip()

    # Determine query and title based on services param
    if services is not None:
        # v3 pattern-first mode: phrase-only query (no service: prefix)
        query = f'"{phrase}"' if phrase else ""
        title_base = template
    else:
        # Legacy mode: service-scoped query
        base_query = f"service:{service}"
        if status:
            base_query += f" status:{status}"
        query = f'{base_query} "{phrase}"' if phrase else base_query
        title_base = f"{(phrase or template)[:48]}"

    rec = cls["recommendation"]
    # sample_rate: exclude=drop all; sample=keep 10% (drop 90%); to_metric=convert
    if rec == "sample":
        sample_rate = 0.9      # exclusion filter drops 90%, keeps 10%
        realized = cost["monthly_cost_usd"] * 0.9
    elif rec == "exclude":
        sample_rate = 1.0
        realized = cost["monthly_cost_usd"]
    else:  # to_metric / trim_fields — treat like near-full realization but flagged
        sample_rate = 1.0
        realized = cost["monthly_cost_usd"]

    share_pct = round(cost["share"] * 100, 1)
    action_verb = {"exclude": "Exclude", "sample": "Down-sample", "to_metric": "Convert to metric",
                   "trim_fields": "Trim fields on", "keep": "Review"}.get(rec, "Exclude")

    # FIX 1: Set apply_safe=False and generated_config=None if query is empty
    if is_empty_query:
        generated_config = None
    else:
        generated_config = {
            "endpoint": f"/api/v1/logs/config/indexes/{target_index}",
            "verb": "PUT",
            "payload": {
                "exclusion_filters": [
                    {
                        "name": _safe_name("observabill", service, phrase or level),
                        "filter": {"query": query, "sample_rate": sample_rate},
                        "is_enabled": True,
                    }
                ]
            },
        }

    evidence = [
        {
            "label": f"{service} · template",
            "volume": f"{cost['monthly_events'] / 1_000_000:.1f}M events/mo ({share_pct}% of sampled {service})",
            "cost_usd": cost["monthly_cost_usd"],
        },
    ]

    base_query_for_detection = f"service:{service}"
    if status:
        base_query_for_detection += f" status:{status}"
    detection_query = (
        f"POST /api/v2/logs/events/search query:'{base_query_for_detection}' "
        f"-> mined {sample_size} sampled msgs -> template matched {template_count} "
        f"({share_pct}% of sample)"
    )

    # Build title
    if services is not None:
        # v3: just the template, with action verb
        title = f'{action_verb} noisy template: "{title_base[:48]}"'
    else:
        # Legacy: "verb in service"
        title = f'{action_verb} noisy template in {service}: "{title_base}"'

    # FIX 1: Update why_safe if query is empty
    why_safe_text = cls["why_safe"]
    if is_empty_query:
        why_safe_text += " (no safe exclusion query could be derived — review manually)"

    result = {
        "id": _safe_name("pattern", service, phrase or level),
        "lever": "pattern_exclusion",
        "category": "logs",
        "service": service,
        "status": status,
        "title": title,
        "summary": (
            f"Template `{template[:80]}` accounts for {share_pct}% of sampled `{service}` "
            f"logs ≈ {cost['monthly_events']/1_000_000:.1f}M events/mo (${cost['monthly_cost_usd']:.2f}/mo). "
            f"Recommended: {rec}."
        ),
        "monthly_savings_usd": round(realized, 2),
        "monthly_cost_usd": cost["monthly_cost_usd"],
        "savings_pct": f"{share_pct}%",
        "effort": "low" if rec in ("exclude", "sample") else "medium",
        "confidence": cls["confidence"],
        "evidence": evidence,
        "generated_config": generated_config,
        "needs_write_scope": False if is_empty_query else True,
        "apply_safe": False if is_empty_query else True,
        "detection_query": detection_query,
        "why": cls["why_safe"],
        # v2 content-intelligence fields:
        "template": template,
        "sample_redacted": sample_redacted,
        "recommended_action": rec,
        "why_safe": why_safe_text,
        "noise_score": cls["noise_score"],
        "metered": cls["metered"],
        "monthly_events": cost["monthly_events"],
    }

    # v3 pattern-first field
    if services is not None:
        result["services"] = services

    return result


# ===========================================================================
# mine_patterns — account-wide event clustering
# ===========================================================================

def mine_patterns(events: list[dict]) -> list[dict]:
    """Cluster events' messages into templates, tracking services/hosts/status.

    Input: events like [{"message": str, "level": str, "service": str, "host": str, "attributes": dict}]

    Returns list of patterns (ranked by count desc, capped at _MAX_TEMPLATES):
      {template, count, services, hosts, status_breakdown, sample_redacted}
    where:
      - services: [{"service": str, "count": int, "share_pct": float}] desc by count
      - hosts: distinct host strings (capped at 10)
      - status_breakdown: {level: count, ...}

    Raw messages/attributes never appear in output.
    """
    if not events:
        return []

    # First, mine templates from messages
    messages = [e.get("message", "") for e in events if e.get("message")]
    if not messages:
        return []

    templates = mine_templates(messages)
    if not templates:
        return []

    # For each template, attribute events and gather metadata
    patterns: list[dict] = []
    for tmpl in templates:
        template_str = tmpl["template"]
        template_tokens = tmpl["tokens"]
        template_count = tmpl["count"]

        # Attribute events to this template by re-clustering
        matching_events: list[dict] = []
        for e in events:
            msg = e.get("message", "")
            if not msg:
                continue
            masked = _masked_tokens(msg)
            if len(masked) != len(template_tokens):
                continue
            # Check if this message matches the template (similarity >= threshold)
            if _similarity(template_tokens, masked) >= _SIM_THRESHOLD:
                matching_events.append(e)

        if not matching_events:
            # Fallback: if no events matched, skip (shouldn't happen)
            continue

        # Gather service breakdown
        service_counter: Counter = Counter()
        for e in matching_events:
            svc = e.get("service", "unknown")
            service_counter[svc] += 1

        services = []
        total = len(matching_events)
        for svc, cnt in service_counter.most_common():
            services.append({
                "service": svc,
                "count": cnt,
                "share_pct": round(cnt / total * 100, 1) if total > 0 else 0.0,
            })

        # Gather distinct hosts (capped at 10)
        hosts_set: set = set()
        for e in matching_events:
            h = e.get("host", "")
            if h and len(hosts_set) < 10:
                hosts_set.add(h)
        hosts = sorted(list(hosts_set))[:10]

        # Status breakdown by level
        status_counter: Counter = Counter()
        for e in matching_events:
            level = e.get("level", "")
            if level:
                status_counter[level] += 1
        status_breakdown = dict(status_counter)

        # Pick one redacted sample from matching events
        sample_redacted = tmpl["sample_redacted"]

        patterns.append({
            "template": template_str,
            "count": total,
            "services": services,
            "hosts": hosts,
            "status_breakdown": status_breakdown,
            "sample_redacted": sample_redacted,
        })

    # Sort by count desc and cap
    patterns.sort(key=lambda p: p["count"], reverse=True)
    return patterns[:_MAX_TEMPLATES]


# ===========================================================================
# build_template_leaderboard — cost-ranked patterns account-wide
# ===========================================================================

def build_template_leaderboard(
    patterns: list[dict],
    sample_size: int,
    account_monthly_events: float,
    price_per_million: float,
    total_bill_cost: float,
) -> list[dict]:
    """Build a cost-ranked leaderboard from patterns with cumulative cost %.

    Parameters
    ----------
    patterns : output of mine_patterns
    sample_size : total events sampled
    account_monthly_events : monthly events across full account
    price_per_million : $/1M events
    total_bill_cost : total account bill for reference

    For each pattern: compute monthly_events and monthly_cost_usd via estimate_template_cost.
    Filter to cost >= $100/mo OR share >= 0.1%.
    Sort by monthly_cost_usd desc; add cumulative_pct running total.
    """
    if not patterns or sample_size <= 0:
        return []

    leaderboard: list[dict] = []
    for p in patterns:
        count = p.get("count", 0)
        # Estimate cost as if this pattern is account-wide (all events, not just sample)
        cost = estimate_template_cost(count, sample_size, account_monthly_events, price_per_million)

        # Filter by cost or share thresholds
        monthly_cost = cost["monthly_cost_usd"]
        share = cost["share"]
        if monthly_cost < 100 and share < 0.001:
            continue  # drop tiny patterns

        row = dict(p)  # copy pattern dict
        row["monthly_events"] = cost["monthly_events"]
        row["monthly_cost_usd"] = monthly_cost
        row["share_pct"] = round(share * 100, 2)
        leaderboard.append(row)

    # Sort by monthly_cost_usd desc
    leaderboard.sort(key=lambda r: r["monthly_cost_usd"], reverse=True)

    # Add cumulative_pct
    total_cost = sum(r["monthly_cost_usd"] for r in leaderboard)
    cumulative = 0.0
    for row in leaderboard:
        cumulative += row["monthly_cost_usd"]
        row["cumulative_pct"] = round(cumulative / total_bill_cost * 100, 1) if total_bill_cost > 0 else 0.0

    return leaderboard


# ===========================================================================
# cluster_similar_templates — group related patterns by Jaccard similarity
# ===========================================================================

def cluster_similar_templates(
    leaderboard: list[dict],
    min_jaccard: float = 0.65,
    min_combined_cost: float = 20.0,
) -> list[dict]:
    """Cluster similar templates into families by Jaccard on term sets.

    Parameters
    ----------
    leaderboard : output of build_template_leaderboard
    min_jaccard : minimum Jaccard(A∩B / A∪B) to consider templates related
    min_combined_cost : minimum combined monthly_cost_usd to emit family

    Returns list of families sorted by combined_monthly_cost_usd desc:
      {family_terms, members, member_count, combined_monthly_cost_usd, combined_query}
    where combined_query is an OR phrase like "connection timeout" OR "socket hang up"
    """
    if not leaderboard:
        return []

    # Union-find clustering
    n = len(leaderboard)
    parent = list(range(n))

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # For each pair, compute Jaccard on term sets
    for i in range(n):
        for j in range(i + 1, n):
            ti = leaderboard[i].get("template", "")
            tj = leaderboard[j].get("template", "")

            terms_i = set(template_terms(ti))
            terms_j = set(template_terms(tj))

            if not terms_i or not terms_j:
                continue

            intersection = len(terms_i & terms_j)
            union_size = len(terms_i | terms_j)
            jaccard = intersection / union_size if union_size > 0 else 0.0

            if jaccard >= min_jaccard:
                union(i, j)

    # Group by root parent
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        root = find(i)
        groups[root].append(i)

    families: list[dict] = []
    for indices in groups.values():
        if len(indices) < 2:
            continue  # family must have >=2 members

        members = [leaderboard[i] for i in indices]
        combined_cost = sum(m.get("monthly_cost_usd", 0) for m in members)

        if combined_cost < min_combined_cost:
            continue

        # family_terms = intersection of all members' term sets
        term_sets = [set(template_terms(m.get("template", ""))) for m in members]
        family_terms = sorted(list(set.intersection(*term_sets))) if term_sets else []

        # combined_query: OR of member phrases (first 4 literal words per template)
        phrases = []
        for m in members:
            tmpl = m.get("template", "")
            words = [w for w in tmpl.split() if not (w.startswith("<") and w.endswith(">"))]
            phrase = " ".join(words[:4])
            if phrase:
                phrases.append(f'"{phrase}"')
        combined_query = " OR ".join(phrases) if phrases else ""

        families.append({
            "family_terms": family_terms,
            "members": members,
            "member_count": len(members),
            "combined_monthly_cost_usd": combined_cost,
            "combined_query": combined_query,
        })

    # Sort by combined cost desc
    families.sort(key=lambda f: f["combined_monthly_cost_usd"], reverse=True)
    return families


# ===========================================================================
# analyze_patterns — orchestrator combining pattern mining, classification, ranking
# ===========================================================================

def analyze_patterns(
    events: list[dict],
    sample_size: int,
    account_monthly_events: float,
    price_per_million: float,
    total_bill_cost: float,
    metric_catalog: list[str] | None = None,
    target_index: str = "main",
) -> dict:
    """Orchestrator: mine patterns, classify, attach costs, rank opportunities.

    Steps:
    1. patterns = mine_patterns(events)
    2. leaderboard = build_template_leaderboard(patterns, ...)
    3. For each row in leaderboard:
       - Derive modal level from status_breakdown (key with max value)
       - Classify the template
       - Attach classification to row
       - If is_noise=False, skip opportunity but keep row in leaderboard
       - Else build opportunity with services, overwrite account-wide costs, append
    4. families = cluster_similar_templates(leaderboard)
    5. Return {opportunities (sorted desc by savings), leaderboard, families}
    """
    # Step 1: mine patterns
    patterns = mine_patterns(events)
    if not patterns:
        return {"opportunities": [], "leaderboard": [], "families": []}

    # Step 2: build leaderboard
    leaderboard = build_template_leaderboard(patterns, sample_size, account_monthly_events, price_per_million, total_bill_cost)
    if not leaderboard:
        return {"opportunities": [], "leaderboard": [], "families": []}

    # Step 3: classify each pattern in leaderboard and build opportunities
    opportunities: list[dict] = []

    for row in leaderboard:
        template_str = row.get("template", "")
        status_breakdown = row.get("status_breakdown", {})
        services = row.get("services", [])

        # Derive modal level (key with max count in status_breakdown)
        modal_level = ""
        if status_breakdown:
            modal_level = max(status_breakdown.keys(), key=lambda k: status_breakdown[k])

        # Derive first service from services list
        svc0 = services[0]["service"] if services else ""

        # Classify the template
        cls = classify_template(
            template_str,
            level=modal_level,
            service=svc0,
            sample=row.get("sample_redacted", ""),
            metric_catalog=metric_catalog,
        )
        row["classification"] = cls

        # TASK B: Add recommended_action to leaderboard row
        monthly_cost = row.get("monthly_cost_usd", 0.0)
        if cls["is_noise"]:
            # Noise patterns: use the recommendation from classification
            row["recommended_action"] = cls["recommendation"]
        elif cls["signal"] == "error":
            # Error signals: always keep
            row["recommended_action"] = "keep"
        elif cls["signal"] == "neutral" and monthly_cost >= 100:
            # High-cost neutral patterns: recommend review
            row["recommended_action"] = "review"
        else:
            # Default: keep
            row["recommended_action"] = "keep"

        # If not noise, skip opportunity (but keep row in leaderboard)
        if not cls["is_noise"]:
            # If it's neutral and high-cost, add a review opportunity
            if cls["signal"] == "neutral" and monthly_cost >= 100:
                # Create a review opportunity (0 savings, low confidence, informational)
                review_opp = {
                    "id": _safe_name("review", svc0, template_str[:20]),
                    "lever": "pattern_review",
                    "category": "logs",
                    "service": svc0,
                    "status": "",
                    "title": f"Review high-cost operational pattern: \"{template_str[:48]}\"",
                    "summary": (
                        f"Template `{template_str[:80]}` costs ${monthly_cost:.2f}/mo "
                        f"({row.get('share_pct', 0.0)}% of sampled logs). "
                        f"It's an operational log (not error/noise) — confirm it's needed before sampling."
                    ),
                    "monthly_savings_usd": 0.0,  # Review has NO guaranteed savings
                    "monthly_cost_usd": monthly_cost,
                    "savings_pct": "0%",
                    "effort": "low",
                    "confidence": "low",
                    "evidence": [
                        {
                            "label": f"{svc0} · template",
                            "volume": f"{row.get('monthly_events', 0) / 1_000_000:.1f}M events/mo ({row.get('share_pct', 0.0)}% of sampled {svc0})",
                            "cost_usd": monthly_cost,
                        },
                    ],
                    "generated_config": {
                        "endpoint": f"/api/v1/logs/config/indexes/{target_index}",
                        "verb": "PUT",
                        "payload": {"exclusion_filters": []},  # No auto-action for review
                    },
                    "needs_write_scope": False,
                    "detection_query": (
                        f"POST /api/v2/logs/events/search query:'*' "
                        f"-> mined templates -> matched {template_str[:60]}"
                    ),
                    "why": cls["why_safe"],
                    "why_safe": (
                        f"High-volume repeated log = {row.get('share_pct', 0.0)}% of your log bill "
                        f"(${monthly_cost:.2f}/mo). Not an error — confirm it's needed; "
                        f"sampling it could cut most of this."
                    ),
                    "template": template_str,
                    "sample_redacted": row.get("sample_redacted", ""),
                    "recommended_action": "review",
                    "noise_score": cls["noise_score"],
                    "metered": cls["metered"],
                    "monthly_events": row.get("monthly_events", 0),
                    "services": services,
                }
                opportunities.append(review_opp)
            continue

        # Build opportunity with services parameter
        opp = build_pattern_opportunity(
            service=svc0,
            template=template_str,
            sample_redacted=row.get("sample_redacted", ""),
            level=modal_level,
            template_count=row.get("count", 0),
            sample_size=sample_size,
            service_monthly_events=account_monthly_events,
            price_per_million=price_per_million,
            target_index=target_index,
            metric_catalog=metric_catalog,
            classification=cls,
            services=services,
        )

        # Overwrite account-wide costs from leaderboard (more accurate)
        opp["monthly_cost_usd"] = row.get("monthly_cost_usd", 0.0)
        opp["monthly_events"] = row.get("monthly_events", 0)

        # Compute realized savings based on recommendation
        rec = cls["recommendation"]
        if rec == "sample":
            opp["monthly_savings_usd"] = round(row.get("monthly_cost_usd", 0.0) * 0.9, 2)
        elif rec == "exclude":
            opp["monthly_savings_usd"] = round(row.get("monthly_cost_usd", 0.0), 2)
        else:  # to_metric, trim_fields, keep
            opp["monthly_savings_usd"] = round(row.get("monthly_cost_usd", 0.0), 2)

        opp["share_pct"] = row.get("share_pct", 0.0)
        opp["cumulative_pct"] = row.get("cumulative_pct", 0.0)

        opportunities.append(opp)

    # Sort opportunities: real savings first (desc), then review opps (0 savings)
    # Stable sort ensures review opportunities stay after real ones
    opportunities.sort(key=lambda o: (
        o.get("recommended_action") != "review",  # True (1) for real, False (0) for review
        -o.get("monthly_savings_usd", 0.0)  # then by savings descending
    ))

    # Step 4: cluster families
    families = cluster_similar_templates(leaderboard)

    # Step 5: return structured result
    return {
        "opportunities": opportunities,
        "leaderboard": leaderboard,
        "families": families,
    }


# ===========================================================================
# Volume anomaly detection (REWRITE)
# ===========================================================================

_SPIKE_SIGMA = 3.0
_MIN_BASELINE_POINTS = 5


def parse_timeseries(agg: dict) -> list[tuple[str, list[float]]]:
    """Turn a logs-aggregate timeseries response into [(series_name, [counts...])].

    Real shape: data.buckets[].{by:{service}, computes:{c0:[v0,v1,...]}}. When
    c0 is a scalar (non-timeseries fallback) it is wrapped in a single-item list.
    """
    out: list[tuple[str, list[float]]] = []
    for b in agg.get("data", {}).get("buckets", []):
        by = b.get("by", {})
        name = str(by.get("service") or by.get("status") or "account")
        c0 = b.get("computes", {}).get("c0", [])
        if isinstance(c0, list):
            vals = []
            for v in c0:
                if isinstance(v, dict):          # real timeseries point: {value, time}
                    vals.append(float(v.get("value") or 0))
                else:
                    vals.append(float(v or 0))
        else:
            vals = [float(c0 or 0)]
        if vals:
            out.append((name, vals))
    return out


def daily_labels(days: int) -> list[str]:
    """Best-effort YYYY-MM-DD labels for the last `days` buckets (relative offsets).

    Uses only relative day arithmetic — no wall-clock in the module hot path;
    callers may pass their own `dates` to detect_volume_anomalies for exact labels.
    """
    return [f"d-{days - 1 - i}" for i in range(days)]


def detect_volume_anomalies(
    series: list[tuple[str, list[float]]],
    dates: list[str] | None = None,
    price_per_million: float | None = None,
) -> list[dict]:
    """Detect volume anomalies across per-series daily counts.

    Parameters
    ----------
    series : list of (series_name, [count_per_bucket, ...])
    dates  : optional list of bucket date labels (same length as each series)
    price_per_million : optional, to attach an approximate $ to the anomaly

    Detects 4 regimes (each requires >= 15 points unless noted):
      1. "spike": latest > mean(baseline) + 3σ AND excess >= 16667/day (~500k/mo)
      2. "level_shift": mean(last 3) / mean(prior 14) >= 1.20 AND mean(last3) > 10000
      3. "wow_growth": sum(last 7)/sum(prior 7) >= 1.15 AND sum(last7) > 70000
      4. "new_pattern": first 10 == 0 and rest > 0; onset = first non-zero index
               (always surfaced if detected)

    For 1-3: only surface if monthly_cost_usd > 50 (when price given) or volume gate.
    Returns at most 1 anomaly per series (highest severity kind ranking).
    Ranked by severity desc.
    """
    out: list[dict] = []
    for name, counts in series:
        if len(counts) < _MIN_BASELINE_POINTS + 1:
            continue

        anomalies_for_series: list[dict] = []

        # --- Regime 4: new_pattern (no minimum length, always surfaced) ---
        if len(counts) >= 17:
            first_10 = counts[:10]
            rest = counts[10:]
            if sum(first_10) == 0 and sum(rest) > 0:
                onset_idx = next((i for i, c in enumerate(counts) if c > 0), 10)
                recent_half = counts[onset_idx + len(counts)//2:] if onset_idx < len(counts) else counts[onset_idx:]
                recent_avg = statistics.mean([c for c in recent_half if c > 0]) if any(recent_half) else 0
                monthly = recent_avg * 30
                anomaly = {
                    "kind": "new_pattern",
                    "series": name,
                    "onset_index": onset_idx,
                    "onset_date": dates[onset_idx] if dates and onset_idx < len(dates) else "",
                    "recent_daily_avg": int(recent_avg),
                    "monthly_events": int(monthly),
                    "severity": monthly,
                }
                if price_per_million:
                    anomaly["monthly_cost_usd"] = round(monthly / 1_000_000 * price_per_million, 2)
                anomalies_for_series.append(anomaly)

        # --- Regime 1: spike ---
        if len(counts) >= _MIN_BASELINE_POINTS + 1:
            baseline = counts[:-1]
            latest = counts[-1]
            mean_bl = statistics.mean(baseline)
            std_bl = statistics.pstdev(baseline)
            threshold = mean_bl + _SPIKE_SIGMA * std_bl
            excess_daily = latest - mean_bl

            # Require: latest > 3-sigma threshold, excess >= 5000/day (baseline filter)
            if std_bl > 0 and latest > threshold and excess_daily >= 5000:
                monthly_excess = excess_daily * 30
                anomaly = {
                    "kind": "spike",
                    "series": name,
                    "latest": int(latest),
                    "baseline_mean": int(mean_bl),
                    "sigma": round(excess_daily / std_bl, 1),
                    "onset_date": dates[-1] if dates else "",
                    "monthly_excess_events": int(monthly_excess),
                    "severity": monthly_excess,
                }
                if price_per_million:
                    anomaly["monthly_cost_usd"] = round(monthly_excess / 1_000_000 * price_per_million, 2)
                else:
                    anomaly["monthly_cost_usd"] = 0.0
                # Only surface if cost > 50 or volume gate passes
                if price_per_million and anomaly.get("monthly_cost_usd", 0) > 50:
                    anomalies_for_series.append(anomaly)
                elif not price_per_million:
                    anomalies_for_series.append(anomaly)

        # --- Regime 2: level_shift (mean last 3 / mean prior 14) ---
        if len(counts) >= 17:  # need 14 prior + 3 recent
            prior_14 = counts[:14]
            last_3 = counts[-3:]
            mean_prior = statistics.mean(prior_14)
            mean_recent = statistics.mean(last_3)
            if mean_prior > 0 and mean_recent / mean_prior >= 1.20 and mean_recent > 100:
                monthly_excess = (mean_recent - mean_prior) * 30
                anomaly = {
                    "kind": "level_shift",
                    "series": name,
                    "ratio": round(mean_recent / mean_prior, 2),
                    "baseline_mean": int(mean_prior),
                    "recent_mean": int(mean_recent),
                    "onset_date": dates[-3] if dates and len(dates) >= 3 else "",
                    "monthly_excess_events": int(monthly_excess),
                    "severity": monthly_excess,
                }
                if price_per_million:
                    anomaly["monthly_cost_usd"] = round(monthly_excess / 1_000_000 * price_per_million, 2)
                else:
                    anomaly["monthly_cost_usd"] = 0.0
                # Only surface if cost > 50 or volume gate
                if price_per_million and anomaly.get("monthly_cost_usd", 0) > 50:
                    anomalies_for_series.append(anomaly)
                elif not price_per_million:
                    anomalies_for_series.append(anomaly)

        # --- Regime 3: wow_growth (last 7 / prior 7) ---
        if len(counts) >= 14:  # need 7 prior + 7 recent
            prior_7 = counts[:-7]
            last_7 = counts[-7:]
            sum_prior = sum(prior_7) if prior_7 else 0
            sum_last = sum(last_7)
            if sum_prior > 0 and sum_last / sum_prior >= 1.15 and sum_last > 1000:
                avg_last = sum_last / 7
                avg_prior = sum_prior / len(prior_7) if prior_7 else 0
                monthly_excess = (avg_last - avg_prior) * 30
                growth_pct = round((sum_last - sum_prior) / sum_prior * 100, 1) if sum_prior > 0 else 0
                anomaly = {
                    "kind": "wow_growth",
                    "series": name,
                    "growth_pct": growth_pct,
                    "onset_date": dates[-7] if dates and len(dates) >= 7 else "",
                    "monthly_excess_events": int(monthly_excess),
                    "severity": monthly_excess,
                }
                if price_per_million:
                    anomaly["monthly_cost_usd"] = round(monthly_excess / 1_000_000 * price_per_million, 2)
                else:
                    anomaly["monthly_cost_usd"] = 0.0
                # Only surface if cost > 50 or volume gate
                if price_per_million and anomaly.get("monthly_cost_usd", 0) > 50:
                    anomalies_for_series.append(anomaly)
                elif not price_per_million:
                    anomalies_for_series.append(anomaly)

        # Pick at most 1 anomaly per series: highest-severity kind ranking
        if anomalies_for_series:
            # Rank: new_pattern > spike > level_shift > wow_growth
            kind_rank = {"new_pattern": 4, "spike": 3, "level_shift": 2, "wow_growth": 1}
            best = max(anomalies_for_series,
                      key=lambda a: (kind_rank.get(a["kind"], 0), a.get("severity", 0)))
            out.append(best)

    out.sort(key=lambda a: a.get("severity", 0), reverse=True)
    return out


# ===========================================================================
# Field / attribute bloat
# ===========================================================================

_MIN_FIELD_BYTES = 200          # avg serialized bytes to be "bloat"
_HIGH_CARDINALITY_RATIO = 0.5   # distinct/count above this = unbounded-ish
_MIN_EVENTS_FOR_BLOAT = 20


def _flatten_attrs(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested attributes dict to dotted keys -> leaf value."""
    flat: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                flat.update(_flatten_attrs(v, key))
            else:
                flat[key] = v
    return flat


def detect_field_bloat(events: list[dict], price_per_million: float | None = None) -> list[dict]:
    """Analyze sampled events for bloated / high-cardinality attributes.

    Each event: {message, attributes: {...possibly nested...}}.

    Flags a field when it is either:
      - large: avg serialized bytes >= _MIN_FIELD_BYTES and present in most events
      - unbounded: distinct-value ratio >= _HIGH_CARDINALITY_RATIO (near-unique)

    Returns list ranked by estimated impact (bytes * frequency, then cardinality).
    """
    n = len(events)
    if n < _MIN_EVENTS_FOR_BLOAT:
        return []

    present: Counter = Counter()
    total_bytes: dict[str, int] = defaultdict(int)
    distinct: dict[str, set] = defaultdict(set)

    for ev in events:
        attrs = ev.get("attributes", {}) or {}
        flat = _flatten_attrs(attrs)
        for key, val in flat.items():
            present[key] += 1
            sval = val if isinstance(val, str) else str(val)
            total_bytes[key] += len(sval.encode("utf-8", "ignore"))
            # cap distinct set memory
            if len(distinct[key]) < 5000:
                distinct[key].add(sval[:120])

    out: list[dict] = []
    for key, cnt in present.items():
        freq = cnt / n
        avg_bytes = total_bytes[key] / cnt if cnt else 0
        card = len(distinct[key])
        card_ratio = card / cnt if cnt else 0

        is_big = avg_bytes >= _MIN_FIELD_BYTES and freq >= 0.5
        # unbounded cardinality only matters for fields present in a meaningful
        # share of events (else a rare field with a few distinct values is noise).
        is_unbounded = (card_ratio >= _HIGH_CARDINALITY_RATIO and card >= 50
                        and freq >= 0.3)

        if not (is_big or is_unbounded):
            continue

        kind = "large_field" if is_big else "high_cardinality"
        # crude monthly-byte impact for context (per-event bytes * frequency)
        impact = avg_bytes * freq
        rec = "trim_fields" if is_big else "reduce_cardinality"
        why = (
            f"Field `{key}` averages {int(avg_bytes)} bytes and appears in "
            f"{freq*100:.0f}% of sampled events — trimming it via a log pipeline "
            f"remap/remove reduces ingested GB."
            if is_big else
            f"Field `{key}` has ~{card} distinct values across {cnt} events "
            f"({card_ratio*100:.0f}% unique) — near-unbounded cardinality bloats "
            f"indexing and any facet on it; drop or hash it."
        )
        out.append({
            "field_name": key,
            "kind": kind,
            "frequency": round(freq, 3),
            "avg_bytes": round(avg_bytes, 1),
            "cardinality": card,
            "cardinality_ratio": round(card_ratio, 3),
            "recommendation": rec,
            "why_safe": why,
            "impact": impact,
        })

    out.sort(key=lambda b: b["impact"], reverse=True)
    return out


# ===========================================================================
# Log content sampling — POST /api/v2/logs/events/search (cursor-paginated)
# ===========================================================================

_SEARCH_PATH = "/api/v2/logs/events/search"
_PAGE_LIMIT = 1000   # max page size Datadog allows for events search


def _extract_event(item: dict) -> dict:
    """Pull the fields we cluster on out of a raw events/search item.

    Returns {message, level, service, host, attributes}. No raw item retained.
    """
    attrs = item.get("attributes", {}) or {}
    inner = attrs.get("attributes", {}) or {}  # nested custom attributes
    return {
        "message": attrs.get("message", "") or "",
        "level": (attrs.get("status") or inner.get("status") or inner.get("level") or "").lower(),
        "service": attrs.get("service", "") or inner.get("service", "") or "",
        "host": attrs.get("host", "") or "",
        "attributes": inner,
    }


def sample_logs(
    query: str,
    frm: str,
    to: str,
    api_key: str,
    app_key: str,
    site: str,
    cap: int = 5000,
    post_fn=None,
) -> list[dict]:
    """Sample up to `cap` log events for `query`, cursor-paginated.

    Content is returned in-memory only; callers must not persist it.
    `post_fn` is injectable for tests (defaults to savings._post).

    Returns list of {message, level, service, host, attributes}.
    """
    import time as _time
    if post_fn is None:
        import savings
        post_fn = savings._post

    collected: list[dict] = []
    cursor: str | None = None
    pages = 0
    max_pages = max(1, math.ceil(cap / _PAGE_LIMIT)) + 2

    while len(collected) < cap and pages < max_pages:
        page: dict = {
            "filter": {"query": query, "from": frm, "to": to, "indexes": ["*"]},
            "sort": "-timestamp",
            "page": {"limit": min(_PAGE_LIMIT, cap - len(collected))},
        }
        if cursor:
            page["page"]["cursor"] = cursor
        try:
            resp = post_fn(_SEARCH_PATH, page, api_key, app_key, site)
        except dd_client.RateLimitError:
            _time.sleep(2)
            try:
                resp = post_fn(_SEARCH_PATH, page, api_key, app_key, site)
            except dd_client.DatadogError:
                break
        except dd_client.DatadogError:
            break

        data = resp.get("data", [])
        for item in data:
            collected.append(_extract_event(item))
        pages += 1

        cursor = resp.get("meta", {}).get("page", {}).get("after")
        if not cursor or not data:
            break

    return collected[:cap]


# ===========================================================================
# Log-based metric catalog — the team's own "what matters" list
# ===========================================================================

def fetch_metric_catalog(api_key: str, app_key: str, site: str, get_fn=None) -> list[str]:
    """Return the filter.query strings of all log-based metrics.

    These are patterns the team already meters -> we never blind-recommend
    dropping them. Best-effort; returns [] on error.
    """
    if get_fn is None:
        import savings
        get_fn = savings._get
    try:
        resp = get_fn("/api/v2/logs/config/metrics", {}, api_key, app_key, site)
    except dd_client.DatadogError:
        return []
    out: list[str] = []
    for m in resp.get("data", []):
        q = m.get("attributes", {}).get("filter", {}).get("query", "")
        if q:
            out.append(q)
    return out


# ===========================================================================
# Orchestrator — sample top services, mine, classify, rank
# ===========================================================================

_MAX_SERVICES_TO_SAMPLE = 6
_SAMPLE_PER_SERVICE = 1200
_MIN_TEMPLATE_COST = 5.0        # ignore templates below $5/mo
_SAMPLING_BUDGET_SECONDS = 40   # soft wall-clock cap for the whole content-sampling phase


def detect_pattern_opportunities(
    cost_map: list[dict],
    api_key: str,
    app_key: str,
    site: str,
    frm: str,
    to: str,
    price_per_million: float,
    metric_catalog: list[str] | None = None,
    target_index: str = "main",
    sample_fn=None,
) -> tuple[list[dict], int, list[dict]]:
    """For the top-cost services, sample logs, mine templates, classify, rank.

    Returns (opportunities, total_lines_examined, field_bloat).

    cost_map : output of savings.build_log_cost_map — rows with service/status/
               monthly_events. We aggregate to per-service monthly volume and
               sample the highest-volume services.
    """
    if sample_fn is None:
        sample_fn = sample_logs

    # Aggregate monthly events per service from the cost map.
    per_service: dict[str, float] = defaultdict(float)
    for row in cost_map:
        per_service[row.get("service", "unknown")] += float(row.get("monthly_events", 0))

    top_services = sorted(per_service.items(), key=lambda kv: kv[1], reverse=True)
    top_services = [(s, v) for s, v in top_services if s and s != "unknown"][:_MAX_SERVICES_TO_SAMPLE]

    import time as _time
    deadline = _time.time() + _SAMPLING_BUDGET_SECONDS

    opportunities: list[dict] = []
    lines_examined = 0
    bloat_events: list[dict] = []   # accumulate sampled events for field-bloat analysis

    for service, monthly_events in top_services:
        if _time.time() > deadline:
            break   # soft budget hit — proceed with what we have (fail-soft, never hang)
        # A single slow/broken service must never crash the whole scan.
        try:
            events = sample_fn(
                query=f"service:{service}", frm=frm, to=to,
                api_key=api_key, app_key=app_key, site=site, cap=_SAMPLE_PER_SERVICE,
            )
        except Exception:
            continue
        if not events:
            continue
        lines_examined += len(events)
        # keep a bounded slice per service for cross-service field-bloat analysis
        bloat_events.extend(events[:500])
        sample_size = len(events)
        messages = [e["message"] for e in events if e.get("message")]
        # dominant level per template comes from the events; approximate with the
        # modal level of the sample for classification hints.
        level_hint = Counter(e.get("level", "") for e in events).most_common(1)
        modal_level = level_hint[0][0] if level_hint else ""

        templates = mine_templates(messages)
        for t in templates:
            cls = classify_template(
                t["template"], level=modal_level, service=service,
                sample=t["sample_redacted"], metric_catalog=metric_catalog,
            )
            if not cls["is_noise"]:
                continue
            cost = estimate_template_cost(t["count"], sample_size, monthly_events, price_per_million)
            if cost["monthly_cost_usd"] < _MIN_TEMPLATE_COST:
                continue
            opp = build_pattern_opportunity(
                service=service,
                template=t["template"],
                sample_redacted=t["sample_redacted"],
                level=modal_level,
                template_count=t["count"],
                sample_size=sample_size,
                service_monthly_events=monthly_events,
                price_per_million=price_per_million,
                target_index=target_index,
                metric_catalog=metric_catalog,
                classification=cls,
            )
            opportunities.append(opp)

    opportunities.sort(key=lambda o: o["monthly_savings_usd"], reverse=True)
    field_bloat = detect_field_bloat(bloat_events, price_per_million=price_per_million)
    return opportunities, lines_examined, field_bloat
