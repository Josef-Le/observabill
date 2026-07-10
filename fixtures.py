"""
ObservaBill Savings Scanner - Realistic Sample Data & API Fixtures

Pure Python stdlib; no external imports. Used by sample mode, tests, and UI dev.
All data structures follow explicit SavingsOpportunity and ScanResult contracts.
"""

# ============================================================================
# DATA CONTRACTS (reference implementation)
# ============================================================================
# SavingsOpportunity = dict with keys:
#   id: str
#   lever: str ("exclusion_filter" | "logs_to_metrics" | "high_cardinality_metric" | "index_quota")
#   category: str ("logs" | "metrics")
#   title: str
#   summary: str
#   monthly_savings_usd: float
#   savings_pct: str
#   effort: str ("low" | "medium" | "high")
#   confidence: str ("high" | "medium" | "low")
#   evidence: list of {label: str, volume: str, cost_usd: float}
#   generated_config: {endpoint: str, verb: str, payload: dict}
#   needs_write_scope: bool
#
# ScanResult = dict with keys:
#   total_monthly_waste_usd: float
#   currency: "USD"
#   region: str
#   opportunities: list[SavingsOpportunity] (sorted desc by monthly_savings_usd)
#   sparkline: list[float] (30 daily points)
#   notes: list[str]


# ============================================================================
# SAMPLE SCAN RESULT (6 opportunities, ~$3,500/mo total waste)
# ============================================================================

