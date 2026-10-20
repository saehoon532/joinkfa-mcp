import json
import time
import xml.etree.ElementTree as ET

import httpx
import pytest

from kfa_lookup import (CATALOG, DETAIL, MENU, SCHEDULE, Job, KfaClient, LookupError,
                        Session, parse_detail, roles_for, xml_request)


def session():
    return Session("엄세훈", "test-user", time.time() + 300, {"JSESSIONID": "test-only"})


def detail(**overrides):
    row = {"REFEREE_MAIN": "다른심판", "REFEREE_SUB1": "엄세훈, 다른부심",
           "REFEREE_SUB2": "", "REFEREE_STANDBY": "", "TITLE": "테스트 리그",
           "MATCH_CHECK_TIME": "2026-01-01 (목) 17:40", "MATCH_NUMBER": "1"}
    row.update(overrides)
    root = ET.Element("Root", xmlns="http://www.nexacroplatform.com/platform/dataset")
    ET.SubElement(ET.SubElement(root, "Parameters"), "Parameter", id="ErrorCode").text = "0"
    rr = ET.SubElement(ET.SubElement(ET.SubElement(root, "Dataset", id="dsCommonInfo"), "Rows"), "Row")
    for k, v in row.items():
        ET.SubElement(rr, "Col", id=k).text = v
    return ET.tostring(root, encoding="utf-8")


def test_xml_and_role_mapping():
    parsed = parse_detail(detail(REFEREE_STANDBY="엄세훈"), session())
    assert roles_for(parsed, "엄세훈") == ["부심", "대기심"]
    assert roles_for(parsed, "엄세") == []
    assert roles_for(parsed, "엄세훈2") == []
    assert "v_USER_ID" in xml_request("a&b", "1", "2").decode()
    assert "a&amp;b" in xml_request("a&b", "1", "2").decode()


@pytest.mark.parametrize("body", [b"<html>login</html>", b"<Root/>",
    b'<!DOCTYPE Root [<!ENTITY x SYSTEM "file:///etc/passwd">]><Root>&x;</Root>'])
def test_bad_xml_never_means_zero(body):
    with pytest.raises(LookupError):
        parse_detail(body, session())


def test_missing_role_column_is_not_empty_assignment():
    raw = detail().replace(b'<Col id="REFEREE_MAIN">', b'<Col id="unknown">')
    with pytest.raises(LookupError, match="DETAIL_SCHEMA_CHANGED"):
        parse_detail(raw, session())


def test_no_identity_mixup():
    extra = b'<Dataset id="gds_userInfo"><Rows><Row><Col id="USER_ID">another-user</Col></Row></Rows></Dataset>'
    with pytest.raises(LookupError, match="SESSION_IDENTITY_MISMATCH"):
        parse_detail(detail().replace(b"</Root>", extra + b"</Root>"), session())


def test_session_rejects_long_lease_and_bad_cookie(tmp_path):
    p = tmp_path / "s.json"
    obj = {"name": "엄세훈", "user_id": "test-user", "expires_at": time.time() + 99999,
           "cookies": {"JSESSIONID": "test"}}
    p.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(LookupError):
        Session.load(str(p))
    obj["expires_at"] = time.time() + 100
    obj["cookies"]["JSESSIONID"] = "bad\r\nheader"
    p.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(LookupError, match="SESSION_NOT_CONFIGURED"):
        Session.load(str(p))
    obj["cookies"]["JSESSIONID"] = "test"
    p.write_text(json.dumps(obj), encoding="utf-8")
    assert Session.load(str(p)).name == "엄세훈"
    assert "test-user" not in repr(Session.load(str(p)))


@pytest.mark.asyncio
async def test_redirect_never_receives_cookie_and_errors_redacted():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/"})
    async with KfaClient(session(), httpx.MockTransport(handler)) as client:
        with pytest.raises(LookupError, match="AUTH_REQUIRED_OR_ACCESS_DENIED"):
            await client.authenticate("엄세훈")
        with pytest.raises(LookupError, match="ENDPOINT_NOT_ALLOWED"):
            await client.request("https://other.example/")
    assert len(requests) == 1
    assert requests[0].url.host == "www.joinkfa.com"


