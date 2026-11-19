"""Tests for mcp_evidence_server.py (offline: stdio subprocess, no network).

Run with:  python test_mcp_server.py   (or: pytest test_mcp_server.py)

The protocol tests start the real server as a subprocess and talk to it with
the official MCP client over stdin/stdout. A Python audit hook inside the
server process makes any internet socket connection or DNS lookup fail, so a
passing run proves the server needs no network access.
"""

import asyncio
import hashlib
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

from mcp_evidence_server import get_app_evidence

PROJECT_DIR = Path(__file__).parent
SYN001_URL = "https://evidence.example.com/apps/SYN001"
SYN001_TEXT = ("Prizes are virtual rewards only. They cannot be exchanged "
               "for cash or other items of monetary value.")

# Started with `python -c`: install the no-network guard, then run the server.
NO_NETWORK_LAUNCHER = """
import socket, sys, runpy
def _guard(event, args):
    if event == "socket.getaddrinfo":
        raise RuntimeError("network blocked: DNS lookup")
    if event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError("network blocked: connect")
sys.addaudithook(_guard)
runpy.run_path("mcp_evidence_server.py", run_name="__main__")
"""


async def _with_session(action):
    params = StdioServerParameters(command=sys.executable, args=["-c", NO_NETWORK_LAUNCHER],
                                   cwd=str(PROJECT_DIR))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await action(session)


def call_tool(arguments):
    """Call get_app_evidence over a real MCP stdio session; return the CallToolResult."""
    return asyncio.run(_with_session(
        lambda session: session.call_tool("get_app_evidence", arguments)))


# --- MCP protocol: tools/list ---

def test_tools_list_exposes_exactly_one_tool():
    result = asyncio.run(_with_session(lambda session: session.list_tools()))
    assert [tool.name for tool in result.tools] == ["get_app_evidence"]

    tool = result.tools[0]
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert list(schema["properties"]) == ["submission_id"]   # no source_url parameter
    assert schema["properties"]["submission_id"]["type"] == "string"
    assert schema["required"] == ["submission_id"]
    assert tool.annotations.read_only_hint is True


# --- MCP protocol: tools/call ---

def test_tools_call_syn001_returns_fixture_evidence():
    result = call_tool({"submission_id": "SYN001"})
    assert result.is_error is False
    evidence = result.structured_content
    assert evidence["status"] == "OK"
    assert evidence["submission_id"] == "SYN001"
    assert evidence["source_url"] == SYN001_URL
    assert evidence["text"] == SYN001_TEXT
    assert evidence["sha256"] == hashlib.sha256(SYN001_TEXT.encode("utf-8")).hexdigest()
    assert evidence["truncated"] is False


def test_tools_call_unknown_id_is_unavailable():
    evidence = call_tool({"submission_id": "SYN999"}).structured_content
    assert evidence["status"] == "UNAVAILABLE"
    assert evidence["reason"] == "No approved evidence source"
    assert evidence["source_url"] is None and evidence["text"] is None


def test_tools_call_malformed_id_is_rejected():
    evidence = call_tool({"submission_id": "../SYN001"}).structured_content
    assert evidence["status"] == "REJECTED"
    assert evidence["reason"] == "Malformed submission_id"
    assert evidence["text"] is None


def test_client_cannot_choose_source_url():
    # mcp 2.3.0 ignores arguments that are not in the tool's input schema.
    result = call_tool({"submission_id": "SYN001", "source_url": "https://evil.com/x"})
    assert result.is_error is False
    evidence = result.structured_content
    assert evidence["source_url"] == SYN001_URL  # server-approved URL, not the client's
    assert "evil.com" not in str(evidence)


def test_no_network_guard_actually_blocks():
    """Proves the guard used by the protocol tests is real, not a no-op."""
    import subprocess
    probe = NO_NETWORK_LAUNCHER.replace(
        'runpy.run_path("mcp_evidence_server.py", run_name="__main__")',
        'import socket\n'
        'for attempt in (lambda: socket.getaddrinfo("example.com", 443),\n'
        '                lambda: socket.socket(socket.AF_INET).connect(("127.0.0.1", 9))):\n'
        '    try:\n'
        '        attempt(); print("NOT BLOCKED")\n'
        '    except RuntimeError as error:\n'
        '        print(error)\n')
    output = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                            cwd=PROJECT_DIR, timeout=30).stdout
    assert "network blocked: DNS lookup" in output, output
    assert "network blocked: connect" in output, output
    assert "NOT BLOCKED" not in output, output


# --- Tool function called directly (no subprocess) ---

def test_malformed_ids_are_rejected_without_lookup():
    for bad in ["", " SYN001", "SYN001\n", "a" * 33, "SYN/001", "SYN001?x=1", "https://evil.com"]:
        evidence = get_app_evidence(bad)
        assert evidence["status"] == "REJECTED", bad
        assert evidence["source_url"] is None and evidence["retrieved_at"] is None, bad


def test_direct_call_matches_evidence_result_structure():
    evidence = get_app_evidence("SYN001")
    assert set(evidence) == {"submission_id", "source_url", "status", "reason", "text",
                             "retrieved_at", "sha256", "truncated"}
    assert evidence["status"] == "OK" and evidence["text"] == SYN001_TEXT


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\nAll {len(tests)} tests passed.")
