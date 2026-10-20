"""Single-owner private MCP tools. Public mode never loads a KFA session."""
import asyncio
import os
import time

from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.exceptions import ToolError

from kfa_lookup import Job, KfaClient, LookupError, Session


class OwnerVerifier(JWTVerifier):
    def __init__(self, *, owner: str, **kwargs):
        self.owner = owner
        super().__init__(**kwargs)

    async def verify_token(self, token):
        verified = await super().verify_token(token)
        if (verified is None or verified.claims.get("sub") != self.owner
                or not verified.expires_at or verified.expires_at <= time.time()):
            return None
        return verified


class OwnerGitHubProvider(GitHubProvider):
    """OAuth login with an immutable GitHub account ID allowlist."""
    def __init__(self, *, owner: str, **kwargs):
        self.owner = owner
        super().__init__(required_scopes=["read:user"], **kwargs)

    async def load_access_token(self, token):
        verified = await super().load_access_token(token)
        if verified is None or verified.claims.get("sub") != self.owner:
            return None
        # The proxy verifies its signed token, then revalidates the GitHub identity.
        # Preserve that signed token's expiry for the tool-level guard too.
        try:
            expiry = int(self.jwt_issuer.verify_token(token)["exp"])
        except Exception:
            return None
        if expiry <= time.time():
            return None
        verified.expires_at = expiry
        verified.scopes = [*verified.scopes, "assignments:read"]
        return verified


def private_auth():
    if os.environ.get("KFA_PRIVATE_ENABLED") != "1":
        return None
    if os.environ.get("KFA_AUTH_MODE", "jwt") == "github":
        required = ("KFA_OWNER_SUB", "KFA_GITHUB_CLIENT_ID", "KFA_GITHUB_CLIENT_SECRET")
        if any(not os.environ.get(k) for k in required):
            raise RuntimeError("GitHub mode requires owner and OAuth application configuration")
        return OwnerGitHubProvider(owner=os.environ["KFA_OWNER_SUB"],
            client_id=os.environ["KFA_GITHUB_CLIENT_ID"],
            client_secret=os.environ["KFA_GITHUB_CLIENT_SECRET"],
            base_url="https://joinkfa-mcp.onrender.com",
            redirect_path="/auth/callback",
            allowed_client_redirect_uris=["https://chatgpt.com/connector_platform/oauth_redirect",
                                          "https://chat.openai.com/aip/*"],
            require_authorization_consent=True)
    if os.environ.get("KFA_AUTH_MODE", "jwt") != "jwt":
        raise RuntimeError("Unsupported authentication mode")
    required = ("KFA_SESSION_FILE", "KFA_OWNER_SUB", "KFA_JWT_PUBLIC_KEY_FILE",
                "KFA_JWT_ISSUER", "KFA_JWT_AUDIENCE")
    if any(not os.environ.get(k) for k in required):
        raise RuntimeError("Private mode requires session, owner and JWT configuration")
    from pathlib import Path
    return OwnerVerifier(owner=os.environ["KFA_OWNER_SUB"],
        public_key=Path(os.environ["KFA_JWT_PUBLIC_KEY_FILE"]).read_text(encoding="utf-8"),
        issuer=os.environ["KFA_JWT_ISSUER"], audience=os.environ["KFA_JWT_AUDIENCE"],
        algorithm="RS256", required_scopes=["assignments:read"])


def register_private_tools(mcp):
    jobs = {}
    lock = asyncio.Lock()

    def session():
        # Defense in depth, including direct/in-process MCP calls.
        token = get_access_token()
        if (not token or token.claims.get("sub") != os.environ.get("KFA_OWNER_SUB")
                or not token.expires_at or token.expires_at <= time.time()
                or "assignments:read" not in token.scopes):
            raise ToolError("AUTH_REQUIRED")
        try:
            return Session.load(os.environ.get("KFA_SESSION_FILE", "/etc/secrets/kfa-session.json"))
        except LookupError as exc:
            raise ToolError(str(exc)) from None

    def cleanup():
        for key in list(jobs):
            if time.time() - jobs[key].created > 3600:
                del jobs[key]

    def get_job(job_id):
        cleanup()
        if job_id not in jobs:
            raise ToolError("JOB_EXPIRED_OR_NOT_FOUND")
        return jobs[job_id]

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def referee_auth_status() -> dict:
        """현재 서버에 안전하게 설정된 본인 JOIN KFA 로그인 상태를 확인합니다."""
        s = session()
        try:
            async with lock, KfaClient(s) as client:
                await client.authenticate(s.name)
            return {"authenticated": True, "name": s.name, "lookup_scope": "visible_match_referees_by_name"}
        except LookupError as exc:
            return {"authenticated": False, "error": str(exc)}

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def start_referee_lookup(name: str, year: int = 2026,
                                   competition_ids: list[str] | None = None,
                                   month: int | None = None) -> dict:
        """로그인 계정에서 열람 가능한 경기의 심판을 이름으로 검색합니다. 다른 심판 이름도 가능합니다.

        competition_ids 생략 시 해당 연도의 축구 대회 목록을 탐색합니다.
        month(1~12)를 지정하면 해당 월만 조회합니다. continue로 끝까지 진행해야 합니다.
        완성된 결과도 해당 목록 범위이며 전국의 미공개 배정 전체를 보장하지 않습니다.
        비밀번호, 쿠키, 사용자 ID를 인자로 받지 않습니다.
        """
        s = session()
        try:
            async with lock, KfaClient(s) as client:
                await client.authenticate()
                cleanup()
                if len(jobs) >= 3:
                    raise LookupError("TOO_MANY_JOBS_CANCEL_OR_WAIT")
                job = Job(name, year, competition_ids, month=month)
                jobs[job.id] = job
                return job.result()
        except LookupError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def continue_referee_lookup(job_id: str, request_budget: int = 5) -> dict:
        """조회 작업을 조금씩 진행합니다. complete=false 또는 error이면 최종 건수가 아닙니다."""
        s = session()
        async with lock, KfaClient(s) as client:
            job = get_job(job_id)
            try:
                return await job.advance(client, request_budget)
            except LookupError as exc:
                raise ToolError(str(exc)) from None

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def referee_lookup_results(job_id: str, offset: int = 0, limit: int = 50) -> dict:
        """확인된 배정 경기를 페이지별로 반환합니다. 역할별 합은 경기 수와 다를 수 있습니다."""
        s = session()
        if offset < 0 or not 1 <= limit <= 100:
            raise ToolError("INVALID_PAGE")
        async with lock, KfaClient(s) as client:
            job = get_job(job_id)
            try:
                await client.authenticate()
            except LookupError as exc:
                raise ToolError(str(exc)) from None
            return {**job.result(), "matches": job.matches[offset:offset + limit],
                    "next_offset": offset + limit if offset + limit < len(job.matches) else None}

    @mcp.tool(annotations={"destructiveHint": False, "openWorldHint": False})
    async def cancel_referee_lookup(job_id: str) -> dict:
        """서버 메모리의 임시 조회 결과를 제거합니다. JOIN KFA의 배정은 변경하지 않습니다."""
        session()
        async with lock:
            jobs.pop(job_id, None)
        return {"cancelled": True}
