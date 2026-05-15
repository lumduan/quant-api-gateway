# Quickstart

**Available since:** v0.1.0

Get the gateway running in under 5 minutes. Assumes Docker and uv are installed.

---

## 1. Clone and install

```bash
git clone https://github.com/lumduan/quant-api-gateway.git
cd quant-api-gateway

uv sync --all-groups
cp .env.example .env
# edit .env — fill in real passwords and URLs
```

## 2. Start the infra stack

The gateway depends on `quant-infra-db` (PostgreSQL + MongoDB) on the shared Docker network `quant-network`.

```bash
# In quant-infra-db repo:
cd ../quant-infra-db && docker compose up -d
```

## 3. Start the gateway

```bash
# Back in quant-api-gateway:
docker compose up -d
docker compose ps
# NAME                STATUS
# quant-api-gateway   Up (healthy)
# quant-redis         Up (healthy)
```

## 4. Verify

```bash
# Health check
curl -s http://localhost:8000/health
# → {"status":"ok"}

# Swagger UI
open http://localhost:8000/docs
```

## 5. Ingest a daily report

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest/daily-report \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_internal_api_key_here" \
  -d '{
    "strategy_metadata": {
        "id": "csm-set-01",
        "type": "equity-long",
        "last_updated": "2026-05-15T14:00:00Z"
    },
    "performance_metrics": {
        "daily_pnl": "15000.50",
        "equity_curve": [{"date": "2026-05-15", "value": "1050000.00"}],
        "max_drawdown": "-0.063",
        "sharpe_ratio": "1.85"
    },
    "current_exposure": {
        "total_value": "1050000.00",
        "cash_balance": "50000.00",
        "positions_count": 5
    }
}'
# → {"status":"accepted","strategy_id":"csm-set-01","time":"2026-05-15T14:00:00+00:00"}
```

## 6. Read back

```bash
# Overall portfolio performance
curl -s http://localhost:8000/api/v1/overall-performance | python3 -m json.tool

# Strategy detail
curl -s http://localhost:8000/api/v1/strategies/csm-set-01 | python3 -m json.tool

# Date-range query
curl -s "http://localhost:8000/api/v1/strategies/csm-set-01/performance?from_date=2026-05-01&to_date=2026-05-31" | python3 -m json.tool
```

---

## What's next?

- [API Endpoints Reference](../reference/api/endpoints.md) — complete endpoint table
- [Architecture Overview](../architecture/system-overview.md) — how the layers fit together
- [Adding a Strategy](../guides/adding-strategy.md) — register a new strategy service
