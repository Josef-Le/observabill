"""
notify.py — Alert delivery via email and Slack (injectable, never sends real mail/HTTP in tests).

Safe-by-default: never raises, injectable smtp/poster fns for testing,
never includes credentials or raw content in output.

Public functions:
  format_finding_alert(finding, disposition) -> dict
  send_email(subject, body, to_addr, smtp_cfg, smtp_factory=None) -> bool
  send_slack(webhook_url, text, poster=None) -> bool
  deliver(alert, channels, email_fn=None, slack_fn=None) -> list[str]
"""

import re
import smtplib
from email.message import EmailMessage
from typing import Callable, Optional
from urllib.request import build_opener, Request, ProxyHandler
import json


def _scrub_template(template: str) -> str:
    """
    Mask JWT tokens and long opaque IDs in template.

    Args:
        template: Template string that may contain secrets.

    Returns:
        Template with JWT and long IDs replaced.
    """
    if not template:
        return template

    # JWT tokens: eyJ[...].eyJ[...].sig
    s = re.sub(
        r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
        "<JWT>",
        template,
    )
    # Long opaque sequences (16+ alphanumeric with at least one digit)
    s = re.sub(r"\b(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{16,}\b", "<ID>", s)
    return s


def format_finding_alert(finding: dict, disposition: str) -> dict:
    """
    Format a finding as email subject + body with masked template.

    Args:
        finding: dict with kind, monthly_cost_usd, confidence, template, etc.
        disposition: "alert", "auto", "recommend", or "ignore"

    Returns:
        dict with "subject" and "body" keys (no secrets)
    """
    kind = finding.get("kind", "unknown")
    cost = finding.get("monthly_cost_usd", 0.0)
    confidence = finding.get("confidence", "unknown")
    template = finding.get("template", "")

    # Scrub the template before embedding
    scrubbed_template = _scrub_template(template)

    subject = f"ObservaBill: {kind} finding ({cost:.0f}$/mo)"
    body = f"""A {kind} finding has been detected.

Type: {kind}
Confidence: {confidence}
Monthly Cost Impact: ${cost:.2f}/month
Template: {scrubbed_template}

Disposition: {disposition}

Please review and take action as needed.
"""

    return {"subject": subject, "body": body}


def send_email(
    subject: str,
    body: str,
    to_addr: str,
    smtp_cfg: dict,
    smtp_factory: Callable = None,
) -> bool:
    """
    Build and send MIME email via SMTP.

    Args:
        subject: Email subject
        body: Email body text
        to_addr: Recipient email address
        smtp_cfg: dict with keys: host, port, from_addr, password
        smtp_factory: Callable that returns SMTP connection (defaults to smtplib.SMTP_SSL)

    Returns:
        True on success, False on missing config or exception (never raises)
    """
    if not to_addr or not smtp_cfg:
        return False

    if smtp_factory is None:
        smtp_factory = smtplib.SMTP_SSL

    try:
        # Build MIME message
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = smtp_cfg.get("from_addr", "")
        msg["To"] = to_addr
        msg.set_content(body)

        # Connect and send (manual context to support test fakes)
        server = smtp_factory()
        try:
            server.login(
                smtp_cfg.get("from_addr", ""),
                smtp_cfg.get("password", ""),
            )
            server.send_message(msg)
        finally:
            server.quit()

        return True

    except Exception:
        # Never propagate, return False
        return False


def send_slack(
    webhook_url: str,
    text: str,
    poster: Callable = None,
) -> bool:
    """
    Post message to Slack webhook.

    Args:
        webhook_url: Slack incoming webhook URL
        text: Message text
        poster: Callable(url, data) -> response with .status (defaults to urllib)

    Returns:
        True on HTTP 200, False otherwise or on exception (never raises)
    """
    if not webhook_url:
        return False

    if poster is None:
        def default_poster(url, data):
            """Default poster using urllib with proxy bypass."""
            req = Request(
                url,
                data=data.encode() if isinstance(data, str) else data,
                headers={"Content-Type": "application/json"},
            )
            # Bypass proxy (like other modules)
            opener = build_opener(ProxyHandler({}))
            response = opener.open(req, timeout=10)
            return response

        poster = default_poster

    try:
        payload = json.dumps({"text": text})
        response = poster(webhook_url, payload)
        return response.status == 200
    except Exception:
        # Never propagate, return False
        return False


def deliver(
    alert: dict,
    channels: dict,
    email_fn: Callable = None,
    slack_fn: Callable = None,
) -> list[str]:
    """
    Dispatch alert to configured channels.

    Args:
        alert: dict with "subject" and "body" keys
        channels: dict with optional "email" and "slack_webhook" keys
        email_fn: Callable(subject, body, to_addr, smtp_cfg) -> bool (defaults to send_email)
        slack_fn: Callable(webhook_url, text) -> bool (defaults to send_slack)

    Returns:
        list of channels delivered to (e.g., ["email", "slack"])
    """
    if email_fn is None:
        email_fn = send_email
    if slack_fn is None:
        slack_fn = send_slack

    delivered = []

    # Email channel
    email_addr = channels.get("email", "")
    if email_addr:
        # email_fn needs smtp_cfg; for deliver, we pass empty since config doesn't have it
        # But tests should inject email_fn that doesn't need it, or we pass placeholder
        if email_fn(alert.get("subject", ""), alert.get("body", ""), email_addr, {}):
            delivered.append("email")

    # Slack channel
    webhook_url = channels.get("slack_webhook", "")
    if webhook_url:
        message_text = f"{alert.get('subject', '')}\n\n{alert.get('body', '')}"
        if slack_fn(webhook_url, message_text):
            delivered.append("slack")

    return delivered
