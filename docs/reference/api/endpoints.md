# API Endpoints Reference

**Module:** `src.api.v1`
**Available since:** v0.1.0

All 11 REST endpoints. The gateway listens on port `8000` (container) / `${API_GATEWAY_HOST_PORT}` (host).

---

## Endpoint Table

| Method | Path | Auth | Description | Cache TTL | Response Model |
|--------|------|------|-------------|-----------|----------------|
| GET | `/health` | — | Liveness probe | — | `{"status": "ok"}` |
| POST | `/api/v1/ingest/daily-report` | `X-API-Key` | Ingest daily performance | — | `{"status": "accepted", ...}` |
| GET | `/api/v1/overall-performance` | — | Aggregated portfolio performance | 300 s | `OverallPerformanceResponse` |
| GET | `/api/v1/strategies` | — | List active strategies | — | `list[StrategyConfig]` |
| GET | `/api/v1/strategies/{id}` | — | Single strategy detail | — | `StrategyConfig` |
| GET | `/api/v1/strategies/{id}/performance` | — | Latest or date-range performance | 300 s (latest only) | `StrategyPerformanceResponse \| list[StrategyPerformanceResponse]` |
| GET | `/api/v1/strategies/{id}/equity-curve` | — | Full equity curve | — | `list[EquityPoint]` |
| GET | `/api/v1/portfolio/snapshot` | — | Latest portfolio snapshot | 3600 s | `PortfolioSnapshotResponse` |
| GET | `/api/v1/portfolio/snapshot/{date}` | — | Snapshot for date (YYYY-MM-DD) | 3600 s | `PortfolioSnapshotResponse` |
| GET | `/api/v1/portfolio/equity-curve` | — | Merged portfolio equity curve | — | `list[EquityPoint]` |
| POST | `/api/v1/admin/cache/flush` | `X-API-Key` | Flush all gateway cache keys | — | `{"status": "flushed", ...}` |

---

## Common Query Parameters

### `GET /api/v1/strategies/{id}/performance`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `from_date` | `date` (YYYY-MM-DD) | `None` | Start date for range query. Requires `to_date`. |
| `to_date` | `date` (YYYY-MM-DD) | `None` | End date for range query. Requires `from_date`. |

When both `from_date` and `to_date` are provided:
- Returns `list[StrategyPerformanceResponse]` ordered by time ascending
- No caching
- Empty list when no rows in range (not 404)

When only ONE is provided:
- Returns `422` with detail `"Both from_date and to_date are required for range queries"`

When NEITHER is provided:
- Returns latest `StrategyPerformanceResponse` (cached, TTL 300 s)

### `GET /api/v1/portfolio/equity-curve`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `normalize` | `bool` | `true` | Normalize each input curve to base 100 before merging. Set `false` for raw cumulative values. |

---

## Error Responses

| Status | When | Body |
|--------|------|------|
| `403` | Missing or wrong `X-API-Key` | `{"detail": "Invalid API key"}` |
| `404` | Unknown strategy ID or no data | `{"detail": "Strategy 'xyz' not found"}` |
| `422` | Malformed JSON or validation failure | Pydantic validation detail |
| `422` | Partial date-range params | `{"detail": "Both from_date and to_date are required..."}` |
| `500` | Database/Redis failure | `{"detail": "Failed to compute..."}` |

---

## See Also

- [Ingest Endpoint](ingest.md) — full request/response specification
- [Performance Endpoints](performance.md) — overall and strategy performance
- [Portfolio Endpoints](portfolio.md) — snapshots and equity curves
- [Strategy Payload Schema](../schemas/strategy-payload.md) — input JSON contract
- [Gateway Response Schemas](../schemas/gateway.md) — output model reference
