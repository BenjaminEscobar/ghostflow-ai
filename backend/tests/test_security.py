import base64
import string

import pytest

from ghostflow.security import UrlNotAllowedError, UrlPolicy, is_sensitive_field, looks_like_secret, redact_url

POLICY = UrlPolicy(["http://localhost:8080", "https://portal.example.test"])


# Fake secrets are assembled at runtime so no token-shaped literal is ever committed
# (secret scanners such as Gitleaks would rightly flag one).
def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _synthetic(prefix: str, length: int = 40) -> str:
    alphabet = string.ascii_letters + string.digits
    return prefix + "".join(alphabet[(i * 7) % len(alphabet)] for i in range(length))


FAKE_JWT = ".".join((_b64url('{"alg":"HS256"}'), _b64url('{"sub":"1"}'), _b64url("signature")))
FAKE_STRIPE_KEY = _synthetic("_".join(("sk", "live", "")))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/",
        "http://localhost:8080/new-request.html?x=1",
        "HTTP://LOCALHOST:8080/a",
        "https://portal.example.test/path",
        "https://portal.example.test:443/path",
    ],
)
def test_allowed_urls(url):
    assert POLICY.is_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8081/",  # other port
        "https://localhost:8080/",  # other scheme
        "http://127.0.0.1:8080/",  # same machine, different origin string
        "http://2130706433:8080/",  # decimal-encoded 127.0.0.1
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://metadata.google.internal/",
        "http://user:pass@localhost:8080/",  # embedded credentials
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,hi",
        "ftp://localhost:8080/",
        "http://localhost:8080.evil.test/",
        "not a url",
        "http://[::1",
    ],
)
def test_blocked_urls(url):
    assert not POLICY.is_allowed(url)
    with pytest.raises(UrlNotAllowedError):
        POLICY.check(url)


def test_error_messages_do_not_echo_paths_or_queries():
    with pytest.raises(UrlNotAllowedError) as exc:
        POLICY.check("http://other.test/private/path?token=abc")
    assert "private" not in str(exc.value) and "abc" not in str(exc.value)


def test_metadata_hosts_cannot_be_allowlisted():
    with pytest.raises(UrlNotAllowedError):
        UrlPolicy(["http://169.254.169.254"])


def test_restricted_policy_must_be_subset():
    narrower = POLICY.restricted_to(["http://localhost:8080"])
    assert narrower.is_allowed("http://localhost:8080/x")
    assert not narrower.is_allowed("https://portal.example.test/")
    with pytest.raises(UrlNotAllowedError):
        POLICY.restricted_to(["http://evil.test"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_type": "password"},
        {"autocomplete": "current-password"},
        {"autocomplete": "section-x cc-number"},
        {"names": ("Contraseña",)},
        {"names": (None, "api_key")},
        {"names": ("Card number",)},
        {"names": ("auth_token",)},
        {"names": ("Código OTP",)},
    ],
)
def test_sensitive_fields(kwargs):
    assert is_sensitive_field(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_type": "text", "names": ("Nombre del cliente", "customer_name")},
        {"input_type": "email", "autocomplete": "email", "names": ("Correo",)},
        {"names": ("Author", "Descripción", "Spinner")},
    ],
)
def test_regular_fields(kwargs):
    assert not is_sensitive_field(**kwargs)


@pytest.mark.parametrize(
    "value",
    [FAKE_JWT, "Bearer abc.def", FAKE_STRIPE_KEY],
    ids=["jwt", "bearer", "stripe-like-key"],  # ids keep fake values out of test output
)
def test_secret_values(value):
    assert looks_like_secret(value)


@pytest.mark.parametrize("value", ["Ana Torres", "ana@ejemplo.test", "12345678901234567890123456789012345", "", None])
def test_regular_values(value):
    assert not looks_like_secret(value)


def test_redact_url():
    url = "http://localhost:8080/confirm.html?ref=REQ-1&token=abc123&session_id=zz#frag"
    out = redact_url(url)
    assert "ref=REQ-1" in out
    assert "abc123" not in out and "zz" not in out and "frag" not in out
    assert redact_url("http://localhost:8080/a.html") == "http://localhost:8080/a.html"


def test_redact_url_catches_secret_shaped_values_under_innocent_keys():
    out = redact_url(f"http://localhost:8080/cb?state={FAKE_STRIPE_KEY}&ref=REQ-1")
    assert FAKE_STRIPE_KEY not in out and "ref=REQ-1" in out
