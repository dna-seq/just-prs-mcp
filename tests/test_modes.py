"""The exposed tool surface depends on the selected mode."""

from __future__ import annotations


async def _tool_names(client) -> set[str]:
    return {t.name for t in await client.list_tools()}


async def test_essentials_surface(essentials_client):
    names = await _tool_names(essentials_client)
    # Always-on essentials + auth + (listed) gated tools.
    assert {"list_recipes", "get_recipe", "bake_cake", "authenticate"} <= names
    # Extended-only tools must NOT be present in essentials mode.
    assert "scale_recipe" not in names
    assert "suggest_pairings" not in names
    assert "continue_bake" not in names


async def test_extended_superset(essentials_client, extended_client):
    essentials = await _tool_names(essentials_client)
    extended = await _tool_names(extended_client)
    assert essentials <= extended
    assert {"scale_recipe", "suggest_pairings", "continue_bake"} <= extended
