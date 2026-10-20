"""Operator-only login helper. Never collects passwords or exports other sites.

Launches a fresh visible Chrome window for the owner to log in manually and open
one match detail. Watches only that known match request, validates the resulting
session through the read-only adapter, and writes an owner-protected local file.
No network upload, browser profile reuse, HAR, screenshots, or token logging.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from defusedxml.ElementTree import fromstring
from kfa_lookup import DETAIL, ORIGIN, KfaClient, LookupError, Session, identifier


def match_request(url, body):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "www.joinkfa.com":
        return None
    if parsed.path != DETAIL.split("?")[0] or not body or len(body) > 65536:
        return None
    root = fromstring(body, forbid_dtd=True)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    fields = {col.get("id"): col.text or "" for col in root.findall(
        "./Dataset[@id='dsReqParam']/Rows/Row/Col")}
    return tuple(identifier(fields[key]) for key in
                 ("v_USER_ID", "v_MATCH_IDX", "v_SINGLE_IDX"))


async def capture():
    from playwright.async_api import async_playwright
    folder = Path(__file__).resolve().parents[1] / ".secrets"
    if not folder.is_dir():
        raise RuntimeError("PROTECTED_DIRECTORY_REQUIRED")
    target = folder / "kfa-session.json"
    if target.exists():
        raise RuntimeError("SESSION_FILE_ALREADY_EXISTS")
    queue = asyncio.Queue(maxsize=5)

    def observe(request):
        # Do not read POST bodies from login or any other endpoint.
        parsed = urlsplit(request.url)
        if (parsed.scheme, parsed.netloc, parsed.path) != (
                "https", "www.joinkfa.com", DETAIL.split("?")[0]):
            return
        try:
            match = match_request(request.url, request.post_data)
            if match and not queue.full():
                queue.put_nowait(match)
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False)
        context = await browser.new_context(accept_downloads=False)
        context.on("request", observe)
        page = await context.new_page()
        await page.goto(ORIGIN, wait_until="domcontentloaded", timeout=60000)
        print("LOGIN_WINDOW_READY: Log in manually, then open one match detail.", flush=True)
        deadline = time.monotonic() + 20 * 60
        try:
            while time.monotonic() < deadline:
                try:
                    uid, cid, sid = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    if not browser.is_connected():
                        return
                    continue
                # Only cookies applicable to the fixed JOIN KFA origin.
                cookies = await context.cookies([ORIGIN + "/"])
                jar = {}
                expiry = time.time() + 4 * 3600
                for cookie in cookies:
                    if cookie["domain"].lstrip(".") != "joinkfa.com" and cookie["domain"] != "www.joinkfa.com":
                        continue
                    if cookie["name"] in jar and jar[cookie["name"]] != cookie["value"]:
                        raise RuntimeError("AMBIGUOUS_COOKIE_PATHS")
                    jar[cookie["name"]] = cookie["value"]
                    if cookie.get("expires", -1) > 0:
                        expiry = min(expiry, cookie["expires"])
                session = Session("엄세훈", uid, expiry, jar)
                # Quarantine locally so a parser fix does not require another
                # login. This file is never treated as a verified upload.
                candidate = folder / "kfa-session.pending.json"
                pending = {"name": session.name, "user_id": session.user_id,
                           "expires_at": session.expires_at, "cookies": session.cookies}
                with candidate.open("w", encoding="utf-8") as stream:
                    json.dump(pending, stream, ensure_ascii=False)
                try:
                    async with KfaClient(session) as client:
                        await client.authenticate("엄세훈")
                        detail = await client.detail(cid, sid)
                except LookupError as exc:
                    print("VALIDATION_FAILED: " + str(exc), flush=True)
                    continue
                data = {"name": session.name, "user_id": session.user_id,
                        "expires_at": session.expires_at, "cookies": session.cookies}
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False)
                Session.load(str(target))
                candidate.unlink()
                print("SESSION_SAVED_AND_VERIFIED", flush=True)
                print("Lease: at most 4 hours. Passwords were not collected.", flush=True)
                return
            print("LOGIN_TIMEOUT: no session saved", flush=True)
        finally:
            # Closing the ephemeral browser does not log out the upstream session.
            await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(capture())
    except KeyboardInterrupt:
        print("CANCELLED", flush=True)
    except Exception:
        # Exception strings can contain URLs or POST data. Never print them.
        print("HELPER_FAILED: no credentials printed; inspect configuration locally.", flush=True)
        sys.exit(1)