SAMPLE_SCAN = {
    "total_monthly_waste_usd": 3500.0,
    "currency": "USD",
    "region": "us-east-1",
    "price_source": "derived",
    "scope_check": {
        "logs_read":    True,
        "metrics_read": True,
        "billing_read": True,
        "usage_read":   True,
        "missing": [],
        "unlocks": {},
    },
    "opportunities": [
        # 1. logs_to_metrics: CDN/LB 2xx access logs -> count metric (~$1,850/mo)
        {
            "id": "opp-001-cdn-access-logs",
            "lever": "logs_to_metrics",
            "category": "logs",
            "title": "Archive CDN/LB 2xx access logs to metrics",
            "summary": "HTTP 200 access logs from CDN and load balancers are high-volume, low-value. Convert to a count metric tracking request volume by service, method, endpoint.",
            "monthly_savings_usd": 1850.0,
            "savings_pct": "59.7%",
            "effort": "low",
            "confidence": "high",
            "evidence": [
                {
                    "label": "cdn-prod (HTTPS GETs)",
                    "volume": "18.5M events/day",
                    "cost_usd": 945.0,
                },
                {
                    "label": "load-balancer-us (ALB access)",
                    "volume": "8.2M events/day",
                    "cost_usd": 420.0,
                },
                {
                    "label": "api-gateway-prod (pass-through 2xx)",
                    "volume": "5.1M events/day",
                    "cost_usd": 260.0,
                },
                {
                    "label": "cdn-edge-cache (cache-hit logs)",
                    "volume": "3.8M events/day",
                    "cost_usd": 225.0,
                },
            ],
            "generated_config": {
                "endpoint": "/api/v1/logs-metrics",
                "verb": "POST",
                "payload": {
                    "name": "http.requests.2xx_total",
                    "filter": {
                        "query": "source:(cdn-prod OR load-balancer-us OR api-gateway-prod OR cdn-edge-cache) status:200"
                    },
                    "group_by": ["service", "method", "http.url_details.path"],
                },
            },
            "needs_write_scope": True,
            "detection_query": "POST /api/v2/logs/analytics/aggregate\n{\"compute\": [{\"aggregation\": \"count\", \"type\": \"total\"}], \"filter\": {\"from\": \"now-30d\", \"query\": \"source:(cdn-prod OR load-balancer-us OR api-gateway-prod OR cdn-edge-cache) status:200\"}, \"group_by\": [{\"facet\": \"service\", \"limit\": 10}]}",
            "why": "HTTP 200 responses from CDN and load-balancers carry no actionable signal — they confirm success, which is already tracked by error-rate monitors. These 4 sources collectively account for 35.6M events/day at $945+$420+$260+$225 = $1,850/mo. Converting them to a count metric preserves trend visibility at <$5/mo.",
        },
        # 2. high_cardinality_metric: user_id tag explosion (~$780/mo)
        {
            "id": "opp-002-user-id-cardinality",
            "lever": "high_cardinality_metric",
            "category": "metrics",
            "title": "Reduce cardinality on payment-service custom metric",
            "summary": "payment.transaction.latency metric is tagged by user_id, creating unbounded cardinality (~450k unique users/day). Recommend tagging by user_cohort (10 buckets) instead.",
            "monthly_savings_usd": 780.0,
            "savings_pct": "25.1%",
            "effort": "medium",
            "confidence": "high",
            "evidence": [
                {
                    "label": "payment.transaction.latency unique user_ids",
                    "volume": "450k uniques/day",
                    "cost_usd": 540.0,
                },
                {
                    "label": "Spillover storage (DTS overage)",
                    "volume": "High-cardinality spillover",
                    "cost_usd": 240.0,
                },
            ],
            "generated_config": {
                "endpoint": "/api/v1/metrics/{metric_id}/tag_configurations",
                "verb": "PATCH",
                "payload": {
                    "metric_id": "payment.transaction.latency",
                    "tags": {
                        "user_id": {"aggregations": ["percentile_approx(0.99)"]},
                        "user_cohort": {
                            "aggregations": ["count", "percentile_approx(0.99)"]
                        },
                    },
                    "exclude_tags_mode": True,
                    "excluded_tags": ["user_id"],
                },
            },
            "needs_write_scope": True,
            "detection_query": "GET /api/v1/metrics/payment.transaction.latency/tags\n# Returns tag keys and cardinality estimates for each.\n# Flag: any tag_name with cardinality > 50,000 on a gauge metric.\nGET /api/v1/metrics/summary?metric_names=payment.transaction.latency",
            "why": "The 'user_id' tag on payment.transaction.latency has ~450,000 unique values per day (one timeseries per user). Each unique tag combination is billed as an independent custom metric timeseries. Collapsing user_id into 10 user_cohort buckets (e.g. by spend tier) preserves p99 latency visibility for alerting while eliminating 99.9% of cardinality.",
        },
        # 3. exclusion_filter: health-check + kube-probe logs (~$420/mo)
        {
            "id": "opp-003-healthcheck-logs",
            "lever": "exclusion_filter",
            "category": "logs",
            "title": "Exclude health-check and kubelet-probe logs",
            "summary": "Kubernetes liveness/readiness probes and application health checks generate repetitive, low-value logs at high volume. Exclude from indexed logs.",
            "monthly_savings_usd": 420.0,
            "savings_pct": "13.5%",
            "effort": "low",
            "confidence": "high",
            "evidence": [
                {
                    "label": "/health endpoint GET requests",
                    "volume": "12.3M events/day",
                    "cost_usd": 245.0,
                },
                {
                    "label": "kubelet probe logs (readiness/liveness)",
                    "volume": "4.8M events/day",
                    "cost_usd": 175.0,
                },
            ],
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "name": "main",
                    "exclusion_filters": [
                        {
                            "name": "exclude-health-checks",
                            "filter": "http.method:GET (path:/health OR path:/healthz OR path:/ready)",
                        },
                        {
                            "name": "exclude-kubelet-probes",
                            "filter": "source:kubelet (probe:readiness OR probe:liveness)",
                        },
                    ],
                },
            },
            "needs_write_scope": True,
            "detection_query": "POST /api/v2/logs/analytics/aggregate\n{\"compute\": [{\"aggregation\": \"count\", \"type\": \"total\"}], \"filter\": {\"from\": \"now-30d\", \"query\": \"http.method:GET (path:/health OR path:/healthz OR path:/ready)\"}, \"group_by\": [{\"facet\": \"path\", \"limit\": 5}]}",
            "why": "Kubernetes liveness and readiness probes fire every 10–30 seconds per pod. With hundreds of pods in production, this generates 12.3M+ health-check GET requests per day in the log index. These logs are structurally identical (always 200 OK for healthy pods) and carry zero diagnostic value — alerting on failed health checks is better handled via Kubernetes events, not log-based monitors.",
        },
        # 4. exclusion_filter: DEBUG logs left on in production (~$260/mo)
        {
            "id": "opp-004-debug-logs",
            "lever": "exclusion_filter",
            "category": "logs",
            "title": "Disable DEBUG level logs in production",
            "summary": "DEBUG-level logs from worker and scheduler services are enabled in production and represent noise. Recommend disabling in prod, keeping only in staging.",
            "monthly_savings_usd": 260.0,
            "savings_pct": "8.4%",
            "effort": "low",
            "confidence": "medium",
            "evidence": [
                {
                    "label": "worker-service DEBUG logs",
                    "volume": "6.7M events/day",
                    "cost_usd": 155.0,
                },
                {
                    "label": "scheduler DEBUG logs",
                    "volume": "2.9M events/day",
                    "cost_usd": 105.0,
                },
            ],
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PUT",
                "payload": {
                    "name": "main",
                    "exclusion_filters": [
                        {
                            "name": "exclude-debug-worker-prod",
                            "filter": "source:worker-service level:DEBUG env:production",
                        },
                        {
                            "name": "exclude-debug-scheduler-prod",
                            "filter": "source:scheduler level:DEBUG env:production",
                        },
                    ],
                },
            },
            "needs_write_scope": True,
        },
        # 5. logs_to_metrics: payment-service debug events (~$190/mo)
        {
            "id": "opp-005-payment-debug-metrics",
            "lever": "logs_to_metrics",
            "category": "logs",
            "title": "Convert payment-service debug events to gauge metric",
            "summary": "payment-service emits debug-level events for transaction initiation and completion. These can be tracked as a gauge metric instead of storing raw logs.",
            "monthly_savings_usd": 190.0,
            "savings_pct": "6.1%",
            "effort": "low",
            "confidence": "medium",
            "evidence": [
                {
                    "label": "payment.transaction.initiated (debug)",
                    "volume": "2.3M events/day",
                    "cost_usd": 110.0,
                },
                {
                    "label": "payment.transaction.completed (debug)",
                    "volume": "1.8M events/day",
                    "cost_usd": 80.0,
                },
            ],
            "generated_config": {
                "endpoint": "/api/v1/logs-metrics",
                "verb": "POST",
                "payload": {
                    "name": "payment.transaction.gauge",
                    "filter": {
                        "query": "source:payment-service (event:transaction.initiated OR event:transaction.completed) level:DEBUG"
                    },
                    "group_by": ["event", "status"],
                },
            },
            "needs_write_scope": True,
        },
        # 6. index_quota: overage prevention (flagging spike, $0 direct savings)
        {
            "id": "opp-006-index-quota-spike",
            "lever": "index_quota",
            "category": "logs",
            "title": "Monitor and cap index quota spike",
            "summary": "Main index is approaching retention quota (88% full). A spike in events/day is trending upward. Recommend daily monitoring and soft quota alert at 75%.",
            "monthly_savings_usd": 0.0,
            "savings_pct": "0%",
            "effort": "low",
            "confidence": "high",
            "evidence": [
                {
                    "label": "Main index current size",
                    "volume": "88% of 500GB quota",
                    "cost_usd": 0.0,
                },
                {
                    "label": "7-day trend (daily events/GB ingested)",
                    "volume": "+8% week-over-week",
                    "cost_usd": 0.0,
                },
            ],
            "generated_config": {
                "endpoint": "/api/v1/logs/config/indexes/main",
                "verb": "PATCH",
                "payload": {
                    "name": "main",
                    "daily_limit": 500,
                    "retention_days": 30,
                    "flexible_retention": True,
                    "alert_threshold_pct": 75,
                },
            },
            "needs_write_scope": True,
        },
    ],
    "sparkline": [
        # 30-day daily ingestion trend (events), trending up with spike near end
        2150.0,  # Day 1
        2155.0,  # Day 2
        2160.0,  # Day 3
        2175.0,  # Day 4
        2180.0,  # Day 5
        2165.0,  # Day 6
        2170.0,  # Day 7
        2185.0,  # Day 8
        2190.0,  # Day 9
        2195.0,  # Day 10
        2210.0,  # Day 11
        2215.0,  # Day 12
        2220.0,  # Day 13
        2225.0,  # Day 14
        2235.0,  # Day 15
        2240.0,  # Day 16
        2250.0,  # Day 17
        2260.0,  # Day 18
        2270.0,  # Day 19
        2280.0,  # Day 20
        2290.0,  # Day 21
        2295.0,  # Day 22
        2310.0,  # Day 23
        2330.0,  # Day 24
        2350.0,  # Day 25
        2375.0,  # Day 26
        2395.0,  # Day 27
        2420.0,  # Day 28
        2500.0,  # Day 29 (spike)
        2480.0,  # Day 30 (cooling after spike)
    ],
    "notes": [
        "Top opportunity: archive CDN/LB 2xx logs (low risk, high confidence). Implement logs-to-metrics conversion and exclusion filter in parallel.",
        "Monitor index quota closely: main index trending toward retention limits. Consider tiered retention (hot/cold storage) after quota reduction.",
    ],
}


