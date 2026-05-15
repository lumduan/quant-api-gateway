# Settings Reference

**Module:** `src.config`
**Available since:** v0.1.0

Pydantic v2 Settings model. All values come from environment variables (or `.env` file). Access via the cached `get_settings()` function.

---

## Import

```python
from src.config import get_settings

settings = get_settings()
print(settings.log_level)    # → "INFO"
print(settings.redis_url)     # → "redis://quant-redis:6379/0"
```

---

## `Settings`

### Signature

```python
class Settings(BaseSettings):
    postgres_dsn: str
    mongo_uri: str
    redis_url: str
    csm_set_service_url: str
    internal_api_key: str
    log_level: str = "INFO"
    strategy_registry_path: Path = Path("strategies.json")
    overall_performance_ttl_seconds: int = 300
    strategy_performance_ttl_seconds: int = 300
    portfolio_snapshot_ttl_seconds: int = 3600
```

### Fields

| Field | Type | Default | Env Var | Description |
|-------|------|---------|---------|-------------|
| `postgres_dsn` | `str` | required | `POSTGRES_DSN` | PostgreSQL DSN for `db_gateway`. Ex: `postgresql://postgres:pass@quant-postgres:5432/db_gateway` |
| `mongo_uri` | `str` | required | `MONGO_URI` | MongoDB URI. Ex: `mongodb://quant-mongo:27017/` |
| `redis_url` | `str` | required | `REDIS_URL` | Redis URL. Ex: `redis://quant-redis:6379/0` |
| `csm_set_service_url` | `str` | required | `CSM_SET_SERVICE_URL` | Base URL of CSM-SET strategy service. Ex: `http://csm:8000` |
| `internal_api_key` | `str` (min_length=1) | required | `INTERNAL_API_KEY` | Shared secret for `X-API-Key` header |
| `log_level` | `str` | `"INFO"` | `LOG_LEVEL` | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `strategy_registry_path` | `Path` | `Path("strategies.json")` | `STRATEGY_REGISTRY_PATH` | Filesystem path to registry JSON |
| `overall_performance_ttl_seconds` | `int` | `300` | `OVERALL_PERFORMANCE_TTL_SECONDS` | Cache TTL for `overall_performance` key |
| `strategy_performance_ttl_seconds` | `int` | `300` | `STRATEGY_PERFORMANCE_TTL_SECONDS` | Cache TTL for `strategy:{id}:performance` keys |
| `portfolio_snapshot_ttl_seconds` | `int` | `3600` | `PORTFOLIO_SNAPSHOT_TTL_SECONDS` | Cache TTL for `portfolio_snapshot:{date}` keys |

---

## `get_settings()`

Lazy, cached accessor — reads environment once, reuses thereafter.

### Signature

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings: ...
```

### Returns

`Settings` — The validated settings instance. Cached after first call.

### Raises

`pydantic.ValidationError` — If any required env var is missing or fails validation.

### Example

```python
from src.config import get_settings

s = get_settings()
assert s.log_level == "INFO"

# Tests can override:
get_settings.cache_clear()
monkeypatch.setenv("LOG_LEVEL", "DEBUG")
s2 = get_settings()
assert s2.log_level == "DEBUG"
```

---

## `.env` Template

```bash
# Storage — provisioned by quant-infra-db on quant-network
POSTGRES_DSN=postgresql://postgres:<pass>@quant-postgres:5432/db_gateway
MONGO_URI=mongodb://quant-mongo:27017/
REDIS_URL=redis://quant-redis:6379/0

# Upstream Strategy Services
CSM_SET_SERVICE_URL=http://csm:8000

# Inter-service authentication
INTERNAL_API_KEY=your_strong_internal_key_here

# Observability
LOG_LEVEL=INFO

# Docker host port (default 8000; override if 8000 is taken)
API_GATEWAY_HOST_PORT=8000
```

---

## See Also

- [Environment Variables Reference](../../operations/environment.md) — all env vars with deployment notes
- [Docker Compose Reference](../../operations/docker-compose.md) — how Settings maps to container env
- [`uv run python -c "from src.config import Settings; print(Settings.model_json_schema())"`](https://docs.pydantic.dev/latest/concepts/json_schema/)
