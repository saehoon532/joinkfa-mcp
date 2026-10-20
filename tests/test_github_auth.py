import time
from unittest.mock import AsyncMock, Mock

import pytest
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.github import GitHubProvider
from private_tools import OwnerGitHubProvider, private_auth


@pytest.mark.asyncio
async def test_github_restricts_numeric_owner_and_preserves_expiry(monkeypatch):
    # Bypass constructor/network/storage; exercise our policy around provider validation.
    provider = object.__new__(OwnerGitHubProvider)
    provider.owner = "123"
    issuer = Mock()
    monkeypatch.setattr(OwnerGitHubProvider, "jwt_issuer", property(lambda self: issuer))
    expiry = int(time.time()) + 300
    issuer.verify_token.return_value = {"exp": expiry}
    upstream = AsyncMock(return_value=AccessToken(token="test", client_id="123",
        scopes=["read:user"], claims={"sub": "123"}))
    monkeypatch.setattr(GitHubProvider, "load_access_token", upstream)
    result = await provider.load_access_token("test-proxy-jwt")
    assert result.expires_at == expiry
    assert "assignments:read" in result.scopes
    upstream.return_value = AccessToken(token="test", client_id="456", scopes=["read:user"], claims={"sub": "456"})
    assert await provider.load_access_token("test-proxy-jwt") is None
    upstream.return_value = None
    assert await provider.load_access_token("test-proxy-jwt") is None


def test_github_config_missing_secret_fails_closed(monkeypatch):
    monkeypatch.setenv("KFA_PRIVATE_ENABLED", "1")
    monkeypatch.setenv("KFA_AUTH_MODE", "github")
    monkeypatch.delenv("KFA_GITHUB_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="OAuth"):
        private_auth()


@pytest.mark.asyncio
async def test_restore_only_existing_public_client_with_exact_redirect(monkeypatch):
    from pydantic import AnyUrl
    provider = object.__new__(OwnerGitHubProvider)
    monkeypatch.setattr(GitHubProvider, "get_client", AsyncMock(return_value=None))
    assert await provider.get_client("unknown") is None
    client = await provider.get_client("2d77d5b6-e1cc-49c9-b96d-ac011e3eb522")
    assert client.scope == "read:user"
    assert client.client_secret is None
    assert str(client.validate_redirect_uri(AnyUrl("https://chatgpt.com/connector/oauth/n9VBX4UUgSfk"))) == "https://chatgpt.com/connector/oauth/n9VBX4UUgSfk"
    with pytest.raises(Exception):
        client.validate_redirect_uri(AnyUrl("https://evil.example/callback"))
    with pytest.raises(Exception):
        client.validate_redirect_uri(AnyUrl("https://chatgpt.com/connector/oauth/other"))
