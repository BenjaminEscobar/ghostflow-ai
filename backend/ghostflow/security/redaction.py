"""Heuristics that keep passwords, tokens and similar data out of recordings.

The Chrome extension applies the same rules before sending anything; the backend applies
them again on ingestion so a buggy or tampered client cannot store secrets.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_INPUT_TYPES = {"password", "hidden"}

_SENSITIVE_AUTOCOMPLETE = {
    "current-password",
    "new-password",
    "one-time-code",
    "cc-number",
    "cc-csc",
    "cc-exp",
    "cc-exp-month",
    "cc-exp-year",
}

_SENSITIVE_NAME = re.compile(
    r"pass(word|wd|phrase)?|contrase|pwd|secret|token|api[-_ ]?key|\botp\b|\bpin\b|cvv|cvc|"
    r"card[-_ ]?(number|num)|tarjeta|\bssn\b|\biban\b|\bauth\b|authori[sz]ation|authentication|"
    r"session[-_ ]?id|cookie|credential",
    re.IGNORECASE,
)

_JWT = re.compile(r"^eyJ[\w-]+\.[\w-]+\.[\w-]*$")
_BEARER = re.compile(r"^(bearer|basic)\s+\S+", re.IGNORECASE)
_LONG_OPAQUE = re.compile(r"^[A-Za-z0-9+/=_\-]{32,}$")

_SENSITIVE_QUERY_KEYS = re.compile(
    r"token|key|secret|pass|auth|session|sig|code|credential", re.IGNORECASE
)


def is_sensitive_field(
    *,
    input_type: str | None = None,
    autocomplete: str | None = None,
    names: tuple[str | None, ...] = (),
) -> bool:
    if input_type and input_type.lower() in _SENSITIVE_INPUT_TYPES:
        return True
    if autocomplete and any(t in _SENSITIVE_AUTOCOMPLETE for t in autocomplete.lower().split()):
        return True
    return any(n and _SENSITIVE_NAME.search(n) for n in names)


def looks_like_secret(value: str | None) -> bool:
    if not value:
        return False
    v = value.strip()
    return bool(_JWT.match(v) or _BEARER.match(v) or (_LONG_OPAQUE.match(v) and not v.isdigit()))


def redact_url(url: str) -> str:
    """Replace values of sensitive-looking query parameters and drop the fragment."""
    parts = urlsplit(url)
    if not parts.query and not parts.fragment:
        return url
    query = [
        (k, "REDACTED" if _SENSITIVE_QUERY_KEYS.search(k) or looks_like_secret(v) else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
