"""Pydantic models used as structured tool inputs/outputs.

Returning Pydantic models from tools gives clients a typed output schema
(``result.data`` on the client side) instead of opaque text.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Recipe(BaseModel):
    """A cake recipe."""

    name: str = Field(description="Recipe name, e.g. 'classic-sponge'.")
    layers: int = Field(description="Number of cake layers.", ge=1)
    servings: int = Field(description="How many servings this recipe yields.", ge=1)
    ingredients: list[str] = Field(description="Ingredient lines.")
    steps: list[str] = Field(description="Ordered preparation steps.")
    bake_minutes: int = Field(description="Bake time in minutes.", ge=1)
    temp_c: int = Field(description="Oven temperature in Celsius.", ge=1)


class BakeResult(BaseModel):
    """Outcome of a (simulated) baking run."""

    recipe: str = Field(description="The recipe that was baked.")
    status: str = Field(description="Final status, e.g. 'done'.")
    minutes: int = Field(description="Total minutes the bake took.")
    temp_c: int = Field(description="Oven temperature actually used (may be clamped).")
    message: str = Field(description="Human-readable summary.")
    bake_id: str = Field(description="Identifier usable with continue_bake.")


class AuthResult(BaseModel):
    """Result of an authenticate() call (scoped to the calling session)."""

    authenticated: bool = Field(description="Whether the key was accepted.")
    unlocked_tools: list[str] = Field(
        default_factory=list,
        description="Gated tools now usable in THIS session.",
    )
    message: str = Field(description="Human-readable summary.")


class OpResult(BaseModel):
    """Generic success/failure envelope for fallible tools.

    Tools return this (with ``success=False``) instead of raising, so an agent
    gets an actionable message rather than a protocol-level error.
    """

    success: bool = Field(description="Whether the operation succeeded.")
    message: str = Field(description="Human-readable summary or error.")
    data: dict | None = Field(default=None, description="Optional payload.")
