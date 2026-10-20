"""Read-only adapter for the JOIN KFA UI's observed JSON/XML services.

No password login, cookie export, arbitrary URL, or upstream mutation is exposed.
Credentials are provisioned by the operator, outside MCP/chat.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup
from defusedxml.ElementTree import fromstring

ORIGIN = "https://www.joinkfa.com"
MENU = "/service/menu/mainMenu.jsp"
CATALOG = "/portal/mat/getMatchList.do"
SCHEDULE = "/portal/mat/getMatchSingleList.do"
DETAIL = "/generate/MAP_04_001/SEARCH00.do?CALL_TYPE=NEXACRO"
ALLOWED = {MENU, CATALOG, SCHEDULE, DETAIL}
NS = "http://www.nexacroplatform.com/platform/dataset"
MAX_BYTES = 8 * 1024 * 1024


class LookupError(Exception):
    """Only fixed, credential-free codes may leave this adapter."""


def name_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def identifier(value: object) -> str:
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise LookupError("INVALID_IDENTIFIER")
    return value


def year_value(year: int) -> int:
    if isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2100:
        raise LookupError("INVALID_YEAR")
    return year


@dataclass(repr=False)
class Session:
    name: str
    user_id: str
    expires_at: float
    cookies: dict[str, str] = field(repr=False)

    @classmethod
    def load(cls, path: str) -> Session:
        try:
            p = Path(path)
            if p.stat().st_size > 65536:
                raise ValueError()
            obj = json.loads(p.read_text(encoding="utf-8"))
            session = cls(obj["name"], obj["user_id"], float(obj["expires_at"]), obj["cookies"])
            if not isinstance(session.name, str) or not session.name.strip():
                raise ValueError()
            identifier(session.user_id)
            if not isinstance(session.cookies, dict) or not session.cookies:
                raise ValueError()
            for key, value in session.cookies.items():
                if not re.fullmatch(r"[A-Za-z0-9_-]+", key) or not isinstance(value, str):
                    raise ValueError()
                if not value or any(ord(c) < 33 or ord(c) > 126 or c in ';,' for c in value):
                    raise ValueError()
            if not time.time() < session.expires_at <= time.time() + 8 * 3600:
                raise LookupError("SESSION_EXPIRED_OR_LEASE_TOO_LONG")
            return session
        except LookupError:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise LookupError("SESSION_NOT_CONFIGURED") from None


def xml_request(user_id: str, match_id: str, single_id: str) -> bytes:
    root = ET.Element("Root", xmlns=NS)
    ET.SubElement(root, "Parameters")
    ds = ET.SubElement(root, "Dataset", id="dsReqParam")
    info = ET.SubElement(ds, "ColumnInfo")
    row = ET.SubElement(ET.SubElement(ds, "Rows"), "Row")
    for key, value in {"v_MATCH_IDX": match_id, "v_SINGLE_IDX": single_id,
                       "v_USER_ID": user_id}.items():
        ET.SubElement(info, "Column", id=key, type="STRING", size="256")
        ET.SubElement(row, "Col", id=key).text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_detail(payload: bytes, session: Session) -> dict:
    try:
        root = fromstring(payload, forbid_dtd=True)
        for element in root.iter():
            element.tag = element.tag.rsplit("}", 1)[-1]
        if root.tag != "Root":
            raise ValueError()
        params = {x.get("id"): x.text or "" for x in root.findall("./Parameters/Parameter")}
        if "ErrorCode" not in params or int(params["ErrorCode"]) < 0:
            raise LookupError("UPSTREAM_REJECTED")
        identity = root.find("./Dataset[@id='gds_userInfo']/Rows/Row/Col[@id='USER_ID']")
        if identity is not None and identity.text != session.user_id:
            raise LookupError("SESSION_IDENTITY_MISMATCH")
        rows = root.findall("./Dataset[@id='dsCommonInfo']/Rows/Row")
        if len(rows) != 1:
            raise LookupError("DETAIL_UNAVAILABLE")
        row = {x.get("id"): x.text or "" for x in rows[0].findall("Col")}
        required = {"REFEREE_MAIN", "REFEREE_SUB1", "REFEREE_STANDBY", "MATCH_CHECK_TIME"}
        if not required <= row.keys():
            raise LookupError("DETAIL_SCHEMA_CHANGED")
        # Discard player, staff, medical and unrelated account datasets immediately.
        return {k: row.get(k, "") for k in required | {"REFEREE_SUB2", "TITLE", "MATCH_NUMBER"}}
    except LookupError:
        raise
    except Exception:
        raise LookupError("INVALID_XML_RESPONSE") from None


def roles_for(row: dict, name: str) -> list[str]:
    roles = []
    for role, fields in {"주심": ["REFEREE_MAIN"], "부심": ["REFEREE_SUB1", "REFEREE_SUB2"],
                         "대기심": ["REFEREE_STANDBY"]}.items():
        # The UI binds SUB1 to a combined comma-separated name string.
        names = {name_key(n) for key in fields for n in row.get(key, "").split(",")}
        if name_key(name) in names:
            roles.append(role)
    return roles


class KfaClient:
    def __init__(self, session: Session, transport=None):
        self.session = session
        self.transport = transport
        self._last_request = 0.0

    async def __aenter__(self):
        jar = httpx.Cookies()
        for key, value in self.session.cookies.items():
            jar.set(key, value, domain="www.joinkfa.com", path="/")
        self.http = httpx.AsyncClient(cookies=jar, timeout=20, follow_redirects=False,
                                     trust_env=False, transport=self.transport)
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    async def request(self, path: str, **kwargs) -> bytes:
        if path not in ALLOWED:
            raise LookupError("ENDPOINT_NOT_ALLOWED")
        if self.session.expires_at <= time.time():
            raise LookupError("SESSION_EXPIRED")
        await asyncio.sleep(max(0, 0.35 - (time.monotonic() - self._last_request)))
        self._last_request = time.monotonic()
        try:
            headers = {"Referer": ORIGIN + "/", "Origin": ORIGIN}
            if path == DETAIL:
                headers.update({"Content-Type": "text/xml; charset=UTF-8",
                                "GP_EMPL_ID": base64.b64encode(self.session.user_id.encode()).decode()})
            async with self.http.stream("GET" if path == MENU else "POST", ORIGIN + path,
                                        headers=headers, **kwargs) as response:
                if response.status_code in (401, 403) or 300 <= response.status_code < 400:
                    raise LookupError("AUTH_REQUIRED_OR_ACCESS_DENIED")
                if response.status_code != 200:
                    raise LookupError("UPSTREAM_HTTP_ERROR")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise LookupError("RESPONSE_TOO_LARGE")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError:
            raise LookupError("UPSTREAM_CONNECTION_ERROR") from None

    async def authenticate(self, requested_name: str):
        if name_key(requested_name) != name_key(self.session.name):
            raise LookupError("SELF_LOOKUP_ONLY")
        raw = await self.request(MENU)
        soup = BeautifulSoup(raw, "html.parser")
        names = []
        for tag in soup.find_all(attrs={"title": True}):
            m = re.fullmatch(r"\[([^\]]+)\]님의 개인정보로 이동합니다\.", tag["title"].strip())
            if m:
                names.append(name_key(m.group(1)))
        if names != [name_key(self.session.name)] or not soup.find(id="logoutBtn"):
            raise LookupError("AUTH_REQUIRED_OR_IDENTITY_UNVERIFIED")

    async def json(self, path: str, params: dict) -> dict:
        raw = await self.request(path, json=params)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            if data.get("errorMessage") or data.get("ErrorMsg"):
                raise LookupError("UPSTREAM_REJECTED")
            for key in ("ErrorCode", "errorCode"):
                if key in data and int(data[key]) < 0:
                    raise LookupError("UPSTREAM_REJECTED")
            return data
        except LookupError:
            raise
        except (ValueError, UnicodeError):
            raise LookupError("INVALID_JSON_RESPONSE") from None

    async def detail(self, competition_id: str, single_id: str) -> dict:
        return parse_detail(await self.request(DETAIL, content=xml_request(
            self.session.user_id, identifier(competition_id), identifier(single_id))), self.session)


@dataclass
class Job:
    name: str
    year: int
    competition_ids: list[str] | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    created: float = field(default_factory=time.time)
    queue: deque = field(default_factory=deque)
    competitions: set = field(default_factory=set)
    seen: set = field(default_factory=set)
    matches: list = field(default_factory=list)
    catalog_total: int | None = None
    checked: int = 0
    error: str | None = None

    def __post_init__(self):
        year_value(self.year)
        if self.competition_ids is not None:
            if not 1 <= len(self.competition_ids) <= 20:
                raise LookupError("SELECT_1_TO_20_COMPETITIONS")
            for cid in self.competition_ids:
                self.add_competition(identifier(cid))
        else:
            self.queue.append(("catalog", 1))

    def add_competition(self, cid):
        if cid not in self.competitions:
            self.competitions.add(cid)
            for month in range(1, 13):
                self.queue.append(("schedule", (cid, f"{self.year}-{month:02d}")))

    def result(self) -> dict:
        complete = not self.queue and not self.error
        return {"job_id": self.id, "name": self.name, "year": self.year,
                "complete": complete, "error": self.error,
                "scope": "selected_competitions" if self.competition_ids else "soccer_competitions_in_year_catalog",
                "coverage_note": "선택/연도별 축구 대회 목록에서 열람 가능한 경기 상세 기준. 배정 알림·예비배정·미공개 경기·풋살 전체를 뜻하지 않습니다.",
                "count": len(self.matches) if complete else None,
                "confirmed_so_far": len(self.matches), "checked_games": self.checked,
                "pending_requests": len(self.queue), "competitions": len(self.competitions),
                "by_role": {r: sum(r in m["roles"] for m in self.matches) for r in ("주심", "부심", "대기심")},
                "matching_method": "exact_display_name; 동명이인은 이름만으로 구별할 수 없음"}

    async def advance(self, client: KfaClient, budget: int = 10):
        if not 1 <= budget <= 20:
            raise LookupError("INVALID_BUDGET")
        if self.error:
            return self.result()
        try:
            await client.authenticate(self.name)
            for _ in range(budget):
                if not self.queue:
                    break
                kind, arg = self.queue[0]
                if kind == "catalog":
                    data = await client.json(CATALOG, {"v_CURPAGENUM": str(arg), "v_ROWCOUNTPERPAGE": "20",
                        "v_ORDERBY": "", "v_YEAR": str(self.year), "v_STYLE": "", "v_MGC_IDX": "",
                        "v_AREACODE": "", "v_SIGUNGU_CODE": "", "v_ITEM_CD": "S", "v_TITLE": "",
                        "v_TEAMID": "", "v_USER_ID": ""})
                    rows = data.get("matchList")
                    total = int(data["totalCount"])
                    if not isinstance(rows, list) or not 0 <= total <= 10000:
                        raise LookupError("CATALOG_SCHEMA_CHANGED")
                    if self.catalog_total is not None and self.catalog_total != total:
                        raise LookupError("CATALOG_CHANGED_RESTART")
                    self.catalog_total = total
                    expected = min(20, max(0, total - (arg - 1) * 20))
                    ids = [identifier(r["IDX"]) for r in rows]
                    if len(ids) != expected or len(set(ids)) != len(ids) or self.competitions.intersection(ids):
                        raise LookupError("CATALOG_PAGINATION_INCOMPLETE")
                    self.queue.popleft()
                    for cid in ids:
                        self.add_competition(cid)
                    if arg * 20 < total:
                        self.queue.appendleft(("catalog", arg + 1))
                elif kind == "schedule":
                    cid, month = arg
                    data = await client.json(SCHEDULE, {"v_CURPAGENUM": "1", "v_ROWCOUNTPERPAGE": "1000",
                        "v_ORDERBY": "", "v_MATCH_IDX": cid, "v_YEAR_MONTH": month,
                        "v_TEAMID": "", "v_USER_ID": ""})
                    rows = data.get("singleList")
                    if not isinstance(rows, list):
                        raise LookupError("SCHEDULE_SCHEMA_CHANGED")
                    if len(rows) >= 1000:
                        raise LookupError("SCHEDULE_PAGINATION_LIMIT")
                    if "totalCount" in data and int(data["totalCount"]) != len(rows):
                        raise LookupError("SCHEDULE_PAGINATION_INCOMPLETE")
                    additions = []
                    for row in rows:
                        sid = identifier(row["IDX"])
                        # Deduplicate the same competition/game across months.
                        if (cid, sid) not in self.seen:
                            additions.append(("detail", (cid, sid)))
                            self.seen.add((cid, sid))
                    if len(self.seen) > 100000:
                        raise LookupError("SCAN_LIMIT")
                    self.queue.popleft()
                    self.queue.extendleft(reversed(additions))
                else:
                    cid, sid = arg
                    row = await client.detail(cid, sid)
                    text = row["MATCH_CHECK_TIME"]
                    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
                    if not m:
                        raise LookupError("UNKNOWN_MATCH_DATE")
                    day = date(*(int(v) for v in m.groups()))
                    roles = roles_for(row, self.name)
                    if day.year == self.year and roles:
                        self.matches.append({"competition_id": cid, "game_id": sid,
                            "date": day.isoformat(), "title": row["TITLE"],
                            "match_number": row["MATCH_NUMBER"], "roles": roles})
                    self.checked += 1
                    self.queue.popleft()
            # A session can expire while an otherwise well-shaped empty list is returned.
            # Never finalize a scan without checking the authenticated identity again.
            if not self.queue:
                await client.authenticate(self.name)
        except LookupError as exc:
            self.error = str(exc)
        except (KeyError, ValueError, TypeError):
            self.error = "UPSTREAM_SCHEMA_CHANGED"
        return self.result()
