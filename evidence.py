"""Read-only evidence retrieval for policy screening (M07.1a).

get_evidence() validates a source URL, calls an injected fetcher, and returns
the evidence text with provenance. It never performs network access itself:
the caller supplies `fetcher(url) -> str`.

Evidence text is UNTRUSTED DATA. This module never interprets it; it only
validates where it comes from, limits its size, and records a hash.

Statuses:
    OK           evidence retrieved
    UNAVAILABLE  URL was valid, but the fetcher failed or returned no text
    REJECTED     URL failed validation; the fetcher was never called
"""

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset({"evidence.example.com"})   # synthetic, reserved domain
MAX_EVIDENCE_CHARS = 2000

STATUS_OK = "OK"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_REJECTED = "REJECTED"


def url_rejection_reason(source_url):
    """Return why the URL is not allowed, or None if it is allowed."""
    if not isinstance(source_url, str) or not source_url:
        return "URL must be a non-empty string"
    if any(ch.isspace() or ord(ch) < 32 or ch == "\\" for ch in source_url):
        return "URL contains whitespace, control or backslash characters"

    try:
        parts = urlsplit(source_url)
        port = parts.port            # raises ValueError for an invalid port
    except ValueError:
        return "Malformed URL"

    if parts.scheme != "https":
        return "URL must use https"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return "URL must not contain credentials"
    if not parts.hostname:
        return "URL has no hostname"
    if parts.hostname not in ALLOWED_HOSTS:
        return f"Host not in allowlist: {parts.hostname}"
    if port not in (None, 443):
        return f"Port not allowed: {port}"
    return None


def _result(submission_id, source_url, status, reason=None,
            text=None, retrieved_at=None, truncated=False):
    return {
        "submission_id": submission_id,
        "source_url": source_url,
        "status": status,
        "reason": reason,
        "text": text,
        "retrieved_at": retrieved_at,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None,
        "truncated": truncated,
    }


def get_evidence(submission_id, source_url, fetcher):
    """Validate the URL, fetch evidence through `fetcher`, and record provenance.

    Never raises for bad URLs or fetcher failures; the status says what happened.
    """
    reason = url_rejection_reason(source_url)
    if reason is not None:
        return _result(submission_id, source_url, STATUS_REJECTED, reason=reason)

    retrieved_at = datetime.now(timezone.utc).isoformat()
    try:
        text = fetcher(source_url)
    except Exception as error:
        return _result(submission_id, source_url, STATUS_UNAVAILABLE,
                       reason=f"Fetcher failed: {type(error).__name__}",
                       retrieved_at=retrieved_at)

    if not isinstance(text, str) or not text.strip():
        return _result(submission_id, source_url, STATUS_UNAVAILABLE,
                       reason="Fetcher returned no text", retrieved_at=retrieved_at)

    truncated = len(text) > MAX_EVIDENCE_CHARS
    return _result(submission_id, source_url, STATUS_OK,
                   text=text[:MAX_EVIDENCE_CHARS], retrieved_at=retrieved_at,
                   truncated=truncated)
