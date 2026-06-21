"""Runtime, per-session authentication for key-gated tools.

Design goals (multi-user safe):

* The server ALWAYS boots — a missing key is never a startup error. This is
  required by Smithery's immutable-server / per-request dependency-injection
  model, and it is friendly for casual local users.
* A key is resolved PER REQUEST, never stored in a server-global mutable field.
  Resolution precedence:
    1. per-request HTTP header (``settings.api_key_header``)  -> multi-user safe
    2. Smithery-injected session config (``ctx.session_config``)
    3. per-session store keyed by ``ctx.session_id`` (set via ``authenticate``)
    4. ``CAKE_API_KEY`` env (single-tenant / local default)
* ``authenticate`` writes ONLY into the caller's own session slot, so one HTTP
  client can never read or clobber another client's key.

Anti-pattern (documented, off by default): ``mcp.enable(tags=...)`` to "unlock"
gated tools is SERVER-GLOBAL — it would expose tools to every connected client.
Safe only for single-tenant stdio. See ``register_stdio_only_unlock`` below.
"""

from __future__ import annotations

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from mcp_template.logging_setup import get_logger
from mcp_template.models import AuthResult, OpResult
from mcp_template.settings import Settings

log = get_logger()

# Tools tagged with this are key-gated.
GATED_TAG = "bakery_cloud"
GATED_TOOLS = ["order_custom_cake", "delivery_status"]


class SessionKeyStore:
    """Per-session API keys. The ONLY auth state — no shared/global key."""

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    @staticmethod
    def _sid(ctx: Context | None) -> str:
        sid = getattr(ctx, "session_id", None) if ctx else None
        return sid or "__local__"

    def set(self, ctx: Context | None, key: str) -> None:
        self._keys[self._sid(ctx)] = key

    def get(self, ctx: Context | None) -> str | None:
        return self._keys.get(self._sid(ctx))


def _header_key(settings: Settings) -> str | None:
    """Read the API key from the current HTTP request header, if any."""
    try:
        from fastmcp.server.dependencies import get_http_request

        request = get_http_request()
    except Exception:
        return None  # not an HTTP request (stdio / in-memory)
    return request.headers.get(settings.api_key_header)


def _smithery_key(ctx: Context | None) -> str | None:
    """Read a Smithery-injected per-request config key, if present."""
    cfg = getattr(ctx, "session_config", None)
    if cfg is None:
        return None
    return getattr(cfg, "api_key", None) or (
        cfg.get("api_key") if isinstance(cfg, dict) else None
    )


def resolve_api_key(
    ctx: Context | None, settings: Settings, store: SessionKeyStore
) -> str | None:
    """Resolve the API key for THIS request (see module docstring for order)."""
    return (
        _header_key(settings)
        or _smithery_key(ctx)
        or store.get(ctx)
        or settings.api_key
    )


def _valid(key: str) -> bool:
    """Dummy validation. Replace with a real check against your service."""
    return bool(key) and key.startswith("cake_")


def require_key(
    ctx: Context | None, settings: Settings, store: SessionKeyStore
) -> str | None:
    """Return the resolved key, or ``None`` if the caller must authenticate.

    Gated tools use this and return a friendly ``OpResult`` on ``None`` rather
    than raising, so agents get an actionable message.
    """
    return resolve_api_key(ctx, settings, store)


def unauthenticated_result(settings: Settings) -> OpResult:
    return OpResult(
        success=False,
        message=(
            "This tool needs an API key. Call `authenticate` with a key for this "
            f"session, send the `{settings.api_key_header}` header (HTTP), or set "
            "CAKE_API_KEY in the environment."
        ),
    )


def register_auth(mcp: FastMCP, settings: Settings, store: SessionKeyStore) -> None:
    """Register the always-on ``authenticate`` tool (per-session scope)."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Authenticate (this session)",
            readOnlyHint=False,
            idempotentHint=True,
            destructiveHint=False,
        )
    )
    def authenticate(api_key: str, ctx: Context) -> AuthResult:
        """Provide an API key to unlock the Bakery Cloud tools for THIS session.

        The key is stored only against your own session and is never shared with
        other clients. Demo keys start with ``cake_`` (e.g. ``cake_demo123``).
        """
        if not _valid(api_key):
            return AuthResult(
                authenticated=False,
                message="Invalid key. Demo keys must start with 'cake_'.",
            )
        store.set(ctx, api_key)
        log.info("Session %s authenticated", SessionKeyStore._sid(ctx))
        return AuthResult(
            authenticated=True,
            unlocked_tools=GATED_TOOLS,
            message="Authenticated. Bakery Cloud tools are now usable in this session.",
        )


def register_stdio_only_unlock(mcp: FastMCP, store: SessionKeyStore) -> None:
    """OPTIONAL, SINGLE-TENANT ONLY: hide gated tools until authenticated.

    This disables the gated tools at startup and re-enables them globally on a
    successful ``authenticate`` (emitting ``tools/list_changed``). Because
    ``mcp.enable`` is SERVER-GLOBAL, enabling for one client exposes the tools
    to ALL connected clients — so this is appropriate ONLY for single-tenant
    stdio. Do NOT use under multi-user HTTP. Not wired up by default.
    """
    mcp.disable(tags={GATED_TAG})
    # A real implementation would wrap `authenticate` to call
    # `mcp.enable(tags={GATED_TAG})` after storing the key. Left as a documented
    # pattern; the default per-call enforcement in resolve_api_key is the gate.
