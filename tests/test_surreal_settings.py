"""SurrealDB address and credentials: explicit loopback address, values from
the environment or the private config (outside Git), no built-in root
credentials anywhere in the package."""
import json
from pathlib import Path

import pytest

from nadirclaw.settings import Settings

PACKAGE = Path(__file__).resolve().parents[1] / "nadirclaw"


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in ("NADIRCLAW_SURREALDB_URL", "NADIRCLAW_SURREALDB_USER", "NADIRCLAW_SURREALDB_PASS"):
        monkeypatch.delenv(name, raising=False)
    cfg = tmp_path / "surrealdb.json"
    monkeypatch.setenv("SURREALDB_CONFIG", str(cfg))
    return cfg


def test_default_url_is_explicit_loopback(clean_env):
    s = Settings()
    assert s.SURREALDB_URL == "ws://127.0.0.1:8000"
    assert s.SURREALDB_HTTP_URL == "http://127.0.0.1:8000"


def test_no_builtin_credentials(clean_env):
    s = Settings()
    assert (s.SURREALDB_USER, s.SURREALDB_PASS) == ("", "")


def test_private_config_supplies_url_and_credentials_with_client_entry_winning(clean_env):
    clean_env.write_text(json.dumps({
        "url": "http://127.0.0.1:8000", "user": "top", "password": "top-pw",
        "clients": {"nadirclaw": {"user": "nadir", "password": "nadir-pw"}},
    }), encoding="utf-8")
    s = Settings()
    assert s.SURREALDB_URL == "ws://127.0.0.1:8000"
    assert (s.SURREALDB_USER, s.SURREALDB_PASS) == ("nadir", "nadir-pw")


def test_environment_beats_private_config(clean_env, monkeypatch):
    clean_env.write_text(json.dumps({"user": "cfg", "password": "cfg-pw"}), encoding="utf-8")
    monkeypatch.setenv("NADIRCLAW_SURREALDB_USER", "env")
    monkeypatch.setenv("NADIRCLAW_SURREALDB_PASS", "env-pw")
    monkeypatch.setenv("NADIRCLAW_SURREALDB_URL", "ws://127.0.0.1:8001/rpc")
    s = Settings()
    assert (s.SURREALDB_USER, s.SURREALDB_PASS) == ("env", "env-pw")
    assert s.SURREALDB_HTTP_URL == "http://127.0.0.1:8001"


def test_malformed_private_config_is_ignored_not_fatal(clean_env):
    clean_env.write_text("{not json", encoding="utf-8")
    assert Settings().SURREALDB_USER == ""


def test_package_has_no_hard_coded_surrealdb_credentials_or_localhost():
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "root:root" not in text, path.name
        assert '"root")' not in text, path.name
        assert "ws://localhost:8000" not in text, path.name
        assert "0.0.0.0:8000" not in text, path.name
