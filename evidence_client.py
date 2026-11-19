"""MCP client adapter: fetch synthetic evidence for submissions (M07.2a).

fetch_evidence(submission_ids) opens ONE MCP client session to the app-evidence
server (mcp_evidence_server.py, stdio subprocess by default), calls the
get_app_evidence tool once per submission, and returns
{submission_id: evidence_record}. Our code calls the tool; the model never
chooses or invokes tools.

Every requested ID gets a record. Anything that is not trustworthy OK evidence
becomes a non-OK record, so the caller falls back to description-only review:
    - server says UNAVAILABLE / REJECTED   -> passed through unchanged
    - tool error or malformed tool result  -> UNAVAILABLE, with a reason
    - MCP transport failure                -> UNAVAILABLE, with a reason + stderr warning

Only TRANSPORT_ERRORS are handled. Any other exception (a programming error)
propagates, even when the MCP library wraps it in an ExceptionGroup.

Evidence text is UNTRUSTED DATA. This module never interprets it.
"""

import asyncio
import hashlib
import sys
from pathlib import Path

from mcp import Client, MCPError, StdioServerParameters

from evidence import (
    MAX_EVIDENCE_CHARS,
    STATUS_OK,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    _result,
)

PROJECT_DIR = Path(__file__).parent
SERVER_SCRIPT = PROJECT_DIR / "mcp_evidence_server.py"
TOOL_NAME = "get_app_evidence"
READ_TIMEOUT_SECONDS = 30

# Failures of the MCP connection itself: process cannot start (OSError),
# connection closed / request timed out / protocol error (MCPError).
TRANSPORT_ERRORS = (OSError, MCPError, TimeoutError)

EVIDENCE_KEYS = frozenset({"submission_id", "source_url", "status", "reason", "text",
                           "retrieved_at", "sha256", "truncated"})
KNOWN_STATUSES = frozenset({STATUS_OK, STATUS_UNAVAILABLE, STATUS_REJECTED})


def default_server_params():
    """Launch our own MCP server as a stdio subprocess with this interpreter."""
    return StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)],
                                 cwd=str(PROJECT_DIR))


def _unavailable(submission_id, reason):
    return _result(submission_id, None, STATUS_UNAVAILABLE, reason=reason)


def validate_tool_result(submission_id, result):
    """Turn one CallToolResult into an evidence record. Never trusts its shape."""
    if result.is_error:
        return _unavailable(submission_id, "MCP tool returned an error")

    evidence = result.structured_content
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_KEYS:
        return _unavailable(submission_id, "Malformed MCP tool result")
    if evidence["submission_id"] != submission_id:
        return _unavailable(submission_id, "MCP tool result is for a different submission")
    if evidence["status"] not in KNOWN_STATUSES:
        return _unavailable(submission_id, "Unknown evidence status")

    if evidence["status"] == STATUS_OK:
        text = evidence["text"]
        if not isinstance(text, str) or not text.strip():
            return _unavailable(submission_id, "OK evidence has no text")
        if len(text) > MAX_EVIDENCE_CHARS:
            return _unavailable(submission_id, "Evidence text exceeds size limit")
        if evidence["sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest():
            return _unavailable(submission_id, "Evidence sha256 does not match text")

    return dict(evidence)


async def _fetch_all(submission_ids, server, read_timeout_seconds, records):
    """Fill `records` in place, so evidence fetched before a failure is kept."""
    async with Client(server, read_timeout_seconds=read_timeout_seconds) as client:
        for submission_id in submission_ids:
            result = await client.call_tool(TOOL_NAME, {"submission_id": submission_id})
            records[submission_id] = validate_tool_result(submission_id, result)


def _first_leaf(error):
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


def fetch_evidence(submission_ids, server=None, read_timeout_seconds=READ_TIMEOUT_SECONDS):
    """Return {submission_id: evidence_record} for every requested ID.

    `server` is anything mcp.Client accepts: StdioServerParameters (default:
    our server as a subprocess) or, in tests, the MCPServer instance itself.
    One client session serves the whole run.
    """
    submission_ids = list(dict.fromkeys(submission_ids))   # dedupe, keep order
    if not submission_ids:
        return {}
    if server is None:
        server = default_server_params()

    records = {}
    transport_error = None
    try:
        asyncio.run(_fetch_all(submission_ids, server, read_timeout_seconds, records))
    except* TRANSPORT_ERRORS as group:      # anything else propagates
        transport_error = _first_leaf(group)

    if transport_error is not None:
        reason = f"MCP transport failed: {type(transport_error).__name__}"
        print(f"WARNING: {reason} ({transport_error}). "
              "Falling back to description-only review for submissions without evidence.",
              file=sys.stderr)
        for submission_id in submission_ids:
            records.setdefault(submission_id, _unavailable(submission_id, reason))

    return {submission_id: records[submission_id] for submission_id in submission_ids}
