"""Origin allowlist used everywhere a URL can reach the outside world.

GhostFlow never fetches arbitrary URLs from the backend process. The only component that
makes requests on behalf of a workflow is the browser, and every request it issues goes
through `UrlPolicy.is_allowed` (see execution/playwright_web.py).
"""

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Cloud metadata endpoints and similar internal resources. Blocked even if someone
# misconfigures the allowlist to include them.
_ALWAYS_BLOCKED_HOSTS = {
    "169.254.169.254",
    "fd00:ec2::254",
    "100.100.100.200",
    "metadata",
    "metadata.google.internal",
    "metadata.azure.com",
}


class UrlNotAllowedError(ValueError):
    pass


def _is_blocked_host(host: str) -> bool:
    if host in _ALWAYS_BLOCKED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_link_local or ip.is_multicast or ip.is_unspecified


def canonical_origin(url: str) -> str:
    """Return `scheme://host:port` for an http(s) URL, raising UrlNotAllowedError otherwise."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UrlNotAllowedError(f"Malformed URL: {exc}") from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise UrlNotAllowedError(f"Scheme '{scheme}' is not allowed")
    if parts.username or parts.password:
        raise UrlNotAllowedError("URLs with embedded credentials are not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise UrlNotAllowedError("URL has no host")
    if _is_blocked_host(host):
        raise UrlNotAllowedError(f"Host '{host}' is an internal resource and is always blocked")
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port or _DEFAULT_PORTS[scheme]}"


def display_origin(url: str) -> str:
    """Origin without the default port, e.g. http://localhost:8080 or https://example.com."""
    origin = canonical_origin(url)
    scheme, rest = origin.split("://", 1)
    host, port = rest.rsplit(":", 1)
    return f"{scheme}://{host}" if int(port) == _DEFAULT_PORTS[scheme] else origin


class UrlPolicy:
    """Exact-origin allowlist. A URL is allowed only if its scheme, host and port match."""

    def __init__(self, origins: Iterable[str]):
        self._origins = frozenset(canonical_origin(o) for o in origins)
        if not self._origins:
            raise ValueError("At least one allowed origin is required")

    @property
    def origins(self) -> frozenset[str]:
        return self._origins

    def is_allowed(self, url: str) -> bool:
        try:
            return canonical_origin(url) in self._origins
        except UrlNotAllowedError:
            return False

    def check(self, url: str) -> None:
        if not self.is_allowed(url):
            raise UrlNotAllowedError(f"URL origin is not in the allowlist: {_origin_for_message(url)}")

    def restricted_to(self, origins: Iterable[str]) -> "UrlPolicy":
        """A narrower policy. Every requested origin must already be allowed by this one."""
        requested = [canonical_origin(o) for o in origins]
        outside = [o for o in requested if o not in self._origins]
        if outside:
            raise UrlNotAllowedError(f"Origins not in the server allowlist: {', '.join(outside)}")
        return UrlPolicy(requested)


def _origin_for_message(url: str) -> str:
    # Never echo paths or query strings back: they may carry user data.
    try:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.hostname or ''}" + (f":{parts.port}" if parts.port else "")
    except ValueError:
        return "<malformed>"
