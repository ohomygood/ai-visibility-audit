"""Polite, read-only HTTP client.

- Only GET requests.
- Per-host minimum interval (default 1.0 s) so we never hammer a store.
- Hard caps on body size and timeouts.
- Every request is recorded so the report can show exactly what was fetched.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests

from . import __version__

CONTACT_URL = os.environ.get("AIV_CONTACT_URL", "https://github.com/ohomygood/ai-visibility-audit")
DEFAULT_UA = f"AIVisibilityAudit/{__version__} (+{CONTACT_URL}; read-only site audit)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def is_public_host(url: str) -> bool:
    """SSRF guard: refuse localhost / private / link-local / reserved targets."""
    if os.environ.get("AIV_ALLOW_PRIVATE") == "1":
        return True
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, 443)
    except socket.gaierror:
        return False
    trusted = [ipaddress.ip_network(n.strip()) for n in os.environ.get("AIV_TRUSTED_NETS", "").split(",") if n.strip()]
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if any(ip in n for n in trusted):
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def guard_enabled() -> bool:
    return os.environ.get("AIV_BLOCK_PRIVATE") == "1" or os.environ.get("AIV_ENV", "").lower() in ("prod", "production")


@dataclass
class Fetch:
    url: str
    final_url: str
    status: int | None
    headers: dict
    text: str
    elapsed_ms: int
    ua_label: str
    error: str | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


@dataclass
class PoliteSession:
    min_interval: float = 1.0
    timeout: float = 15.0
    max_bytes: int = 3_000_000
    log: list = field(default_factory=list)

    def __post_init__(self):
        self._s = requests.Session()
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _wait(self, host: str):
        with self._lock:
            now = time.monotonic()
            nxt = self._last.get(host, 0.0) + self.min_interval
            delay = max(0.0, nxt - now)
            self._last[host] = now + delay
        if delay:
            time.sleep(delay)

    def get(self, url: str, ua: str = DEFAULT_UA, ua_label: str = "default",
            accept: str = "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            keep_body: bool = True) -> Fetch:
        host = urlparse(url).netloc.lower()
        self._wait(host)
        t0 = time.monotonic()
        try:
            hdrs = {"User-Agent": ua, "Accept": accept, "Accept-Language": "en-US,en;q=0.8"}
            cur, guard = url, guard_enabled()
            for _hop in range(6):  # follow redirects manually so every hop passes the SSRF guard
                if guard and not is_public_host(cur):
                    raise requests.exceptions.InvalidURL(f"blocked non-public target {cur}")
                r = self._s.get(cur, headers=hdrs, timeout=self.timeout, allow_redirects=False, stream=True)
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    r.close()
                    cur = urljoin(cur, r.headers["location"])
                    continue
                break
            chunks, size, truncated = [], 0, False
            for chunk in r.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > self.max_bytes:
                    truncated = True
                    break
            r.close()
            raw = b"".join(chunks)
            enc = r.encoding or "utf-8"
            if enc.lower() == "iso-8859-1" and "charset" not in r.headers.get("content-type", "").lower():
                enc = "utf-8"
            text = raw.decode(enc, errors="replace") if keep_body else ""
            f = Fetch(url, r.url, r.status_code, {k.lower(): v for k, v in r.headers.items()},
                      text, int((time.monotonic() - t0) * 1000), ua_label, truncated=truncated)
        except requests.RequestException as e:
            f = Fetch(url, url, None, {}, "", int((time.monotonic() - t0) * 1000), ua_label,
                      error=f"{type(e).__name__}: {e}"[:300])
        self.log.append({"url": url, "ua": ua_label, "status": f.status,
                         "ms": f.elapsed_ms, "error": f.error})
        return f
