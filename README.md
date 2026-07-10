# ObservaBill — free Datadog bill breakdown

**Datadog bill shock?** Paste your Datadog API key + Application key, pick your site, and see exactly
which product is burning your budget — total, by-product (committed vs on-demand overage), projected
end-of-month, and (if you've enabled Usage Attribution) cost by team/service.

- **Read-only.** Needs `usage_read` + `billing_read` scopes only.
- **Your keys are never stored or logged.** They're used for the single request and discarded. See
  the code — this is open source.
- **Run it yourself** if you'd rather not paste keys into a hosted app (below).

## Run locally

Pure Python 3.11 standard library — no dependencies, no build step.

```bash
python3 app.py            # serves on http://localhost:8921
# then open http://localhost:8921  and click "Try with sample data"
```

## What it calls (all read-only)

- `GET /api/v2/usage/estimated_cost`, `historical_cost`, `projected_cost` — total + by-product,
  committed vs on-demand, month-over-month, projected end-of-month.
- `GET /api/v2/usage/monthly_cost_attribution` — per team/service/env, **only if** you've enabled
  Usage Attribution in Datadog (Plan & Usage → Usage Attribution). If not, you'll see a hint to turn it on.

Data has Datadog's usual ~48h delay. Requires a Datadog Pro/Enterprise plan (cost APIs aren't on Free/Trial).

## Create a read-only key (self-serve)

1. API key: Datadog → Organization Settings → API Keys → New Key.
2. Application key: Organization Settings → Application Keys → New Key (the creating user needs
   `usage_read` + `billing_read`).

## Paid (coming soon)

Weekly Slack/email alerts when a deploy spikes your bill + a monthly manager-ready report — $99/mo.

## License

MIT.
