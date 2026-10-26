"""Allowlisted, pinned-IP HTTPS research fetches with untrusted source records."""

import hashlib
import html
import http.client
import ipaddress
import os
import re
import socket
import ssl
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

MAX_RESEARCH_URLS = 3
MAX_RESEARCH_QUERY_CHARS = 200
MAX_RESPONSE_BYTES = 262_144
MAX_EXCERPT_CHARS = 2_400
FETCH_TIMEOUT_SECONDS = 6.0
MAX_URL_CHARS = 2_048


class ResearchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ResearchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    enabled: bool
    allowed_domains: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.allowed_domains)


class ResearchSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_id: str = Field(min_length=1, max_length=36)
    query: str = Field(min_length=1, max_length=MAX_RESEARCH_QUERY_CHARS)
    urls: list[str] = Field(min_length=1, max_length=MAX_RESEARCH_URLS)


class ResearchSourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    url: str = Field(max_length=MAX_URL_CHARS)
    title: str = Field(max_length=200)
    excerpt: str = Field(max_length=MAX_EXCERPT_CHARS)
    content_type: str | None = None
    content_sha256: str | None = None
    fetched_at: str | None = None
    trust_level: Literal["untrusted"] = "untrusted"
    status: Literal["fetched", "failed"]
    error_code: str | None = None


@dataclass(frozen=True)
class PreparedURL:
    url: str
    hostname: str
    request_target: str
    pinned_ip: str


def _normalize_domain(value: str) -> str:
    try:
        if not value or value != value.strip() or any(char in value for char in "/:@*\\"):
            raise ValueError
        ascii_domain = value.rstrip(".").encode("idna").decode("ascii").lower()
        try:
            ipaddress.ip_address(ascii_domain)
        except ValueError:
            pass
        else:
            raise ValueError
        if not ascii_domain or len(ascii_domain) > 253 or "." not in ascii_domain:
            raise ValueError
        labels = ascii_domain.split(".")
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
            raise ValueError
        if any(not re.fullmatch(r"[a-z0-9-]+", label) for label in labels):
            raise ValueError
        return ascii_domain
    except (UnicodeError, ValueError):
        raise ResearchError("research_config_invalid", "Research domain configuration is invalid") from None


