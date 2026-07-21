"""
test_notify.py — Tests for alert delivery (email/Slack).

Strict TDD: RED tests first (all should fail initially), then implement.
Tests verify:
  - format_finding_alert masks JWT/AWS keys in template
  - send_email builds MIME message, calls injected smtp_factory
  - send_slack calls injected poster, returns bool
  - deliver routes to configured channels via injected fns
  - No real network calls in tests (all injectable)
  - No secrets in formatted output
"""

import json
import smtplib
from email.message import EmailMessage


class TestFormatFindingAlert:
    """format_finding_alert returns {subject, body} with masked template."""

    def test_format_finding_alert_structure(self):
        """Returns dict with subject and body keys."""
        from notify import format_finding_alert

        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "login user=<id> session=abc123",
            "disposition": "alert",
        }

        result = format_finding_alert(finding, "alert")

        assert isinstance(result, dict)
        assert "subject" in result
        assert "body" in result

    def test_format_finding_alert_includes_kind(self):
        """Alert body includes the finding kind."""
        from notify import format_finding_alert

        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "test",
            "disposition": "alert",
        }

        result = format_finding_alert(finding, "alert")

        assert "exclude" in result["body"].lower()

    def test_format_finding_alert_includes_cost(self):
        """Alert body includes monthly cost."""
        from notify import format_finding_alert

        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 1234.56,
            "confidence": "high",
            "template": "test",
            "disposition": "alert",
        }

        result = format_finding_alert(finding, "alert")

        assert "1234.56" in result["body"] or "1234" in result["body"]

    def test_format_finding_alert_masks_jwt(self):
        """Alert body masks JWT tokens in template."""
        from notify import format_finding_alert

        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"  # noqa
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": f"login token={jwt_token}",
            "disposition": "alert",
        }

        result = format_finding_alert(finding, "alert")

        # JWT should be masked
        assert "<JWT>" in result["body"]
        assert jwt_token not in result["body"]

    def test_format_finding_alert_masks_aws_key(self):
        """Alert body masks long opaque IDs (AWS keys, etc)."""
        from notify import format_finding_alert

        aws_key = "AKIAIOSFODNN7EXAMPLE"
        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": f"key={aws_key} user=test",
            "disposition": "alert",
        }

        result = format_finding_alert(finding, "alert")

        # Long key should be masked
        assert "<ID>" in result["body"]
        assert aws_key not in result["body"]

    def test_format_finding_alert_includes_disposition(self):
        """Alert body includes disposition (alert/auto/recommend)."""
        from notify import format_finding_alert

        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "test",
            "disposition": "alert",
        }

        result = format_finding_alert(finding, "alert")

        # Should include some reference to the action being taken
        body_lower = result["body"].lower() + result["subject"].lower()
        assert "alert" in body_lower or "finding" in body_lower

    def test_format_finding_alert_no_raw_secrets(self):
        """Alert output never contains unmasked long IDs or JWTs."""
        from notify import format_finding_alert

        finding = {
            "kind": "exclude",
            "monthly_cost_usd": 500.0,
            "confidence": "high",
            "template": "key=AKIAIOSFODNN7EXAMPLE jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.TJVA95OrM7E2cBab30RMHrHDcEfxjoYZgeFONFh7HgQ",  # noqa
            "disposition": "alert",
        }

        result = format_finding_alert(finding, "alert")
        full = result["subject"] + result["body"]

        # Should not have unmasked JWT or AWS key
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in full
        assert "AKIAIOSFODNN7EXAMPLE" not in full


