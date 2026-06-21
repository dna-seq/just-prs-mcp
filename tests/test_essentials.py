"""Essentials behavior + structured output via result.data (typed objects)."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError


async def test_list_recipes(essentials_client):
    result = await essentials_client.call_tool("list_recipes", {})
    names = {r.name for r in result.data}
    assert {"classic-sponge", "chocolate-fudge", "lemon-drizzle"} <= names


async def test_get_recipe(essentials_client):
    result = await essentials_client.call_tool("get_recipe", {"name": "classic-sponge"})
    assert result.data.name == "classic-sponge"
    assert result.data.layers == 2


async def test_get_recipe_unknown(essentials_client):
    with pytest.raises(ToolError):
        await essentials_client.call_tool("get_recipe", {"name": "nope"})


async def test_scale_recipe_extended(extended_client):
    result = await extended_client.call_tool(
        "scale_recipe", {"name": "classic-sponge", "servings": 16}
    )
    assert result.data.servings == 16


async def test_scale_recipe_rejects_bad_servings(extended_client):
    with pytest.raises(ToolError):
        await extended_client.call_tool(
            "scale_recipe", {"name": "classic-sponge", "servings": 0}
        )
