from datetime import datetime, timedelta, timezone

import pytest
import requests

from app import create_app
from app.repositories.bible_cache import SQLiteCache
from app.core.errors import CacheUnavailable


PATH = "/api/bible/passages"


def test_health_does_not_touch_provider_or_cache(app, client, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Health não deve consultar dependências.")
    monkeypatch.setattr(app.extensions["bible_cache"], "get", forbidden)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json == {"data": {"status": "ok"}}
    assert response.headers["Cache-Control"] == "no-store"


def test_provider_success_and_persistent_cache(client, app, provider):
    calls = provider()
    first = client.get(PATH, query_string={"reference": "João 3:16"})
    assert first.status_code == 200
    assert first.json["data"] == {"reference": "John 3:16", "translation": "almeida",
        "text": "Texto de teste.", "source": "bible-api.com", "cached": False}
    # A second factory simulates another worker using the same persistent cache.
    second_app = create_app({"TESTING": True, "MONGODB_URI": "",
                            "CACHE_SQLITE_PATH": app.config["CACHE_SQLITE_PATH"]})
    second = second_app.test_client().get(PATH, query_string={"reference": "John 3:16"})
    assert second.json["data"]["cached"] is True
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "https://bible-api.com/John%203%3A16"
    assert kwargs["params"] == {"translation": "almeida"}
    assert kwargs["timeout"] == (2, 5)
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("reference", ["Salmos 23:1", "Salmo 23:1", "Psalms 23:1"])
def test_psalm_alias_matches_verified_provider_identifier(client, provider, reference):
    calls = provider(payload={"reference": "Salmos 23:1", "text": "Texto de teste.", "translation_id": "almeida"})
    response = client.get(PATH, query_string={"reference": reference})
    assert response.status_code == 200
    assert calls[0][0][0] == "https://bible-api.com/psa%2023%3A1"


@pytest.mark.parametrize("query", [
    {}, {"reference": ""}, {"reference": " "}, {"reference": "x" * 101},
    {"reference": "https://example.com"}, {"reference": "John 0:1"},
    {"reference": "John 3:16", "translation": "web"},
    {"reference": "John 3:16", "url": "https://example.com"},
    [("reference", "John 3:16"), ("reference", "Romans 1:1")],
])
def test_invalid_inputs_never_call_provider(client, query):
    response = client.get(PATH, query_string=query)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "validation_error"


@pytest.mark.parametrize("exception", [requests.Timeout, requests.ConnectionError, requests.exceptions.SSLError])
def test_network_failures_are_controlled(client, monkeypatch, exception):
    def fail(*args, **kwargs):
        raise exception("segredo-do-provedor")
    monkeypatch.setattr(requests, "get", fail)
    response = client.get(PATH, query_string={"reference": "John 3:16"})
    assert response.status_code == 503
    assert b"segredo" not in response.data


@pytest.mark.parametrize("upstream,expected", [(404, 404), (429, 503), (500, 503), (503, 503),
                                                (301, 502), (400, 502), (403, 502)])
def test_provider_http_errors(client, provider, upstream, expected):
    calls = provider(status=upstream)
    assert client.get(PATH, query_string={"reference": "John 3:16"}).status_code == expected
    assert len(calls) == 1  # No automatic retry storm.


@pytest.mark.parametrize("payload", [[], {}, {"text": "", "reference": "John 3:16", "translation_id": "almeida"},
    {"text": 123, "reference": "John 3:16", "translation_id": "almeida"},
    {"text": "texto", "reference": "John 3:16", "translation_id": "web"},
    {"text": "texto", "reference": None, "translation_id": "almeida"}])
def test_invalid_provider_schema(client, provider, payload):
    provider(payload=payload)
    assert client.get(PATH, query_string={"reference": "John 3:16"}).status_code == 502


@pytest.mark.parametrize("raw", [b"not json", b"\xff", b"x" * 262145])
def test_invalid_or_oversized_provider_body(client, provider, raw):
    provider(raw=raw)
    assert client.get(PATH, query_string={"reference": "John 3:16"}).status_code == 502


def test_expired_cache_does_not_serve_stale_passage(app, client, provider):
    calls = provider()
    service = app.extensions["bible_service"]
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    service.clock = lambda: now
    client.get(PATH, query_string={"reference": "John 3:16"})
    now += timedelta(hours=24)
    response = client.get(PATH, query_string={"reference": "John 3:16"})
    assert response.json["data"]["cached"] is False
    assert len(calls) == 2


def test_failed_response_is_not_cached(client, provider):
    provider(status=503)
    assert client.get(PATH, query_string={"reference": "John 3:16"}).status_code == 503
    calls = provider()
    assert client.get(PATH, query_string={"reference": "John 3:16"}).status_code == 200
    assert len(calls) == 2  # One failed request followed by a fresh successful request.


def test_cache_failure_is_controlled(app, client, monkeypatch):
    def fail(*args):
        raise CacheUnavailable()
    monkeypatch.setattr(app.extensions["bible_cache"], "get", fail)
    assert client.get(PATH, query_string={"reference": "John 3:16"}).status_code == 503
    assert client.get("/api/health").status_code == 200


def test_cache_write_failure_is_controlled(app, client, monkeypatch, provider):
    provider()
    def fail(*args):
        raise CacheUnavailable()
    monkeypatch.setattr(app.extensions["bible_cache"], "set", fail)
    assert client.get(PATH, query_string={"reference": "John 3:16"}).status_code == 503


def test_framework_errors_are_json_and_preserve_allow(client):
    assert client.get("/api/unknown").status_code == 404
    response = client.post("/api/health")
    assert response.status_code == 405
    assert response.is_json
    assert "GET" in response.headers["Allow"]


def test_unexpected_errors_do_not_leak_details(app, client, monkeypatch):
    def fail(*args):
        raise RuntimeError("senha-privada")
    monkeypatch.setattr(app.extensions["bible_service"], "passage", fail)
    response = client.get(PATH, query_string={"reference": "John 3:16"})
    assert response.status_code == 500
    assert b"senha-privada" not in response.data


def test_init_cache_cli(app):
    result = app.test_cli_runner().invoke(args=["init-cache"])
    assert result.exit_code == 0
    assert "SQLiteCache" in result.output


def test_production_never_silently_uses_sqlite():
    with pytest.raises(ValueError, match="Produção exige"):
        create_app({"APP_ENV": "production", "MONGODB_URI": "", "JWT_SECRET": "s" * 32})


def test_fixed_provider_configuration():
    with pytest.raises(ValueError, match="BIBLE_API_BASE_URL"):
        create_app({"BIBLE_API_BASE_URL": "http://localhost"})


def test_sqlite_expiry_and_corrupt_cache_are_controlled(tmp_path):
    cache = SQLiteCache(tmp_path / "cache.db")
    now = datetime.now(timezone.utc)
    cache.set("key", {"text": "ok"}, now, now)
    assert cache.get("key", now) is None
    corrupt = tmp_path / "invalid.db"
    corrupt.write_text("not sqlite")
    with pytest.raises(CacheUnavailable):
        SQLiteCache(corrupt).get("key", now)