class TestSendEmail:
    """send_email builds MIME message, calls smtp_factory, returns bool."""

    def test_send_email_returns_bool(self):
        """send_email returns True or False, never raises."""
        from notify import send_email

        fake_smtp = FakeSMTP()

        result = send_email(
            "Test Subject",
            "Test Body",
            "user@example.com",
            {"host": "smtp.test", "port": 465},
            smtp_factory=lambda: fake_smtp,
        )

        assert isinstance(result, bool)

    def test_send_email_no_to_addr_returns_false(self):
        """send_email returns False when to_addr is empty."""
        from notify import send_email

        result = send_email(
            "Test Subject",
            "Test Body",
            "",  # no to_addr
            {"host": "smtp.test", "port": 465},
            smtp_factory=lambda: FakeSMTP(),
        )

        assert result is False

    def test_send_email_no_smtp_cfg_returns_false(self):
        """send_email returns False when smtp_cfg is None."""
        from notify import send_email

        result = send_email(
            "Test Subject",
            "Test Body",
            "user@example.com",
            None,  # no config
            smtp_factory=lambda: FakeSMTP(),
        )

        assert result is False

    def test_send_email_calls_smtp_factory(self):
        """send_email calls smtp_factory to create SMTP connection."""
        from notify import send_email

        fake_smtp = FakeSMTP()

        send_email(
            "Test Subject",
            "Test Body",
            "user@example.com",
            {"host": "smtp.test", "port": 465},
            smtp_factory=lambda: fake_smtp,
        )

        assert fake_smtp.login_called
        assert fake_smtp.send_message_called
        assert fake_smtp.quit_called

    def test_send_email_calls_login(self):
        """send_email calls .login() on SMTP connection."""
        from notify import send_email

        fake_smtp = FakeSMTP()

        send_email(
            "Test Subject",
            "Test Body",
            "user@example.com",
            {"host": "smtp.test", "port": 465, "from_addr": "admin@test", "password": "pass"},
            smtp_factory=lambda: fake_smtp,
        )

        assert fake_smtp.login_called

    def test_send_email_calls_send_message(self):
        """send_email calls .send_message() exactly once."""
        from notify import send_email

        fake_smtp = FakeSMTP()

        send_email(
            "Test Subject",
            "Test Body",
            "user@example.com",
            {"host": "smtp.test", "port": 465, "from_addr": "admin@test", "password": "pass"},
            smtp_factory=lambda: fake_smtp,
        )

        assert fake_smtp.send_message_called
        assert fake_smtp.send_message_count == 1

    def test_send_email_calls_quit(self):
        """send_email calls .quit() to close connection."""
        from notify import send_email

        fake_smtp = FakeSMTP()

        send_email(
            "Test Subject",
            "Test Body",
            "user@example.com",
            {"host": "smtp.test", "port": 465, "from_addr": "admin@test", "password": "pass"},
            smtp_factory=lambda: fake_smtp,
        )

        assert fake_smtp.quit_called

    def test_send_email_exception_returns_false(self):
        """send_email returns False on exception, never raises."""
        from notify import send_email

        failing_smtp = FailingSMTP()

        result = send_email(
            "Test Subject",
            "Test Body",
            "user@example.com",
            {"host": "smtp.test", "port": 465, "from_addr": "admin@test", "password": "pass"},
            smtp_factory=lambda: failing_smtp,
        )

        assert result is False

    def test_send_email_returns_true_on_success(self):
        """send_email returns True when email sent successfully."""
        from notify import send_email

        fake_smtp = FakeSMTP()

        result = send_email(
            "Test Subject",
            "Test Body",
            "user@example.com",
            {"host": "smtp.test", "port": 465, "from_addr": "admin@test", "password": "pass"},
            smtp_factory=lambda: fake_smtp,
        )

        assert result is True


class TestSendSlack:
    """send_slack posts to webhook, returns bool."""

    def test_send_slack_returns_bool(self):
        """send_slack returns True or False, never raises."""
        from notify import send_slack

        def fake_poster(url, data):
            return FakeResponse(200)

        result = send_slack("https://hooks.slack.com/test", "Test message", poster=fake_poster)

        assert isinstance(result, bool)

    def test_send_slack_no_webhook_returns_false(self):
        """send_slack returns False when webhook_url is empty."""
        from notify import send_slack

        result = send_slack("", "Test message", poster=lambda u, d: FakeResponse(200))

        assert result is False

    def test_send_slack_calls_poster(self):
        """send_slack calls poster function with URL and data."""
        from notify import send_slack

        posted = []

        def fake_poster(url, data):
            posted.append((url, data))
            return FakeResponse(200)

        send_slack("https://hooks.slack.com/test", "Test message", poster=fake_poster)

        assert len(posted) > 0
        assert posted[0][0] == "https://hooks.slack.com/test"

    def test_send_slack_200_returns_true(self):
        """send_slack returns True on HTTP 200."""
        from notify import send_slack

        def fake_poster(url, data):
            return FakeResponse(200)

        result = send_slack("https://hooks.slack.com/test", "Test message", poster=fake_poster)

        assert result is True

    def test_send_slack_non_200_returns_false(self):
        """send_slack returns False on non-200 status."""
        from notify import send_slack

        def fake_poster(url, data):
            return FakeResponse(400)

        result = send_slack("https://hooks.slack.com/test", "Test message", poster=fake_poster)

        assert result is False

    def test_send_slack_exception_returns_false(self):
        """send_slack returns False on exception, never raises."""
        from notify import send_slack

        def failing_poster(url, data):
            raise Exception("Network error")

        result = send_slack("https://hooks.slack.com/test", "Test message", poster=failing_poster)

        assert result is False

    def test_send_slack_default_poster(self):
        """send_slack uses injectable default poster if none provided."""
        from notify import send_slack

        # Just verify it accepts the parameter and defaults work
        # (won't actually post in tests)
        def fake_poster(url, data):
            return FakeResponse(200)

        result = send_slack("https://hooks.slack.com/test", "Test", poster=fake_poster)

        assert result is True


