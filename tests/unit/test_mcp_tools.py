import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from gemini_remote.contracts.models import SendMode
from gemini_remote.gateway.mcp import create_mcp
from gemini_remote.storage.jobs import JobStore


@pytest.fixture
def store(tmp_path):
    s = JobStore(tmp_path / "jobs.sqlite3")
    yield s
    s.close()


async def call(store, name, args, default_mode=SendMode.TASK):
    server = create_mcp(store, default_mode, ["127.0.0.1:*"])
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await client.call_tool(name, args)


async def test_exactly_three_tools_with_read_write_hints(store):
    server = create_mcp(store, SendMode.TASK, ["127.0.0.1:*"])
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"gemini_send", "gemini_status", "gemini_result"}
    assert tools["gemini_send"].annotations.readOnlyHint is False
    assert tools["gemini_status"].annotations.readOnlyHint is True
    assert tools["gemini_result"].annotations.readOnlyHint is True


@pytest.mark.parametrize(("default", "explicit", "expected"),
                         [(SendMode.TASK, None, "task"), (SendMode.CHAT, None, "chat"), (SendMode.TASK, "chat", "chat")])
async def test_send_returns_job_id_and_uses_configured_default_mode(store, default, explicit, expected):
    args = {"task": "plan a trip", "idempotency_key": "k1"}
    if explicit:
        args["mode"] = explicit
    sent = await call(store, "gemini_send", args, default_mode=default)
    assert not sent.isError
    out = sent.structuredContent
    assert out["status"] == "queued" and out["mode"] == expected and out["created"] is True
    assert store.status(out["job_id"])["mode"] == expected


async def test_status_and_result_of_a_queued_job(store):
    sent = await call(store, "gemini_send", {"task": "t", "idempotency_key": "k1"})
    job_id = sent.structuredContent["job_id"]
    status = await call(store, "gemini_status", {"job_id": job_id})
    assert status.structuredContent["status"] == "queued"
    result = await call(store, "gemini_result", {"job_id": job_id})
    assert result.structuredContent["result"] is None


async def test_errors_come_back_as_tool_errors(store):
    await call(store, "gemini_send", {"task": "t", "idempotency_key": "k1"})
    conflict = await call(store, "gemini_send", {"task": "other", "idempotency_key": "k1"})
    assert conflict.isError
    assert "idempotency_key_reused" in conflict.content[0].text
    missing = await call(store, "gemini_status", {"job_id": "nope"})
    assert missing.isError
    bad_mode = await call(store, "gemini_send", {"task": "t", "idempotency_key": "k2", "mode": "deep"})
    assert bad_mode.isError