# ============================================================================
# DATADOG API RESPONSE FIXTURES (real shapes)
# ============================================================================


# Logs aggregate response: services by status, high-volume 2xx requests
LOGS_AGGREGATE_RESP = {
    "data": {
        "buckets": [
            {
                "by": {"service": "cdn-prod", "status": "200"},
                "computes": {"_sample_bucket_count": 18500000},
            },
            {
                "by": {"service": "load-balancer-us", "status": "200"},
                "computes": {"_sample_bucket_count": 8200000},
            },
            {
                "by": {"service": "api-gateway-prod", "status": "200"},
                "computes": {"_sample_bucket_count": 5100000},
            },
            {
                "by": {"service": "cdn-edge-cache", "status": "200"},
                "computes": {"_sample_bucket_count": 3800000},
            },
            {
                "by": {"service": "api-gateway-prod", "status": "401"},
                "computes": {"_sample_bucket_count": 450000},
            },
            {
                "by": {"service": "worker-service", "level": "DEBUG"},
                "computes": {"_sample_bucket_count": 6700000},
            },
            {
                "by": {"service": "scheduler", "level": "DEBUG"},
                "computes": {"_sample_bucket_count": 2900000},
            },
            {
                "by": {"service": "payment-service", "event": "transaction.initiated"},
                "computes": {"_sample_bucket_count": 2300000},
            },
        ],
        "meta": {
            "page": {"after": None},
            "status": "done",
        },
    },
}


