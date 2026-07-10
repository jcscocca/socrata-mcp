from pathlib import Path

from socrata_mcp.config import Config


def test_defaults_when_env_empty():
    cfg = Config.from_env(env={})
    assert cfg.app_token is None
    assert cfg.cache_dir == Path.home() / ".socrata-mcp" / "cache"
    assert cfg.metadata_ttl == 300.0
    assert cfg.catalog_ttl == 300.0
    assert cfg.query_ttl == 3600.0
    assert cfg.default_limit == 100
    assert cfg.max_rows == 5000
    assert cfg.max_export_rows == 1_000_000
    assert cfg.page_size == 1000
    assert cfg.throttle_interval == 0.2
    assert cfg.timeout == 30.0


def test_env_overrides():
    cfg = Config.from_env(
        env={
            "SOCRATA_APP_TOKEN": "tok123",
            "SOCRATA_MCP_CACHE_DIR": "/tmp/altcache",
            "SOCRATA_MCP_QUERY_TTL": "60",
            "SOCRATA_MCP_MAX_ROWS": "250",
            "SOCRATA_MCP_THROTTLE_INTERVAL": "0",
        }
    )
    assert cfg.app_token == "tok123"
    assert cfg.cache_dir == Path("/tmp/altcache")
    assert cfg.query_ttl == 60.0
    assert cfg.max_rows == 250
    assert cfg.throttle_interval == 0.0


def test_blank_app_token_is_none():
    cfg = Config.from_env(env={"SOCRATA_APP_TOKEN": "  "})
    assert cfg.app_token is None
