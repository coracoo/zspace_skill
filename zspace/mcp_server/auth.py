"""Static Bearer token verifier for MCP HTTP transport.

FastMCP wires this via `token_verifier=...`. One token, all clients share —
right-sized for trusted-LAN use. For per-client tokens or OAuth 2.1, swap
this class for a full provider implementation; the wiring in main.py does
not change.
"""
import hmac

from mcp.server.auth.provider import AccessToken, TokenVerifier


class StaticTokenVerifier(TokenVerifier):
    """Validate `Authorization: Bearer <token>` against a single configured token.

    Uses `hmac.compare_digest` to prevent timing attacks. Returns an
    `AccessToken` with `client_id="static"` and `scopes=["all"]` on match.
    """

    def __init__(self, token: str):
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token.encode(), self._token.encode()):
            return None
        return AccessToken(
            token=token,
            client_id="static",
            scopes=["all"],
        )