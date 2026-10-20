import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastmcp import FastMCP
from fastmcp import Client

from private_tools import OwnerVerifier, private_auth, register_private_tools


@pytest.fixture
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.PEM,
                                              serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


def token(private, **changes):
    claims = {"sub": "owner", "iss": "test-issuer", "aud": "test-audience",
              "exp": time.time() + 300, "scope": "assignments:read"}
    claims.update(changes)
    return jwt.encode(claims, private, algorithm="RS256")


def verifier(public):
    return OwnerVerifier(owner="owner", public_key=public, issuer="test-issuer",
                         audience="test-audience", algorithm="RS256", required_scopes=["assignments:read"])


@pytest.mark.asyncio
async def test_owner_signature_audience_scope_and_expiry(keys):
    private, public = keys
    auth = verifier(public)
    assert await auth.verify_token(token(private))
    for changes in [{"sub": "other"}, {"aud": "wrong"}, {"iss": "wrong"},
                    {"scope": "other"}, {"exp": 1}, {"exp": None}]:
        assert await auth.verify_token(token(private, **changes)) is None
    assert await auth.verify_token("not-a-token") is None


def test_private_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("KFA_PRIVATE_ENABLED", "1")
    monkeypatch.delenv("KFA_SESSION_FILE", raising=False)
    with pytest.raises(RuntimeError):
        private_auth()
    monkeypatch.setenv("KFA_PRIVATE_ENABLED", "0")
    assert private_auth() is None


@pytest.mark.asyncio
async def test_private_http_requires_auth(keys):
    _, public = keys
    mcp = FastMCP("test", auth=verifier(public))
    register_private_tools(mcp)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp.http_app()),
                                base_url="http://localhost") as client:
        result = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert result.status_code == 401
        assert "엄세훈" not in result.text


@pytest.mark.asyncio
async def test_private_tools_not_bypassed_by_in_process_client():
    mcp = FastMCP("test", mask_error_details=True)
    register_private_tools(mcp)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="AUTH_REQUIRED"):
            await client.call_tool("referee_auth_status", {})


@pytest.mark.asyncio
async def test_public_mode_keeps_original_tools(monkeypatch):
    monkeypatch.delenv("KFA_PRIVATE_ENABLED", raising=False)
    import server
    async with Client(server.mcp) as client:
        names = {t.name for t in await client.list_tools()}
        assert names == {"list_sources", "inspect_public_page"}