class TestDeliver:
    """deliver routes alert to configured channels via injected fns."""

    def test_deliver_returns_list_of_channels(self):
        """deliver returns list of channels delivered to."""
        from notify import deliver

        alert = {"subject": "Test", "body": "Test body"}
        channels = {}

        result = deliver(alert, channels)

        assert isinstance(result, list)

    def test_deliver_empty_channels_returns_empty_list(self):
        """deliver returns empty list when no channels configured."""
        from notify import deliver

        alert = {"subject": "Test", "body": "Test body"}
        channels = {"email": "", "slack_webhook": ""}

        result = deliver(alert, channels)

        assert result == []

    def test_deliver_email_channel(self):
        """deliver calls email_fn when email channel configured."""
        from notify import deliver

        delivered = []

        def fake_email_fn(subject, body, to_addr, smtp_cfg):
            delivered.append(("email", to_addr))
            return True

        alert = {"subject": "Test", "body": "Test body"}
        channels = {"email": "user@example.com", "slack_webhook": ""}

        result = deliver(alert, channels, email_fn=fake_email_fn)

        assert "email" in result
        assert len(delivered) > 0

    def test_deliver_slack_channel(self):
        """deliver calls slack_fn when Slack channel configured."""
        from notify import deliver

        delivered = []

        def fake_slack_fn(webhook_url, text):
            delivered.append(("slack", webhook_url))
            return True

        alert = {"subject": "Test", "body": "Test body"}
        channels = {"email": "", "slack_webhook": "https://hooks.slack.com/test"}

        result = deliver(alert, channels, slack_fn=fake_slack_fn)

        assert "slack" in result
        assert len(delivered) > 0

    def test_deliver_both_channels(self):
        """deliver calls both email and Slack when both configured."""
        from notify import deliver

        email_called = []
        slack_called = []

        def fake_email_fn(subject, body, to_addr, smtp_cfg):
            email_called.append(True)
            return True

        def fake_slack_fn(webhook_url, text):
            slack_called.append(True)
            return True

        alert = {"subject": "Test", "body": "Test body"}
        channels = {
            "email": "user@example.com",
            "slack_webhook": "https://hooks.slack.com/test",
        }

        result = deliver(alert, channels, email_fn=fake_email_fn, slack_fn=fake_slack_fn)

        assert len(email_called) > 0
        assert len(slack_called) > 0
        assert "email" in result
        assert "slack" in result

    def test_deliver_skips_failed_channel(self):
        """deliver skips channel if function returns False."""
        from notify import deliver

        def fake_email_fn(subject, body, to_addr, smtp_cfg):
            return False  # failed

        alert = {"subject": "Test", "body": "Test body"}
        channels = {"email": "user@example.com", "slack_webhook": ""}

        result = deliver(alert, channels, email_fn=fake_email_fn)

        assert "email" not in result  # failed, so not in result

    def test_deliver_uses_default_fns(self):
        """deliver uses injected default fns if not provided."""
        from notify import deliver

        alert = {"subject": "Test", "body": "Test body"}
        channels = {}

        # Should not raise even without explicit email_fn/slack_fn
        result = deliver(alert, channels)

        assert isinstance(result, list)


# ===========================================================================
# Fake/Mock helpers
# ===========================================================================


class FakeSMTP:
    """Mock SMTP connection for testing."""

    def __init__(self):
        self.login_called = False
        self.send_message_called = False
        self.send_message_count = 0
        self.quit_called = False

    def login(self, user, password):
        self.login_called = True

    def send_message(self, message):
        self.send_message_called = True
        self.send_message_count += 1

    def quit(self):
        self.quit_called = True


class FailingSMTP:
    """Mock SMTP that raises exception."""

    def login(self, user, password):
        raise smtplib.SMTPException("Auth failed")

    def send_message(self, message):
        raise smtplib.SMTPException("Send failed")

    def quit(self):
        pass


class FakeResponse:
    """Mock HTTP response."""

    def __init__(self, status_code):
        self.status = status_code
