import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from app.core.errors import ApiError, CacheUnavailable


ALIASES = {
    "joao": "John", "salmos": "psa", "salmo": "psa",
    "psalms": "psa", "psalm": "psa",
}


def normalize_reference(value):
    if not isinstance(value, str):
        raise ApiError(400, "validation_error", "Informe uma referência bíblica.")
    value = " ".join(value.split())
    if not 1 <= len(value) <= 100 or not re.fullmatch(
        r"(?:[1-3]\s*)?[^\W\d_]+(?:\s+[^\W\d_]+)*\s+[1-9]\d{0,2}"
        r"(?::[1-9]\d{0,2}(?:[-,][1-9]\d{0,2})*)?", value
    ):
        raise ApiError(400, "validation_error", "Referência inválida. Exemplo: João 3:16.")
    book, passage = value.rsplit(" ", 1)
    key = "".join(c for c in unicodedata.normalize("NFD", book) if not unicodedata.combining(c))
    return f"{ALIASES.get(key.casefold(), book)} {passage}"


def fetch_passage(reference, translation, config):
    # Fixed configured provider, encoded path, no redirects to third-party hosts.
    url = config["BIBLE_API_BASE_URL"] + "/" + quote(reference, safe="")
    try:
        with requests.get(
            url, params={"translation": translation},
            timeout=(config["BIBLE_CONNECT_TIMEOUT_SECONDS"], config["BIBLE_READ_TIMEOUT_SECONDS"]),
            allow_redirects=False, stream=True,
            headers={"Accept": "application/json", "User-Agent": "AppCristao-Academic/0.1"},
        ) as response:
            if response.status_code == 404:
                raise ApiError(404, "passage_not_found", "Passagem não encontrada.")
            if response.status_code == 429 or response.status_code >= 500:
                raise ApiError(503, "bible_unavailable", "Consulta bíblica temporariamente indisponível.")
            if response.status_code != 200:
                raise ApiError(502, "invalid_provider_response", "Resposta inválida do serviço bíblico.")
            content = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                content.extend(chunk)
                if len(content) > 262144:
                    raise ApiError(502, "invalid_provider_response", "Resposta inválida do serviço bíblico.")
            payload = json.loads(content)
    except requests.RequestException as exc:
        raise ApiError(503, "bible_unavailable", "Consulta bíblica temporariamente indisponível.") from exc
    except (ValueError, UnicodeError) as exc:
        raise ApiError(502, "invalid_provider_response", "Resposta inválida do serviço bíblico.") from exc

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("text"), str) or not payload["text"].strip()
        or not isinstance(payload.get("reference"), str) or not payload["reference"].strip()
        or payload.get("translation_id") != translation
    ):
        raise ApiError(502, "invalid_provider_response", "Resposta inválida do serviço bíblico.")
    return {
        "reference": payload["reference"].strip(), "translation": translation,
        "text": payload["text"].strip(), "source": "bible-api.com",
    }


class BibleService:
    def __init__(self, cache, config, fetch=fetch_passage, clock=None):
        self.cache, self.config, self.fetch = cache, config, fetch
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def passage(self, reference, translation):
        reference = normalize_reference(reference)
        if translation != "almeida":
            raise ApiError(400, "validation_error", "Tradução permitida: almeida.")
        key = hashlib.sha256(f"{translation}:{reference.casefold()}".encode()).hexdigest()
        now = self.clock()
        try:
            value = self.cache.get(key, now)
            if value is not None:
                return {**value, "cached": True}
            value = self.fetch(reference, translation, self.config)
            self.cache.set(key, value, now + timedelta(seconds=self.config["BIBLE_CACHE_TTL_SECONDS"]), now)
        except CacheUnavailable as exc:
            raise ApiError(503, "cache_unavailable", "Consulta bíblica temporariamente indisponível.") from exc
        return {**value, "cached": False}
