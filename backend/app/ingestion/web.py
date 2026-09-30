"""Fetch a public web page or hosted document and extract ingestable text.

The fetcher is the trust boundary for "inject any website": it enforces
scheme and SSRF rules (re-resolving DNS immediately before every request,
including every redirect hop), caps response size and time, and converts the
body to plain text through the shared document extractor so HTML pages, JSON
files, and even PDFs or office documents served over HTTP all land in memory
in the same shape.
"""

from __future__ import annotations

import ipaddress
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings

from .documents import ExtractedDocument, extract_document

USER_AGENT = "MemoryWorksIngest/1.0 (+https://orgmemory.vercel.app)"
MAX_FETCH_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 25.0

ALLOWED_CONTENT_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
    "application/pdf",
    "application/rtf",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.oasis.opendocument",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/octet-stream",
)


class WebFetchError(ValueError):
    pass


def _validate_public_url(url: str) -> str:
    """Reject non-web schemes and SSRF targets, including every DNS answer."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebFetchError("Only public http(s) URLs can be ingested")
    if parsed.username or parsed.password:
        raise WebFetchError("URLs with embedded credentials cannot be ingested")
    if not settings.connector_custom_mcp_allow_private_networks:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, None)}
        except socket.gaierror as exc:
            raise WebFetchError(f"Hostname {parsed.hostname} could not be resolved") from exc
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if not ip.is_global:
                raise WebFetchError("Web ingestion cannot target private or special-use addresses")
    return url


def fetch_web_document(url: str) -> tuple[ExtractedDocument, dict[str, Any]]:
    """Fetch one public URL and extract text plus fetch metadata."""
    current = url.strip()
    if "://" not in current:
        current = "https://" + current
    fetched_at = datetime.now(UTC).isoformat()
    hops = 0
    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        while True:
            # Re-resolve and re-validate immediately before the request so a
            # hostname cannot swing to a private address between hops.
            _validate_public_url(current)
            try:
                response = client.get(current)
            except httpx.HTTPError as exc:
                raise WebFetchError(f"Fetching {current} failed: {exc}") from exc
            if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location", "")
                if not location:
                    raise WebFetchError("The server redirected without a target")
                hops += 1
                if hops > MAX_REDIRECTS:
                    raise WebFetchError("Too many redirects while ingesting the URL")
                current = urljoin(current, location)
                continue
            break

    if response.status_code >= 400:
        raise WebFetchError(f"The server returned HTTP {response.status_code}")
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type and not content_type.startswith(ALLOWED_CONTENT_PREFIXES):
        raise WebFetchError(f"Unsupported content type {content_type!r} for ingestion")
    body = response.content
    if len(body) > MAX_FETCH_BYTES:
        raise WebFetchError("The response body exceeds the 10MB fetch limit")
    filename = urlparse(current).path.rsplit("/", 1)[-1] or "page.html"
    if "." not in filename:
        filename = {"text/html": "page.html", "application/pdf": "document.pdf"}.get(
            content_type, "page.txt"
        )
    document = extract_document(filename, body)
    metadata = {
        "requested_url": url.strip(),
        "final_url": str(response.url),
        "http_status": response.status_code,
        "content_type": content_type,
        "fetched_at": fetched_at,
    }
    return document, metadata
