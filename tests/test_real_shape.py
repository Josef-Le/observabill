"""Regression tests against the REAL Datadog logs-aggregate response shape.

Discovered by running against a live Datadog org: buckets are nested under
data.buckets[], the count is computes.c0, and the `status` facet is LOWERCASE
(e.g. "debug", not "DEBUG"). The detector was case-sensitive and returned $0 on
real data. These tests lock in the fix and the conservative status set.
"""
import savings


def test_exclusion_detects_lowercase_debug_real_shape():
    agg = {"meta": {}, "data": {"buckets": [
        {"computes": {"c0": 250_000_000}, "by": {"service": "aws", "status": "debug"}},
        {"computes": {"c0": 9_000_000},   "by": {"service": "api", "status": "200"}},
        {"computes": {"c0": 8_000_000},   "by": {"service": "api", "status": "info"}},
        {"computes": {"c0": 7_000_000},   "by": {"service": "api", "status": "error"}},
    ]}}
    idx = {"indexes": [{"name": "main"}]}
    opp = savings.detect_exclusion_candidates(agg, idx)
    assert opp is not None
    assert opp["monthly_savings_usd"] > 0
    labels = " ".join(e["label"] for e in opp["evidence"]).lower()
    assert "aws [debug]" in labels          # lowercase debug IS flagged
    assert "[info]" not in labels           # info is NOT flagged (conservative)
    assert "[error]" not in labels          # error is NOT flagged


def test_noise_status_is_case_insensitive_and_conservative():
    assert savings._is_noise_status("debug")
    assert savings._is_noise_status("DEBUG")
    assert savings._is_noise_status("200")
    assert savings._is_noise_status("204")
    assert not savings._is_noise_status("info")
    assert not savings._is_noise_status("error")
    assert not savings._is_noise_status("warn")
