"""Google AI Studio's Gemini API (ADR-023): the model wherever Bedrock can't run.

The key is ``WF_GEMINI_API_KEY`` locally. Deployed, it is the secret named by
``WF_GEMINI_SECRET`` (``wf-<env>/models/gemini``), stored by hand as a plain string;
a JSON secret's ``api_key`` field works too. It is read at most every five minutes,
so a key stored or replaced after a deploy is picked up without another one.
Nothing here logs the key or returns it anywhere but to the caller.
"""

from __future__ import annotations

import json
import time

from .settings import Settings

TTL_S = 300.0
_cache: dict[str, tuple[float, str]] = {}


def configured(s: Settings) -> bool:
    """A Gemini model id and somewhere to read its key from."""
    return bool(s.gemini_model_id and (s.gemini_api_key or s.gemini_secret))


def api_key(s: Settings) -> str | None:
    if s.gemini_api_key:
        return s.gemini_api_key
    if not s.gemini_secret:
        return None
    hit = _cache.get(s.gemini_secret)
    if hit is None or hit[0] < time.monotonic():
        hit = (time.monotonic() + TTL_S, _plain(_fetch(s.gemini_secret)))
        _cache[s.gemini_secret] = hit
    return hit[1] or None


def _plain(raw: str) -> str:
    raw = raw.strip()
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(value, dict):
        return str(value.get("api_key") or next(iter(value.values()), "")).strip()
    return raw


def _fetch(secret_id: str) -> str:
    import boto3  # lazy: only in AWS

    return str(boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"])
