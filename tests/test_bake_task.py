"""The (simulated) long-running bake completes and reports progress."""

from __future__ import annotations


async def test_bake_cake_completes(essentials_client):
    result = await essentials_client.call_tool(
        "bake_cake", {"recipe": "chocolate-fudge"}
    )
    assert result.data.status == "done"
    assert result.data.recipe == "chocolate-fudge"
    assert result.data.bake_id.startswith("bake_")


async def test_bake_cake_reports_progress(essentials_client):
    seen: list[float] = []

    async def on_progress(progress: float, total: float | None, message: str | None):
        seen.append(progress)

    result = await essentials_client.call_tool(
        "bake_cake",
        {"recipe": "classic-sponge"},
        progress_handler=on_progress,
    )
    assert result.data.status == "done"
    assert len(seen) >= 1  # progress was reported during the bake


async def test_bake_cake_clamps_temperature(make_client):
    from mcp_template.settings import Settings

    settings = Settings(oven_max_temp_c=160)
    async with make_client(settings=settings) as client:
        result = await client.call_tool("bake_cake", {"recipe": "classic-sponge"})
        # classic-sponge bakes at 180; should be clamped to 160.
        assert result.data.temp_c == 160