@pytest.mark.asyncio
async def test_authentication_requires_matching_display_identity():
    html = '<a title="[엄세훈]님의 개인정보로 이동합니다.">내정보</a><a id="logoutBtn">Logout</a>'
    async with KfaClient(session(), httpx.MockTransport(lambda r: httpx.Response(200, text=html))) as client:
        await client.authenticate("엄세훈")
        with pytest.raises(LookupError, match="SELF_LOOKUP_ONLY"):
            await client.authenticate("다른사람")
    async with KfaClient(session(), httpx.MockTransport(lambda r: httpx.Response(200, text="Login"))) as client:
        with pytest.raises(LookupError, match="AUTH_REQUIRED_OR_IDENTITY_UNVERIFIED"):
            await client.authenticate("엄세훈")


class FakeClient:
    def __init__(self):
        self.details = []

    async def authenticate(self, name):
        assert name == "엄세훈"

    async def json(self, path, params):
        if path == CATALOG:
            page = int(params["v_CURPAGENUM"])
            return {"totalCount": 21, "matchList": [{"IDX": str(i)} for i in
                    (range(1, 21) if page == 1 else [21])]}
        assert path == SCHEDULE
        # Same match appears in two month responses: only count once.
        return {"singleList": [{"IDX": "one"}, {"IDX": "old"}]
                if params["v_YEAR_MONTH"] in ("2026-01", "2026-02") else []}

    async def detail(self, cid, sid):
        self.details.append((cid, sid))
        return parse_detail(detail(MATCH_CHECK_TIME="2025-12-31" if sid == "old" else "2026-01-01"), session())


@pytest.mark.asyncio
async def test_resumable_scan_deduplicates_and_filters_year():
    client = FakeClient()
    job = Job("엄세훈", 2026, ["selected"])
    assert (await job.advance(client, 1))["count"] is None
    while job.queue:
        result = await job.advance(client, 20)
    assert result["complete"]
    assert result["count"] == 1
    assert result["checked_games"] == 2
    assert result["by_role"] == {"주심": 0, "부심": 1, "대기심": 0}
    assert client.details == [("selected", "one"), ("selected", "old")]


@pytest.mark.asyncio
async def test_catalog_all_pages_and_scoped_total():
    client = FakeClient()
    job = Job("엄세훈", 2026)
    while job.queue and not job.error:
        result = await job.advance(client, 20)
    assert result["count"] == 21
    assert result["competitions"] == 21
    assert result["scope"] == "soccer_competitions_in_year_catalog"


@pytest.mark.asyncio
async def test_failure_does_not_claim_complete():
    class Broken(FakeClient):
        async def detail(self, cid, sid):
            raise LookupError("AUTH_REQUIRED_OR_ACCESS_DENIED")
    result = await Job("엄세훈", 2026, ["1"]).advance(Broken(), 20)
    assert not result["complete"]
    assert result["count"] is None
    assert result["error"] == "AUTH_REQUIRED_OR_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_truncated_catalog_and_schedule_rejected():
    class Truncated(FakeClient):
        async def json(self, path, params):
            return {"totalCount": 50, "matchList": [{"IDX": "1"}]}
    result = await Job("엄세훈", 2026).advance(Truncated())
    assert result["error"] == "CATALOG_PAGINATION_INCOMPLETE"
    class Huge(FakeClient):
        async def json(self, path, params):
            return {"singleList": [{"IDX": str(i)} for i in range(1000)]}
    result = await Job("엄세훈", 2026, ["1"]).advance(Huge())
    assert result["error"] == "SCHEDULE_PAGINATION_LIMIT"


@pytest.mark.parametrize("year", [True, 0, "2026", 9999])
def test_invalid_year(year):
    with pytest.raises(LookupError):
        Job("엄세훈", year)


@pytest.mark.asyncio
async def test_expiry_during_empty_scan_never_becomes_zero():
    class Expired(FakeClient):
        calls = 0
        async def authenticate(self, name):
            self.calls += 1
            if self.calls > 1:
                raise LookupError("AUTH_REQUIRED_OR_IDENTITY_UNVERIFIED")
        async def json(self, path, params):
            return {"matchList": [], "totalCount": 0}
    result = await Job("엄세훈", 2026).advance(Expired())
    assert result["count"] is None
    assert not result["complete"]


@pytest.mark.asyncio
async def test_json_error_with_empty_list_is_not_success():
    async with KfaClient(session(), httpx.MockTransport(lambda r: httpx.Response(200,
        json={"errorCode": -1, "matchList": [], "totalCount": 0}))) as client:
        with pytest.raises(LookupError, match="UPSTREAM_REJECTED"):
            await client.json(CATALOG, {})
