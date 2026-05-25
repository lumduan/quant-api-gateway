# Portfolio Endpoints — `/api/v2/engines/portfolio/*`

Reference for the portfolio-engine routes that ship pre-formatted data for
OpenBB Workspace widgets. The canonical OpenAPI surface is generated at
runtime — this page documents shape contracts and consumer expectations.

---

## `GET /api/v2/engines/portfolio/metrics`

Returns the **latest** portfolio snapshot pre-formatted as an OpenBB Metric
widget array. The response shape follows
[the official Metric widget spec](https://docs.openbb.co/workspace/developers/widget-types/metric):
a top-level JSON array of `{label, value, delta}` objects. The widget renders
arrows and colors from the sign of `delta`; this payload carries pre-formatted
strings only.

### Request

```
GET /api/v2/engines/portfolio/metrics
Host: localhost:8000
X-API-Key: $INTERNAL_API_KEY   # not required for reads, but the openbb proxy forwards one
```

### Response — `200 OK`

```json
[
  {
    "label": "Daily Return",
    "value": "0.63%",
    "delta": "-0.12"
  },
  {
    "label": "Portfolio Drawdown",
    "value": "-4.22%",
    "delta": "-0.12"
  },
  {
    "label": "Total Portfolio Value",
    "value": "$998,142.71",
    "delta": "6234.10"
  }
]
```

### Field semantics

| Field | Format | Notes |
|---|---|---|
| `label` | Human-readable metric name | Fixed three labels in fixed order: `Daily Return`, `Portfolio Drawdown`, `Total Portfolio Value`. |
| `value` | Pre-formatted value cell | Percentages: `"0.63%"` / `"-4.22%"` (no leading `+`, no arrows). Currency: `"$998,142.71"` / `"-$1,234.50"`. When the underlying field is null (e.g. drawdown without equity curve data) the value is the literal string `"N/A"`. |
| `delta` | Plain signed number string | Day-over-day change vs the most recent strictly-earlier snapshot. Examples: `"0.12"`, `"-0.12"`, `"0.00"`. **No arrow, no unit, no thousands separator, no leading `+`.** Empty string `""` when no previous snapshot exists or either side of the comparison is null. |

The OpenBB Metric widget renders the arrow (`↑` / `↓`) and the color
(green / red / neutral) from the sign of `delta` — the data side stays
unit-only and unstyled.

### Delta computation

- **Daily Return delta**: `(current.weighted_daily_return - previous.weighted_daily_return) * 100`, quantized to two decimals.
- **Portfolio Drawdown delta**: same formula on `combined_drawdown`. Null if either side is null.
- **Total Portfolio Value delta**: `current.total_portfolio_value - previous.total_portfolio_value`, quantized to two decimals (no thousands separator on output).

The "previous snapshot" is the most recent row in `portfolio_snapshot` with
`time::date < current.snapshot_date` — not necessarily the calendar day
before, since snapshots are sparse (one per ingestion day).

### Caching

- Key: `portfolio_metrics:latest`
- TTL: `Settings.portfolio_snapshot_ttl_seconds` (default 3600 s, env-configurable)
- Cache write failures degrade gracefully — request still returns 200 with a warning log line.

### Error responses

| Status | Condition | Body |
|---|---|---|
| `404` | No snapshots in `portfolio_snapshot` | `{"detail": "No portfolio snapshots available"}` |
| `500` | Postgres unavailable | `{"detail": "Failed to query portfolio snapshot"}` |

---

## `GET /api/v2/engines/portfolio/metrics/{snapshot_date}`

Same shape as above for a specific historical date.

### Request

```
GET /api/v2/engines/portfolio/metrics/2026-05-22
```

`snapshot_date` is parsed as ISO-8601 (`YYYY-MM-DD`).

### Response

Identical JSON shape to the `/metrics` route, scoped to the requested date.

### Caching

- Key: `portfolio_metrics:{snapshot_date.isoformat()}` (e.g. `portfolio_metrics:2026-05-22`)
- TTL: same as latest endpoint

### Error responses

| Status | Condition | Body |
|---|---|---|
| `404` | No row for that date | `{"detail": "No portfolio snapshot for 2026-05-22"}` |
| `500` | Postgres unavailable | `{"detail": "Failed to query portfolio snapshot for 2026-05-22"}` |

---

## Consumer wiring

The `quant-openbb` extension exposes the same paths under its own router prefix:

```
GET /api/v2/engines/portfolio/metrics              → proxies to this endpoint
GET /api/v2/engines/portfolio/metrics/{date}       → proxies to this endpoint
```

The proxy forwards the gateway's `X-API-Key` automatically; consumers of the
openbb extension authenticate against `QUANT_OPENBB_INTERNAL_API_KEY` (see
[`quant-openbb/README.md`](../../../quant-openbb/README.md)).

### Widget configuration example

See [`quant-openbb/README.md` § "OpenBB Metric Widget Example"](../../../quant-openbb/README.md) for the corresponding `widgets.json` config.
