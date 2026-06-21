"""KEY-GATED — "Bakery Cloud" tools that require an API key.

These stand in for a real remote service. They are ALWAYS listed (so the
multi-user HTTP path is safe and discoverable) and enforce auth PER CALL via
``resolve_api_key``. If no key is resolvable for the current request, they
return a friendly ``OpResult`` instead of raising — never a global state flip,
so one client can't ride another client's credential.
"""

from __future__ import annotations

import uuid

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from mcp_template.auth import (
    GATED_TAG,
    SessionKeyStore,
    require_key,
    unauthenticated_result,
)
from mcp_template.models import OpResult
from mcp_template.settings import Settings


def register_bakery_cloud(
    mcp: FastMCP, settings: Settings, store: SessionKeyStore
) -> None:
    """Register the key-gated Bakery Cloud tools (tag: bakery_cloud)."""

    @mcp.tool(
        tags={GATED_TAG},
        annotations=ToolAnnotations(
            title="Order a custom cake",
            readOnlyHint=False,
            openWorldHint=True,
        ),
    )
    def order_custom_cake(recipe: str, message: str, ctx: Context) -> OpResult:
        """Place a custom cake order with the Bakery Cloud (requires an API key)."""
        key = require_key(ctx, settings, store)
        if key is None:
            return unauthenticated_result(settings)
        order_id = f"order_{uuid.uuid4().hex[:10]}"
        return OpResult(
            success=True,
            message=f"Order placed for '{recipe}'.",
            data={"order_id": order_id, "recipe": recipe, "note": message},
        )

    @mcp.tool(
        tags={GATED_TAG},
        annotations=ToolAnnotations(
            title="Delivery status",
            readOnlyHint=True,
            openWorldHint=True,
        ),
    )
    def delivery_status(order_id: str, ctx: Context) -> OpResult:
        """Check the delivery status of a Bakery Cloud order (requires an API key)."""
        key = require_key(ctx, settings, store)
        if key is None:
            return unauthenticated_result(settings)
        return OpResult(
            success=True,
            message=f"Order {order_id} is out for delivery.",
            data={"order_id": order_id, "status": "out_for_delivery"},
        )
