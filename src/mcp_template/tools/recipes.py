"""ESSENTIALS — tools present in every mode (no API key required).

These are declared in one ``register_essentials`` function so the same surface
is registered identically regardless of mode. Keeping this set small is the
whole point of the essentials/extended split: a smaller default tool list means
less context pollution for an agent.
"""

from __future__ import annotations

import uuid

import anyio
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from mcp_template.logging_setup import get_logger
from mcp_template.models import BakeResult, Recipe
from mcp_template.settings import Settings
from mcp_template.tools.data import RECIPES, recipe_names

log = get_logger()

# Total work units for the simulated bake (used for progress reporting).
_BAKE_STAGES = ["mixing", "preheating", "baking", "cooling"]


def register_essentials(mcp: FastMCP, settings: Settings) -> None:
    """Register the always-on cake tools, a resource, and a prompt."""

    @mcp.tool(
        annotations=ToolAnnotations(title="List recipes", readOnlyHint=True)
    )
    def list_recipes() -> list[Recipe]:
        """List all available cake recipes."""
        return [RECIPES[name] for name in recipe_names()]

    @mcp.tool(
        annotations=ToolAnnotations(title="Get recipe", readOnlyHint=True)
    )
    def get_recipe(name: str) -> Recipe:
        """Get a single cake recipe by name (e.g. 'classic-sponge')."""
        recipe = RECIPES.get(name)
        if recipe is None:
            raise ToolError(
                f"Unknown recipe '{name}'. Available: {', '.join(recipe_names())}."
            )
        return recipe

    @mcp.tool(
        task=True,
        annotations=ToolAnnotations(
            title="Bake a cake", readOnlyHint=False, idempotentHint=False
        ),
    )
    async def bake_cake(recipe: str, ctx: Context) -> BakeResult:
        """Bake a cake (long-running background task) with live progress updates.

        Runs as a real MCP background task (``task=True``): the client gets a task
        id immediately, polls for status, and receives this result when done.
        Uses FastMCP's in-memory task backend by default (no Redis). Set
        ``FASTMCP_DOCKET_URL=redis://...`` for distributed/persistent tasks.
        Progress is streamed via the Context so clients can show a progress bar.
        """
        spec = RECIPES.get(recipe)
        if spec is None:
            raise ToolError(
                f"Unknown recipe '{recipe}'. Available: {', '.join(recipe_names())}."
            )

        temp_c = min(spec.temp_c, settings.oven_max_temp_c)
        total = len(_BAKE_STAGES)
        for i, stage in enumerate(_BAKE_STAGES, start=1):
            await ctx.info(f"{stage} '{recipe}'...")
            await ctx.report_progress(progress=i, total=total)
            await anyio.sleep(0.01)  # stand-in for real work

        bake_id = f"bake_{uuid.uuid4().hex[:12]}"
        log.info("Baked %s (id=%s) at %d°C", recipe, bake_id, temp_c)
        return BakeResult(
            recipe=recipe,
            status="done",
            minutes=spec.bake_minutes,
            temp_c=temp_c,
            message=f"'{recipe}' is baked and cooling. Enjoy!",
            bake_id=bake_id,
        )

    @mcp.resource("resource://cakes/pantry")
    def pantry() -> str:
        """A quick overview of what's in the pantry (recipe catalog)."""
        lines = ["# Pantry / recipe catalog", ""]
        for name in recipe_names():
            r = RECIPES[name]
            lines.append(f"- **{name}** — {r.layers} layer(s), serves {r.servings}")
        return "\n".join(lines)

    @mcp.prompt
    def bake_a_cake(occasion: str = "a birthday") -> str:
        """Prompt template: pick and bake a cake for a given occasion."""
        return (
            f"I'd like to bake a cake for {occasion}. "
            "List the available recipes, recommend one, and bake it."
        )
