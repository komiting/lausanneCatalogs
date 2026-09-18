"""HTTP layer: a polite live client plus record/replay helpers for tests.

The live client uses curl_cffi so that TLS fingerprints look like a normal
browser; some shops (Coop) sit behind bot protection that rejects plain
Python clients.
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

log = logging.getLogger(__name__)


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Blocked(HttpError):
    """The shop answered with a bot-protection page."""


@dataclass
class Response:
    status: int
    url: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.text)

    def header(self, name: str) -> str | None:
        name = name.lower()
        for k, v in self.headers.items():
            if k.lower() == name:
                return v
        return None


def canonical_url(url: str, params: dict[str, Any] | None = None) -> str:
    """Normalise a URL (decoded path, sorted query) so recordings match."""
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if params:
        query += [(k, str(v)) for k, v in params.items() if v is not None]
    query.sort()
    path = unquote(parts.path)
    q = "&".join(f"{k}={unquote(v)}" for k, v in query)
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, q, ""))


def canonical_body(body: Any) -> str:
    if body is None:
        return ""
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def request_key(method: str, url: str, params: dict | None = None, body: Any = None) -> str:
    return f"{method.upper()} {canonical_url(url, params)} {canonical_body(body)}".strip()


_BLOCK_MARKERS = ("captcha-delivery.com", "datadome", "cf-chl", "Just a moment...", "Access Denied")


class LiveHttp:
    """Real network client with delays, retries and bot-block detection."""

    def __init__(
        self,
        impersonate: str = "chrome",
        headers: dict[str, str] | None = None,
        delay: float = 0.8,
        timeout: float = 30.0,
        retries: int = 3,
    ):
        from curl_cffi import requests as curl_requests

        self.session = curl_requests.Session(impersonate=impersonate)
        if headers:
            self.session.headers.update(headers)
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.count = 0
        self._last = 0.0

    def _pause(self) -> None:
        wait = self.delay * random.uniform(0.7, 1.3) - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def request(self, method: str, url: str, *, params=None, json_body=None, headers=None) -> Response:
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._pause()
            try:
                r = self.session.request(
                    method, url, params=params, json=json_body, headers=headers, timeout=self.timeout
                )
                self.count += 1
            except Exception as exc:  # network error
                last_exc = exc
                log.warning("%s %s failed (%s), attempt %d", method, url, exc, attempt)
                time.sleep(2 ** attempt)
                continue
            text = r.text or ""
            resp = Response(r.status_code, str(r.url), text, {k: v for k, v in r.headers.items()})
            if r.status_code in (429, 500, 502, 503, 504):
                last_exc = HttpError(f"HTTP {r.status_code} for {url}", r.status_code)
                time.sleep(3 * attempt)
                continue
            if r.status_code == 403 or (r.status_code >= 400 and any(m in text[:3000] for m in _BLOCK_MARKERS)):
                raise Blocked(f"blocked by bot protection ({r.status_code}) at {url}", r.status_code)
            if r.status_code >= 400:
                raise HttpError(f"HTTP {r.status_code} for {url}: {text[:200]}", r.status_code)
            return resp
        raise HttpError(f"giving up on {url}: {last_exc}")

    def get(self, url: str, params=None, headers=None) -> Response:
        return self.request("GET", url, params=params, headers=headers)

    def post(self, url: str, json_body=None, headers=None) -> Response:
        return self.request("POST", url, json_body=json_body, headers=headers)


class ReplayHttp:
    """Serves responses recorded earlier (from a browser or a live run).

    A recording is a JSON list of entries:
      {"method", "url", "body", "status", "headers", "text"}
    """

    def __init__(self, entries: list[dict[str, Any]], strict: bool = True):
        self.entries: dict[str, dict[str, Any]] = {}
        for e in entries:
            key = request_key(e.get("method", "GET"), e["url"], None, e.get("body"))
            self.entries[key] = e
        self.strict = strict
        self.count = 0
        self.missing: list[str] = []

    @classmethod
    def from_files(cls, paths: list[Path], strict: bool = True) -> "ReplayHttp":
        entries: list[dict[str, Any]] = []
        for p in paths:
            raw = Path(p).read_bytes()
            if raw[:2] == b"\x1f\x8b":
                import gzip
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8"))
            entries.extend(data["entries"] if isinstance(data, dict) else data)
        return cls(entries, strict=strict)

    def request(self, method: str, url: str, *, params=None, json_body=None, headers=None) -> Response:
        key = request_key(method, url, params, json_body)
        e = self.entries.get(key)
        if e is None:
            self.missing.append(key)
            if self.strict:
                raise HttpError(f"no recording for {key[:300]}", 404)
            return Response(404, url, "{}", {})
        self.count += 1
        status = int(e.get("status", 200))
        text = e.get("text")
        if text is None:
            text = json.dumps(e.get("json"), ensure_ascii=False)
        if status >= 400:
            raise HttpError(f"HTTP {status} for {url} (recorded)", status)
        return Response(status, url, text, e.get("headers") or {})

    def get(self, url: str, params=None, headers=None) -> Response:
        return self.request("GET", url, params=params, headers=headers)

    def post(self, url: str, json_body=None, headers=None) -> Response:
        return self.request("POST", url, json_body=json_body, headers=headers)


class RecordingHttp:
    """Wraps a live client and keeps every exchange (for debugging/fixtures)."""

    def __init__(self, inner: LiveHttp):
        self.inner = inner
        self.entries: list[dict[str, Any]] = []

    @property
    def count(self) -> int:
        return self.inner.count

    def request(self, method, url, *, params=None, json_body=None, headers=None) -> Response:
        r = self.inner.request(method, url, params=params, json_body=json_body, headers=headers)
        full = url if not params else f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
        self.entries.append({
            "method": method, "url": full, "body": json_body, "status": r.status,
            "headers": {k: v for k, v in r.headers.items() if k.lower() in ("leshopch", "content-type")},
            "text": r.text,
        })
        return r

    def get(self, url, params=None, headers=None):
        return self.request("GET", url, params=params, headers=headers)

    def post(self, url, json_body=None, headers=None):
        return self.request("POST", url, json_body=json_body, headers=headers)

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({"entries": self.entries}, ensure_ascii=False), encoding="utf-8")
