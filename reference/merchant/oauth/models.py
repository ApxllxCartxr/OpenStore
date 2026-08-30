from pydantic import BaseModel


class AuthServerMetadata(BaseModel):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    revocation_endpoint: str
    jwks_uri: str
    scopes_supported: list[str]
    response_types_supported: list[str] = ["code"]
    grant_types_supported: list[str] = ["authorization_code", "refresh_token"]
    code_challenge_methods_supported: list[str] = ["S256"]
    token_endpoint_auth_methods_supported: list[str] = ["none"]  # public clients


class ClientRegistrationRequest(BaseModel):
    client_name: str
    redirect_uris: list[str]


class ClientRegistrationResponse(BaseModel):
    client_id: str
    client_name: str
    redirect_uris: list[str]


__all__ = [
    "AuthServerMetadata",
    "ClientRegistrationRequest",
    "ClientRegistrationResponse",
]