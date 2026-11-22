"""Read and write through the logged-in browser: own ads list, inbox, replies.

The persistent Chrome (see `browser.py`) holds the only login. Calls run as the page's own
fetch() in a tab on kleinanzeigen.de, so they carry the same cookies and pass the bot
protection like the web app itself. Endpoints as used by the web app (observed 2026-11):

  GET  /m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=N   own ads: id, state, endDate
  GET  /m-access-token.json          short-lived token in the `authorization` response header
  GET  /messagebox-api/view          {"user": {"id": ...}}
  GET  gateway …/messagebox/api/users/<uid>/conversations?page=0&size=N
  GET  gateway …/conversations/<cid>?contentWarnings=true
  POST gateway …/conversations/<cid>   {"message": text}
  GET  gateway …/conversations?include=_meta   {"_meta": {"numUnread", "lastModified"}}
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

ORIGIN = "https://www.kleinanzeigen.de"
GATEWAY = "https://gateway.kleinanzeigen.de"

_JS_SAME_ORIGIN = """(async () => {
  try {
    const r = await fetch(%s, {credentials: 'include', headers: {Accept: 'application/json'}});
    return JSON.stringify({status: r.status, url: r.url, text: await r.text()});
  } catch (e) { return JSON.stringify({status: 0, text: String(e)}); }
})()"""

_JS_GATEWAY = """(async () => {
  const [method, path, body] = %s;
  try {
    if (!window.__ka || !window.__ka.token || window.__ka.exp < Date.now() + 30000) {
      const r = await fetch('/m-access-token.json', {credentials: 'include', headers: {Accept: 'application/json'}});
      if (!r.ok) return JSON.stringify({status: r.status, text: '', where: 'token'});
      const j = await r.json().catch(() => ({}));
      window.__ka = {token: r.headers.get('authorization'), exp: Number(j.expiration) || 0};
      if (!window.__ka.token) return JSON.stringify({status: 401, text: '', where: 'token'});
    }
    const h = {Accept: 'application/json', Authorization: window.__ka.token, 'X-ECG-USER-AGENT': 'messagebox-1'};
    if (body !== null) h['Content-Type'] = 'application/json';
    const r = await fetch('%s' + path, {method, headers: h, mode: 'cors', credentials: 'include',
                                        body: body === null ? undefined : JSON.stringify(body)});
    return JSON.stringify({status: r.status, text: await r.text(), where: 'gateway'});
  } catch (e) { return JSON.stringify({status: 0, text: String(e), where: 'error'}); }
})()"""


async def disconnect(browser: Any) -> None:
    """Close our websocket connections to an existing browser without stopping it (nodriver keeps them
    per tab when it attaches to a running Chrome; Browser.stop() would kill the process)."""
    conns = [c for c in [getattr(browser, "connection", None), *getattr(browser, "targets", [])] if c is not None]
    for conn in conns:
        try:
            await conn.aclose()
        except Exception:  # noqa: BLE001 - already closed
            pass


class PortalError(RuntimeError):
    pass


class LoggedOut(PortalError):
    pass


def _is_login_url(url: str) -> bool:
    return any(p in (url or "") for p in ("m-einloggen", "/u/login", "login.kleinanzeigen.de"))


def _on_site(url: str) -> bool:
    return (urlparse(url or "").hostname or "") == "www.kleinanzeigen.de"


class Portal:
    """async with Portal(port) as p: ... — calls run as fetch() inside a kleinanzeigen.de tab.

    An open Kleinanzeigen tab is used as it is (no navigation, nothing visible changes). Only if
    none is open, a background tab on the start page is created and closed afterwards. Login state
    shows in the answers themselves (401 / login redirect -> LoggedOut).
    """

    def __init__(self, port: int, host: str = "127.0.0.1", require_login: bool = True) -> None:
        self.host, self.port = host, port
        self.require_login = require_login     # kept for callers; login is checked per call
        self.browser = None
        self.tab = None
        self.own_tab = False
        self.user_id: str | None = None

    async def __aenter__(self) -> "Portal":
        import nodriver
        from nodriver import Config, cdp

        cfg = Config(browser_executable_path="/usr/bin/true")  # only connecting, never launching
        cfg.host, cfg.port = self.host, self.port
        try:
            self.browser = await nodriver.start(cfg)
        except Exception as exc:  # noqa: BLE001
            raise PortalError(f"browser not reachable on {self.host}:{self.port} ({exc}); run `kassist browser`") from exc
        tabs = list(self.browser.tabs)
        self.tab = next((t for t in tabs if _on_site(t.url)), None)
        if self.tab is None:
            anchor = self.browser.main_tab or (tabs[0] if tabs else None)
            if anchor is None:
                await self.close()
                raise PortalError("the Kleinanzeigen browser has no open window")
            target_id = await anchor.send(cdp.target.create_target(ORIGIN + "/", background=True))
            await self.browser.update_targets()
            self.tab = next((t for t in self.browser.tabs if t.target_id == target_id), None)
            if self.tab is None:
                await self.close()
                raise PortalError("could not open a background tab")
            self.own_tab = True
            await self.tab.sleep(2.5)                   # let the start page load before fetching
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self.tab is not None and self.own_tab:
            try:
                await self.tab.close()
            except Exception:  # noqa: BLE001
                pass
        self.tab = None
        # Do not stop the browser: it is the user's persistent session. Only drop the connections.
        if self.browser is not None:
            await disconnect(self.browser)
        self.browser = None

    async def _eval(self, js: str) -> dict[str, Any]:
        raw = await self.tab.evaluate(js, await_promise=True, return_by_value=True)
        if not isinstance(raw, str):
            raise PortalError(f"unexpected evaluate result: {raw!r}"[:200])
        return json.loads(raw)

    async def get_json(self, path: str) -> Any:
        res = await self._eval(_JS_SAME_ORIGIN % json.dumps(path))
        if res.get("status") in (401, 403) or _is_login_url(res.get("url", "")):
            raise LoggedOut(f"Kleinanzeigen refused {path} (HTTP {res.get('status')})")
        if res.get("status") != 200:
            raise PortalError(f"{path}: HTTP {res.get('status')}")
        try:
            return json.loads(res.get("text") or "null")
        except ValueError as exc:
            raise PortalError(f"{path}: no JSON (logged out?)") from exc

    async def gateway(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        res = await self._eval(_JS_GATEWAY % (json.dumps([method, path, body]), GATEWAY))
        status = int(res.get("status") or 0)
        if status in (401, 403) or (res.get("where") == "token" and status == 0):
            raise LoggedOut(f"Kleinanzeigen refused the {res.get('where')} call (HTTP {status})")
        text = res.get("text") or ""
        try:
            return status, (json.loads(text) if text else None)
        except ValueError:
            return status, None

    async def get_html(self, path: str) -> str:
        js = _JS_SAME_ORIGIN.replace("Accept: 'application/json'", "Accept: 'text/html'")
        res = await self._eval(js % json.dumps(path))
        if res.get("status") != 200:
            raise PortalError(f"{path}: HTTP {res.get('status')}")
        return res.get("text") or ""

    # own ads
    async def my_ads(self, max_pages: int = 10) -> list[dict[str, Any]]:
        ads: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            data = await self.get_json(f"/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum={page}")
            batch = (data or {}).get("ads") or []
            ads.extend(batch)
            paging = (data or {}).get("paging") or {}
            last = paging.get("last") or paging.get("numPages") or page
            if not batch or page >= int(last):
                break
        return ads

    # inbox
    async def _uid(self) -> str:
        if self.user_id is None:
            data = await self.get_json("/messagebox-api/view")
            uid = ((data or {}).get("user") or {}).get("id")
            if not uid:
                raise LoggedOut("Kleinanzeigen did not name the account")
            self.user_id = str(uid)
        return self.user_id

    async def inbox_meta(self) -> dict[str, Any]:
        status, data = await self.gateway("GET", f"/messagebox/api/users/{await self._uid()}/conversations?include=_meta")
        if status != 200 or not isinstance(data, dict):
            raise PortalError(f"inbox meta: HTTP {status}")
        return data.get("_meta") or {}

    async def conversations(self, size: int = 50) -> list[dict[str, Any]]:
        status, data = await self.gateway("GET", f"/messagebox/api/users/{await self._uid()}/conversations?page=0&size={size}")
        if status != 200 or not isinstance(data, dict):
            raise PortalError(f"inbox: HTTP {status}")
        return data.get("conversations") or []

    async def conversation(self, cid: str) -> dict[str, Any]:
        status, data = await self.gateway(
            "GET", f"/messagebox/api/users/{await self._uid()}/conversations/{cid}?contentWarnings=true")
        if status != 200 or not isinstance(data, dict):
            raise PortalError(f"conversation {cid}: HTTP {status}")
        return data

    async def reply(self, cid: str, text: str) -> None:
        status, _ = await self.gateway("POST", f"/messagebox/api/users/{await self._uid()}/conversations/{cid}",
                                       {"message": text})
        if status not in (200, 201, 204):
            raise PortalError(f"reply to {cid}: HTTP {status}")