# Metrics response: payment.transaction.latency with exploding user_id cardinality
METRICS_VOLUMES_RESP = {
    "data": [
        {
            "attributes": {
                "archived": False,
                "description": "Payment transaction latency tracked per user",
                "distinct_count": 450000,
                "metric_type": "gauge",
                "tags": [
                    "env:production",
                    "service:payment-service",
                    "user_id",
                    "endpoint",
                    "status",
                ],
                "unit": {
                    "family": "time",
                    "name": "millisecond",
                    "short_name": "ms",
                },
            },
            "id": "payment.transaction.latency",
            "type": "metrics",
        }
    ],
    "included": [
        {
            "attributes": {
                "cardinality": 450000,
                "percentile_approx": 1250,
                "tag_name": "user_id",
            },
            "id": "payment.transaction.latency:user_id",
            "type": "metric_tag_configuration",
        },
        {
            "attributes": {
                "cardinality": 12,
                "percentile_approx": 125,
                "tag_name": "endpoint",
            },
            "id": "payment.transaction.latency:endpoint",
            "type": "metric_tag_configuration",
        },
    ],
}


# Usage/ingestion response: 30-day daily log volumes (matches sparkline)
USAGE_LOGS_RESP = {
    "usage": [
        {
            "date": "2025-06-10",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2150000,
            "logs_ingested_bytes": 5420000000,
        },
        {
            "date": "2025-06-11",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2155000,
            "logs_ingested_bytes": 5430000000,
        },
        {
            "date": "2025-06-12",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2160000,
            "logs_ingested_bytes": 5440000000,
        },
        {
            "date": "2025-06-13",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2175000,
            "logs_ingested_bytes": 5470000000,
        },
        {
            "date": "2025-06-14",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2180000,
            "logs_ingested_bytes": 5480000000,
        },
        {
            "date": "2025-06-15",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2165000,
            "logs_ingested_bytes": 5450000000,
        },
        {
            "date": "2025-06-16",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2170000,
            "logs_ingested_bytes": 5460000000,
        },
        {
            "date": "2025-06-17",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2185000,
            "logs_ingested_bytes": 5490000000,
        },
        {
            "date": "2025-06-18",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2190000,
            "logs_ingested_bytes": 5500000000,
        },
        {
            "date": "2025-06-19",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2195000,
            "logs_ingested_bytes": 5510000000,
        },
        {
            "date": "2025-06-20",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2210000,
            "logs_ingested_bytes": 5540000000,
        },
        {
            "date": "2025-06-21",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2215000,
            "logs_ingested_bytes": 5550000000,
        },
        {
            "date": "2025-06-22",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2220000,
            "logs_ingested_bytes": 5560000000,
        },
        {
            "date": "2025-06-23",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2225000,
            "logs_ingested_bytes": 5570000000,
        },
        {
            "date": "2025-06-24",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2235000,
            "logs_ingested_bytes": 5590000000,
        },
        {
            "date": "2025-06-25",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2240000,
            "logs_ingested_bytes": 5600000000,
        },
        {
            "date": "2025-06-26",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2250000,
            "logs_ingested_bytes": 5620000000,
        },
        {
            "date": "2025-06-27",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2260000,
            "logs_ingested_bytes": 5640000000,
        },
        {
            "date": "2025-06-28",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2270000,
            "logs_ingested_bytes": 5660000000,
        },
        {
            "date": "2025-06-29",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2280000,
            "logs_ingested_bytes": 5680000000,
        },
        {
            "date": "2025-06-30",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2290000,
            "logs_ingested_bytes": 5700000000,
        },
        {
            "date": "2025-07-01",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2295000,
            "logs_ingested_bytes": 5710000000,
        },
        {
            "date": "2025-07-02",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2310000,
            "logs_ingested_bytes": 5740000000,
        },
        {
            "date": "2025-07-03",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2330000,
            "logs_ingested_bytes": 5780000000,
        },
        {
            "date": "2025-07-04",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2350000,
            "logs_ingested_bytes": 5820000000,
        },
        {
            "date": "2025-07-05",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2375000,
            "logs_ingested_bytes": 5870000000,
        },
        {
            "date": "2025-07-06",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2395000,
            "logs_ingested_bytes": 5910000000,
        },
        {
            "date": "2025-07-07",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2420000,
            "logs_ingested_bytes": 5960000000,
        },
        {
            "date": "2025-07-08",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2500000,
            "logs_ingested_bytes": 6130000000,
        },
        {
            "date": "2025-07-09",
            "org_name": "revenue-staging",
            "public_id": "org123456",
            "logs_indexed_events": 2480000,
            "logs_ingested_bytes": 6090000000,
        },
    ]
}


