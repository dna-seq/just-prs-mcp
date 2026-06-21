"""EXTENDED — extra tools registered ONLY when mode == "extended".

This is the "register on start" half of the hybrid pattern: these tools are not
part of the always-on essentials surface, so casual clients don't pay the
context cost. Power users opt in via ``CAKE_MODE=extended`` / ``--mode extended``.
"""

from __future__ import annotations

import uuid

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from mcp_template.models import BakeResult, Recipe
from mcp_template.settings import Settings
from mcp_template.tools.data import RECIPES, recipe_names


def register_extended(mcp: FastMCP, settings: Settings) -> None:
    """Register the extended-only cake tools."""

    @mcp.tool(
        tags={"extended"},
        annotations=ToolAnnotations(title="Scale recipe", readOnlyHint=True),
    )
    def scale_recipe(name: str, servings: int) -> Recipe:
        """Scale a recipe to a target number of servings (>= 1)."""
        if servings < 1:
            raise ToolError("servings must be >= 1.")
        base = RECIPES.get(name)
        if base is None:
            raise ToolError(
                f"Unknown recipe '{name}'. Available: {', '.join(recipe_names())}."
            )
        factor = servings / base.servings

        def scale_line(line: str) -> str:
            # Naive: scale a leading integer/float quantity if present.
            head, _, rest = line.partition(" ")
            try:
                qty = float(head.rstrip("g").rstrip("ml"))
            except ValueError:
                return line
            unit = head[len(str(int(qty))):] if head[-1].isalpha() else ""
            return f"{round(qty * factor)}{unit} {rest}".strip()

        return base.model_copy(
            update={
                "servings": servings,
                "ingredients": [scale_line(line) for line in base.ingredients],
            }
        )

    @mcp.tool(
        tags={"extended"},
        annotations=ToolAnnotations(title="Suggest pairings", readOnlyHint=True),
    )
    def suggest_pairings(name: str) -> list[str]:
        """Suggest drink/topping pairings for a recipe."""
        pairings = {
            "classic-sponge": ["English breakfast tea", "fresh strawberries"],
            "chocolate-fudge": ["espresso", "vanilla ice cream"],
            "lemon-drizzle": ["Earl Grey tea", "blueberries"],
        }
        if name not in pairings:
            raise ToolError(
                f"Unknown recipe '{name}'. Available: {', '.join(recipe_names())}."
            )
        return pairings[name]

    @mcp.tool(
        tags={"extended"},
        annotations=ToolAnnotations(title="Continue a bake", readOnlyHint=False),
    )
    def continue_bake(previous_bake_id: str, note: str) -> BakeResult:
        """Follow up on a previous bake (e.g. add frosting), referencing its id."""
        if not previous_bake_id.startswith("bake_"):
            raise ToolError("previous_bake_id must look like 'bake_...'.")
        return BakeResult(
            recipe="(continuation)",
            status="done",
            minutes=10,
            temp_c=0,
            message=f"Applied follow-up to {previous_bake_id}: {note}",
            bake_id=f"bake_{uuid.uuid4().hex[:12]}",
        )