def research_settings(environ: dict[str, str] | None = None) -> ResearchSettings:
    values = os.environ if environ is None else environ
    enabled = values.get("RESEARCH_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
    raw_domains = [item.strip() for item in values.get("RESEARCH_ALLOWED_DOMAINS", "").split(",") if item.strip()]
    domains = tuple(sorted({_normalize_domain(item) for item in raw_domains}))
    if enabled and not domains:
        raise ResearchError("research_not_configured", "Research is not configured")
    return ResearchSettings(enabled=enabled, allowed_domains=domains)


def _resolve_public_ips(hostname: str) -> tuple[str, ...]:
    try:
        entries = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError:
        raise ResearchError("research_dns_failed", "A research source could not be resolved") from None
    addresses = set()
    for entry in entries:
        try:
            address = ipaddress.ip_address(str(entry[4][0]).split("%", 1)[0])
        except ValueError:
            raise ResearchError("research_address_denied", "A research source address was rejected") from None
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if (
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ResearchError("research_address_denied", "A research source address was rejected")
        addresses.add(address.compressed)
    if not addresses:
        raise ResearchError("research_dns_failed", "A research source could not be resolved")
    # Pin one validated result. No follow-up hostname lookup is performed by the HTTP client.
    return tuple(sorted(addresses, key=lambda item: (":" in item, item)))


def prepare_url(url: str, settings: ResearchSettings) -> PreparedURL:
    if not settings.available:
        raise ResearchError("research_unavailable", "Research fetching is not available")
    if (
        len(url) > MAX_URL_CHARS
        or any(ord(char) < 32 or ord(char) == 127 for char in url)
        or "\\" in url
    ):
        raise ResearchError("research_url_invalid", "A research URL is invalid")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ResearchError("research_url_invalid", "A research URL is invalid") from None
    if (
        parts.scheme.lower() != "https"
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or port not in (None, 443)
    ):
        raise ResearchError("research_url_denied", "A research URL is not allowed")
    try:
        hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        ipaddress.ip_address(hostname)
        raise ResearchError("research_url_denied", "A research URL is not allowed")
    except ValueError:
        pass
    except UnicodeError:
        raise ResearchError("research_url_invalid", "A research URL is invalid") from None
    if hostname not in settings.allowed_domains:
        raise ResearchError("research_domain_denied", "A research domain is not allowlisted")
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/?@!$&'()*+,;=:-._~%")
    target = urlunsplit(("", "", path, query, ""))
    canonical = urlunsplit(("https", hostname, path, query, ""))
    if len(canonical) > MAX_URL_CHARS:
        raise ResearchError("research_url_invalid", "A research URL is invalid")
    pinned_ips = _resolve_public_ips(hostname)
    return PreparedURL(canonical, hostname, target, pinned_ips[0])


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, pinned_ip: str, timeout: float):
        self._pinned_ip = pinned_ip
        self._ssl_context = ssl.create_default_context()
        super().__init__(hostname, 443, timeout=timeout, context=self._ssl_context)

    def connect(self):
        raw = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)
        try:
            self.sock = self._ssl_context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _fetch(prepared: PreparedURL) -> tuple[bytes, str]:
    deadline = time.monotonic() + FETCH_TIMEOUT_SECONDS
    connection = _PinnedHTTPSConnection(prepared.hostname, prepared.pinned_ip, FETCH_TIMEOUT_SECONDS)
    try:
        connection.request(
            "GET",
            prepared.request_target,
            headers={
                "Host": prepared.hostname,
                "Accept": "text/html, text/plain;q=0.9",
                "Accept-Encoding": "identity",
                "User-Agent": "ORQESTRA-Research/1.0",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if response.status in (301, 302, 303, 307, 308):
            raise ResearchError("research_redirect_blocked", "Redirects are not followed")
        if response.status != 200:
            raise ResearchError("research_fetch_failed", "A research source could not be fetched")
        content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type not in ("text/html", "text/plain"):
            raise ResearchError("research_content_type_denied", "The research source type is not allowed")
        length_header = response.getheader("Content-Length")
        if length_header is not None:
            try:
                if int(length_header) < 0 or int(length_header) > MAX_RESPONSE_BYTES:
                    raise ResearchError("research_response_too_large", "The research source is too large")
            except ValueError:
                raise ResearchError("research_response_invalid", "The research source response is invalid") from None
        body = bytearray()
        while len(body) <= MAX_RESPONSE_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResearchError("research_timeout", "The research source exceeded its time limit")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ResearchError("research_response_too_large", "The research source is too large")
        if time.monotonic() > deadline:
            raise ResearchError("research_timeout", "The research source exceeded its time limit")
        if length_header is not None and len(body) != int(length_header):
            raise ResearchError("research_response_invalid", "The research source response was incomplete")
        return bytes(body), response.getheader("Content-Type") or content_type
    except ResearchError:
        raise
    except (http.client.HTTPException, OSError, ssl.SSLError, TimeoutError):
        raise ResearchError("research_fetch_failed", "A research source could not be fetched safely") from None
    finally:
        connection.close()


class _TextExtractor(HTMLParser):
    _BLOCKED = {"script", "style", "noscript", "svg", "iframe", "object", "template"}
    _BLOCKS = {"address", "article", "blockquote", "br", "div", "h1", "h2", "h3", "li", "p", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._blocked_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._BLOCKED:
            self._blocked_depth += 1
        if tag == "title" and self._blocked_depth == 0:
            self._in_title = True
        if tag in self._BLOCKS and self._blocked_depth == 0:
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._BLOCKED and self._blocked_depth:
            self._blocked_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in self._BLOCKS and self._blocked_depth == 0:
            self.text_parts.append("\n")

    def handle_data(self, data):
        if self._blocked_depth:
            return
        (self.title_parts if self._in_title else self.text_parts).append(data)


def _clean_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", html.unescape(value))
    cleaned = "".join(char if char in "\n\t" or ord(char) >= 32 else " " for char in normalized)
    lines = [re.sub(r"\s+", " ", line).strip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract(body: bytes, content_type: str) -> tuple[str, str, str]:
    charset = "utf-8"
    match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1)
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    if content_type.split(";", 1)[0].strip().lower() == "text/html":
        parser = _TextExtractor()
        try:
            parser.feed(text)
            parser.close()
        except Exception:
            raise ResearchError("research_parse_failed", "The research source could not be parsed") from None
        title = _clean_text(" ".join(parser.title_parts))[:200]
        visible_text = _clean_text("\n".join(parser.text_parts))
    else:
        title = ""
        visible_text = _clean_text(text)
    return title, visible_text, hashlib.sha256(body).hexdigest()


def _excerpt(query: str, visible_text: str) -> str:
    paragraphs = [item.strip() for item in visible_text.splitlines() if item.strip()]
    if not paragraphs:
        paragraphs = [visible_text.strip()] if visible_text.strip() else []
    terms = [term for term in re.findall(r"[\w-]{2,}", query.casefold()) if term]
    ranked = sorted(
        enumerate(paragraphs),
        key=lambda item: (-sum(term in item[1].casefold() for term in terms), item[0]),
    )
    excerpt = "\n".join(paragraph for _, paragraph in ranked[:4])
    return excerpt[:MAX_EXCERPT_CHARS]


class ResearchGateway:
    def __init__(self, settings: ResearchSettings):
        self.settings = settings

    def prepare(self, urls: list[str]) -> list[PreparedURL]:
        if not 1 <= len(urls) <= MAX_RESEARCH_URLS:
            raise ResearchError("research_url_limit", "Choose one to three research sources")
        prepared = [prepare_url(url, self.settings) for url in urls]
        if len({item.url for item in prepared}) != len(prepared):
            raise ResearchError("research_url_duplicate", "Research source URLs must be unique")
        return prepared

    def fetch(self, query: str, prepared: PreparedURL) -> ResearchSourceRecord:
        source_id = str(uuid4())
        try:
            body, content_type = _fetch(prepared)
            title, visible_text, digest = _extract(body, content_type)
            return ResearchSourceRecord(
                id=source_id,
                url=prepared.url,
                title=title or prepared.hostname,
                excerpt=_excerpt(query, visible_text),
                content_type=content_type.split(";", 1)[0].strip().lower(),
                content_sha256=digest,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="fetched",
            )
        except ResearchError as error:
            return ResearchSourceRecord(
                id=source_id,
                url=prepared.url,
                title=prepared.hostname,
                excerpt="",
                status="failed",
                error_code=error.code,
            )


class ResearchAgent:
    """Query a caller-provided list of approved sources; returned content is data only."""

    allowed_tools = ("research.search", "research.fetch")

    def __init__(self, gateway: ResearchGateway):
        self.gateway = gateway

    def search(self, query: str, urls: list[str]) -> list[ResearchSourceRecord]:
        cleaned_query = _clean_text(query).strip()
        if not cleaned_query or len(cleaned_query) > MAX_RESEARCH_QUERY_CHARS:
            raise ResearchError("research_query_invalid", "The research query is invalid")
        prepared = self.gateway.prepare(urls)
        return self.search_prepared(cleaned_query, prepared)

    def search_prepared(
        self, query: str, prepared: list[PreparedURL]
    ) -> list[ResearchSourceRecord]:
        cleaned_query = _clean_text(query).strip()
        if not cleaned_query or len(cleaned_query) > MAX_RESEARCH_QUERY_CHARS:
            raise ResearchError("research_query_invalid", "The research query is invalid")
        return [self.gateway.fetch(cleaned_query, item) for item in prepared]