# Indexes configuration response: main index with existing exclusion filters
INDEXES_RESP = {
    "indexes": [
        {
            "name": "main",
            "num_retention_days": 30,
            "daily_limit": 500,
            "is_rate_limited": False,
            "notification_threshold_pct": None,
            "exclusion_filters": [
                {
                    "name": "exclude-synthetics",
                    "filter": "source:synthetics",
                    "num_matching_logs": 1200000,
                }
            ],
        },
        {
            "name": "sensitive-data",
            "num_retention_days": 90,
            "daily_limit": 100,
            "is_rate_limited": False,
            "notification_threshold_pct": None,
            "exclusion_filters": [],
        },
    ]
}


# ============================================================================
# INTERNAL VALIDATION (data consistency checks)
# ============================================================================


def _validate_sample_scan():
    """Validate that SAMPLE_SCAN is internally consistent."""
    total_evidence_cost = 0.0
    total_opportunities = 0.0

    for opp in SAMPLE_SCAN["opportunities"]:
        opp_cost = sum(ev["cost_usd"] for ev in opp["evidence"])
        total_evidence_cost += opp_cost
        total_opportunities += opp["monthly_savings_usd"]

        # Evidence should roughly sum to opportunity savings (within 10% for rounding)
        if opp["monthly_savings_usd"] > 0:
            ratio = opp_cost / opp["monthly_savings_usd"]
            assert (
                0.9 <= ratio <= 1.1
            ), f"{opp['id']}: evidence cost ${opp_cost:.0f} doesn't match savings ${opp['monthly_savings_usd']:.0f}"

    # Total opportunities should match scan total
    assert (
        abs(total_opportunities - SAMPLE_SCAN["total_monthly_waste_usd"]) < 1.0
    ), f"Opportunities sum ${total_opportunities:.0f} != scan total ${SAMPLE_SCAN['total_monthly_waste_usd']:.0f}"

    # Sparkline should be 30 points
    assert (
        len(SAMPLE_SCAN["sparkline"]) == 30
    ), f"Sparkline has {len(SAMPLE_SCAN['sparkline'])} points, expected 30"

    # Opportunities should be sorted descending by monthly_savings_usd
    savings = [opp["monthly_savings_usd"] for opp in SAMPLE_SCAN["opportunities"]]
    assert savings == sorted(savings, reverse=True), "Opportunities not sorted by savings (descending)"


_validate_sample_scan()
