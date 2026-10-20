
import os
import httpx
from bs4 import BeautifulSoup
from fastmcp import FastMCP
from private_tools import private_auth, register_private_tools

auth = private_auth()
mcp = FastMCP("JOIN KFA MCP", auth=auth, mask_error_details=True)
if auth is not None:
    register_private_tools(mcp)

# 공개 대회 페이지의 접근성을 확인하기 위한 초기 주소
SOURCES = {
    "joinkfa": "https://www.joinkfa.com/",
    "kfa": "https://www.kfa.or.kr/",
}

@mcp.tool()
def list_sources() -> dict:
    """현재 등록된 공개 데이터 소스를 조회합니다."""
    return SOURCES

@mcp.tool()
async def inspect_public_page(source: str) -> dict:
    """공개 웹페이지의 접근 여부와 본문을 검사합니다."""
    if source not in SOURCES:
        return {"error": "지원하지 않는 소스입니다."}

    url = SOURCES[source]

    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        soup = BeautifulSoup(
            response.text, "html.parser"
        )

        title = (
            soup.title.get_text(strip=True)
            if soup.title else ""
        )

        for tag in soup(["script", "style"]):
            tag.decompose()

        content = soup.get_text(
            separator="\n", strip=True
        )

        return {
            "url": str(response.url),
            "status_code": response.status_code,
            "title": title,
            "content": content[:10000],
            "truncated": len(content) > 10000,
        }

    except httpx.HTTPError:
        return {"error": "PUBLIC_PAGE_UNAVAILABLE"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=port,
        path="/mcp",
    )
