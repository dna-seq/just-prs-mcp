"""Auth is per-session: no cross-client key bleed, no exceptions."""

from __future__ import annotations


async def test_gated_tool_listed_but_blocked_without_key(essentials_client):
    names = {t.name for t in await essentials_client.list_tools()}
    assert "order_custom_cake" in names  # listed, not hidden

    result = await essentials_client.call_tool(
        "order_custom_cake", {"recipe": "classic-sponge", "message": "hi"}
    )
    assert result.data.success is False
    assert "authenticate" in result.data.message.lower()


async def test_authenticate_unlocks_within_session(essentials_client):
    auth = await essentials_client.call_tool(
        "authenticate", {"api_key": "cake_demo123"}
    )
    assert auth.data.authenticated is True

    result = await essentials_client.call_tool(
        "order_custom_cake", {"recipe": "classic-sponge", "message": "hi"}
    )
    assert result.data.success is True
    assert result.data.data["order_id"].startswith("order_")


async def test_invalid_key_rejected(essentials_client):
    auth = await essentials_client.call_tool("authenticate", {"api_key": "wrong"})
    assert auth.data.authenticated is False


async def test_session_isolation(make_client):
    """A key set in one session must NOT leak into another session."""
    async with make_client() as a, make_client() as b:
        await a.call_tool("authenticate", {"api_key": "cake_demo123"})

        # Session A is authenticated.
        ra = await a.call_tool(
            "order_custom_cake", {"recipe": "lemon-drizzle", "message": "x"}
        )
        assert ra.data.success is True

        # Session B never authenticated -> still blocked.
        rb = await b.call_tool(
            "order_custom_cake", {"recipe": "lemon-drizzle", "message": "x"}
        )
        assert rb.data.success is False
