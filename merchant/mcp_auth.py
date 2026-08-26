import jwt as pyjwt
from fastapi import HTTPException
from mcp.server.mcpserver import Context
from merchant.oauth.routes import JWT_SECRET, REVOKED_JTIS


def get_claims_from_context(ctx: Context, required_scope: str) -> dict:
    request = ctx.request_context.request
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = auth_header.removeprefix("Bearer ")
    try:
        claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(401, f"Invalid token: {e}")

    if claims.get("jti") in REVOKED_JTIS:
        raise HTTPException(401, "Token has been revoked")

    scopes = claims.get("scope", "").split()
    if required_scope not in scopes:
        raise HTTPException(403, f"Missing scope: {required_scope}")
    return claims
