"""Tests for evidence_client.py (offline: no network, no OpenAI calls).

Run with:  python test_evidence_client.py   (or: pytest test_evidence_client.py)

Most tests connect the official mcp.Client to our MCPServer in-process. One
test launches the real server as a stdio subprocess under the no-network
guard from test_mcp_server.py. Transport-failure tests use commands that
cannot start or that exit without speaking MCP.
"""

import contextlib
import hashlib
import io
import sys
from types import SimpleNamespace

from mcp import StdioServerParameters

import evidence_client
from evidence import _result
from evidence_client import fetch_evidence, validate_tool_result
from mcp_evidence_server import server
from test_mcp_server import NO_NETWORK_LAUNCHER, PROJECT_DIR, SYN001_TEXT, SYN001_URL

SYN001_SHA = hashlib.sha256(SYN001_TEXT.encode("utf-8")).hexdigest()


def fetch_capturing_stderr(*args, **kwargs):
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        records = fetch_evidence(*args, **kwargs)
    return records, stderr.getvalue()


def contains(error, error_type):
    """True if error is error_type or an ExceptionGroup containing one."""
    if isinstance(error, BaseExceptionGroup):
        return any(contains(inner, error_type) for inner in error.exceptions)
    return isinstance(error, error_type)


# --- Retrieval through a real MCP client session ---

def test_syn001_ok_through_in_process_server():
    records, warnings = fetch_capturing_stderr(["SYN001"], server=server)
    evidence = records["SYN001"]
    assert evidence["status"] == "OK"
    assert evidence["source_url"] == SYN001_URL
    assert evidence["text"] == SYN001_TEXT
    assert evidence["sha256"] == SYN001_SHA
    assert evidence["truncated"] is False
    assert warnings == ""


def test_syn001_through_real_stdio_subprocess_without_network():
    params = StdioServerParameters(command=sys.executable, args=["-c", NO_NETWORK_LAUNCHER],
                                   cwd=str(PROJECT_DIR))
    records, warnings = fetch_capturing_stderr(["SYN001", "APP001"], server=params)
    assert records["SYN001"]["status"] == "OK"
    assert records["SYN001"]["text"] == SYN001_TEXT
    assert records["APP001"]["status"] == "UNAVAILABLE"
    assert "WARNING" not in warnings


def test_server_statuses_pass_through_and_every_id_gets_a_record():
    records, _ = fetch_capturing_stderr(["APP001", "../SYN001", "SYN001"], server=server)
    assert list(records) == ["APP001", "../SYN001", "SYN001"]   # request order kept
    assert records["APP001"]["status"] == "UNAVAILABLE"
    assert records["APP001"]["reason"] == "No approved evidence source"
    assert records["../SYN001"]["status"] == "REJECTED"
    assert records["SYN001"]["status"] == "OK"


def test_one_client_session_for_all_ids():
    created = []
    original = evidence_client.Client

    class CountingClient(original):
        def __init__(self, *args, **kwargs):
            created.append(args)
            super().__init__(*args, **kwargs)

    evidence_client.Client = CountingClient
    try:
        records, _ = fetch_capturing_stderr(["SYN001", "APP001", "APP002", "SYN001"],
                                            server=server)
    finally:
        evidence_client.Client = original
    assert len(created) == 1
    assert list(records) == ["SYN001", "APP001", "APP002"]   # duplicates fetched once


def test_no_ids_never_starts_a_server():
    unstartable = StdioServerParameters(command="/nonexistent/mcp-server")
    records, warnings = fetch_capturing_stderr([], server=unstartable)
    assert records == {} and warnings == ""


# --- Transport failures: fall back with a warning ---

def test_server_that_cannot_start_falls_back_with_warning():
    unstartable = StdioServerParameters(command="/nonexistent/mcp-server")
    records, warnings = fetch_capturing_stderr(["SYN001", "APP001"], server=unstartable)
    for submission_id in ("SYN001", "APP001"):
        evidence = records[submission_id]
        assert evidence["status"] == "UNAVAILABLE"
        assert evidence["reason"] == "MCP transport failed: FileNotFoundError"
        assert evidence["text"] is None
    assert "WARNING: MCP transport failed: FileNotFoundError" in warnings
    assert "description-only" in warnings


def test_server_that_exits_without_speaking_mcp_falls_back_with_warning():
    # mcp 2.3.0 reports this as an ExceptionGroup wrapping MCPError("Connection closed").
    dead = StdioServerParameters(command=sys.executable, args=["-c", "pass"])
    records, warnings = fetch_capturing_stderr(["SYN001"], server=dead, read_timeout_seconds=10)
    assert records["SYN001"]["status"] == "UNAVAILABLE"
    assert records["SYN001"]["reason"] == "MCP transport failed: MCPError"
    assert "WARNING: MCP transport failed: MCPError" in warnings


# --- Programming errors are not swallowed ---

def test_programming_error_propagates():
    original = evidence_client.validate_tool_result

    def broken(submission_id, result):
        raise KeyError("bug in adapter")

    evidence_client.validate_tool_result = broken
    try:
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            fetch_evidence(["SYN001"], server=server)
        raise AssertionError("expected the KeyError to propagate")
    except BaseException as error:   # may arrive wrapped in an ExceptionGroup
        assert contains(error, KeyError), repr(error)
        assert not contains(error, AssertionError)
        assert "WARNING" not in stderr.getvalue()
    finally:
        evidence_client.validate_tool_result = original


# --- validate_tool_result: never trusts the tool result's shape ---

def tool_result(structured_content, is_error=False):
    return SimpleNamespace(is_error=is_error, structured_content=structured_content)


def ok_record(**overrides):
    record = _result("SYN001", SYN001_URL, "OK", text=SYN001_TEXT,
                     retrieved_at="2026-01-01T00:00:00+00:00")
    record.update(overrides)
    return record


def test_validate_accepts_well_formed_ok_result():
    assert validate_tool_result("SYN001", tool_result(ok_record())) == ok_record()


def test_validate_turns_bad_results_into_unavailable():
    cases = {
        "MCP tool returned an error": tool_result(ok_record(), is_error=True),
        "Malformed MCP tool result": tool_result({"status": "OK", "text": "x"}),
        "MCP tool result is for a different submission": tool_result(
            ok_record(submission_id="SYN002")),
        "Unknown evidence status": tool_result(ok_record(status="APPROVED")),
        "OK evidence has no text": tool_result(ok_record(text="  ")),
        "Evidence text exceeds size limit": tool_result(ok_record(text="x" * 2001)),
        "Evidence sha256 does not match text": tool_result(ok_record(sha256="0" * 64)),
    }
    for reason, result in cases.items():
        evidence = validate_tool_result("SYN001", result)
        assert evidence["status"] == "UNAVAILABLE", reason
        assert evidence["reason"] == reason, (reason, evidence["reason"])
        assert evidence["text"] is None and evidence["submission_id"] == "SYN001", reason
    assert validate_tool_result("SYN001", tool_result(None))["reason"] == \
        "Malformed MCP tool result"


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\nAll {len(tests)} tests passed.")
