"""Local GUI (FastAPI + Jinja + C22). Binds to 127.0.0.1; a Host/Origin guard blocks other sites
(DNS rebinding, cross-site form posts) from driving the app through the browser."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import __version__, bot, browser, catalog, config, engine, llm, planner
from .. import ai_assist as ai_assist_mod
from .. import drafts as drafts_mod
from ..config import Settings
from ..inbox import conversation_row  # noqa: F401  (re-exported for tests)
from ..state import Store
from . import forms, i18n, thumbs, viewmodels
from . import desktop
from .cache import TTLCache
from .scheduler import Scheduler

HERE = Path(__file__).parent
SAFE_NAME = re.compile(r"^[^/\\\x00]+$")


def create_app(settings: Settings, config_path: Path | None = None, scheduler: Scheduler | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sched = scheduler or Scheduler(settings)      # without start() it only runs on "sync now"
    app.state.scheduler = sched
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(default=True))
    flash: dict[str, tuple[str, str]] = {}

    cache = TTLCache()
    app.state.cache = cache
    asset_version = str(max((f.stat().st_mtime_ns for f in (HERE / "static").rglob("*") if f.is_file()), default=0))[-10:]
    env.globals["v"] = asset_version              # ?v=… on static links: a new version is fetched after an update

    def scanned() -> list:
        def scan() -> list:
            try:
                return catalog.scan(settings.source_dir, settings.catalog())
            except ValueError:
                return []
        return cache.get("scan", scan, 300)

    @app.middleware("http")
    async def caching(request: Request, call_next):
        response = await call_next(request)
        if request.method not in ("GET", "HEAD"):
            cache.clear()                          # kassist changed something: read folders fresh
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "max-age=86400"
        return response

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return PlainTextResponse("forbidden host", status_code=403)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin and (urlparse(origin).hostname or "") not in ("127.0.0.1", "localhost", "testserver"):
                return PlainTextResponse("forbidden origin", status_code=403)
        return await call_next(request)

    def store() -> Store:
        return Store(settings.db_path)

    def page(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        lang = i18n.pick(request.headers.get("accept-language"))
        kind, msg = flash.pop("msg", ("", ""))
        if msg.startswith("@"):                    # "@key|detail": translated message with an optional detail
            key, _, detail = msg[1:].partition("|")
            msg = i18n.TABLES[lang].get(key, key).format(detail=detail)
        return HTMLResponse(env.get_template(name).render(
            lang=lang, t=i18n.TABLES[lang], flash=msg, flash_kind=kind, path=request.url.path, version=__version__,
            sync=sched.status(), **ctx))

    def back(to: str, msg: str = "", kind: str = "info") -> RedirectResponse:
        if msg:
            flash["msg"] = (kind, msg)
        return RedirectResponse(to, status_code=303)

    def folder_of(key: str) -> Path | None:
        if not SAFE_NAME.match(key) or key.startswith("."):
            return None
        folder = settings.source_dir / key
        return folder if folder.is_dir() else None   # SAFE_NAME and the dot check keep it inside source_dir

    def reload_settings() -> None:
        settings.raw = config.load(config_path).raw

    # --- items --------------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def items_page(request: Request):
        st = store()
        try:
            return page(request, "items.html", source_ok=settings.source_dir.is_dir())
        finally:
            st.close()

    @app.post("/items/new")
    async def new_item(request: Request):
        name = " ".join(str((await request.form()).get("name") or "").split())
        if not name or not SAFE_NAME.match(name) or name.startswith(".") or not settings.source_dir.is_dir():
            return back("/", "invalid folder name", "destructive")
        (settings.source_dir / name).mkdir(exist_ok=True)
        return back(f"/items/{quote(name)}")

    @app.get("/items/{key}", response_class=HTMLResponse)
    def item_page(request: Request, key: str):
        if folder_of(key) is None:
            return back("/", "unknown item", "destructive")
        st = store()
        try:
            return page(request, "item.html", vm=viewmodels.item(settings, st, key))
        finally:
            st.close()

    @app.post("/items/{key}")
    async def save_item(request: Request, key: str):
        folder = folder_of(key)
        if folder is None:
            return back("/", "unknown item", "destructive")
        form = await request.form()
        try:
            values = forms.listing_values({k: str(v) for k, v in form.items()}, [str(t) for t in form.getlist("texts")],
                                          [str(n) for n in form.getlist("photos.order")],
                                          [str(n) for n in form.getlist("photos.skip")],
                                          {k: [str(v) for v in form.getlist(k)] for k in ("fx.label", "fx.value", "fx.show")})
            catalog.save_listing(folder, values, settings.catalog())
        except ValueError as exc:
            return back(f"/items/{quote(key)}", str(exc), "destructive")
        return back(f"/items/{quote(key)}", "@ok.saved", "success")

    @app.post("/items/{key}/import")
    def import_item(key: str, source: str = Form(""), how: str = Form("plain")):
        """A text file of the folder becomes listing.yaml: as it is, or read by the AI (only what the
        text says; prices not in the text are dropped)."""
        folder = folder_of(key)
        sources = {p.name: p for p in catalog.text_sources(folder)} if folder else {}
        path = sources.get(source) or next(iter(sources.values()), None)
        if path is None:
            return back("/", "no text file", "destructive")
        blocks = settings.catalog().text_blocks
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if how == "ai":
                fields, hints = ai_assist_mod.extract_listing(
                    lambda prompt: llm.complete(settings, prompt, json_mode=True), text, folder.name)
                header = (f"# Read by the AI from {path.name}: only what the text says. Check everything,\n"
                          "# fill in what is missing, then set approved: true.\n")
                header += "".join(f"# - {h}\n" for h in hints)
            else:
                fields, hints = catalog.plain_fields(text, folder.name, blocks), []
                header = (f"# Taken from {path.name} as it is. Fill in title, pricing and the rest,\n"
                          "# then set approved: true.\n")
            catalog.write_import(folder, fields, header)
        except (ValueError, FileExistsError, llm.LLMError) as exc:
            return back(f"/items/{quote(key)}", f"❌ {exc}", "destructive")
        cache.clear()
        return back(f"/items/{quote(key)}", "@ok.imported|" + path.name, "success")

    @app.post("/items/{key}/template")
    def template_item(key: str):
        folder = folder_of(key)
        if folder is None or (folder / catalog.LISTING_FILE).exists():
            return back("/", "unknown item", "destructive")
        (folder / catalog.LISTING_FILE).write_text(catalog.TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        return back(f"/items/{quote(key)}")

    @app.post("/items/{key}/photos")
    async def upload_photos(key: str, files: list[UploadFile]):
        folder = folder_of(key)
        if folder is None:
            return back("/", "unknown item", "destructive")
        saved = 0
        for f in files:
            name = Path(f.filename or "").name
            if not name or Path(name).suffix.lower() not in catalog.IMAGE_SUFFIXES or (folder / name).exists():
                continue
            (folder / name).write_bytes(await f.read())
            saved += 1
        return back(f"/items/{quote(key)}", f"@ok.photos|{saved}", "success")

    @app.post("/items/{key}/photos/crop")
    async def crop_photo(key: str, source: str = Form(...), file: UploadFile = File(...)):
        """Save a cropped/rotated copy next to the original; the copy takes the original's place in the
        ad order and the original is left out (it stays in the folder)."""
        folder = folder_of(key)
        if folder is None or not SAFE_NAME.match(source) or not (folder / source).is_file():
            return JSONResponse({"error": "unknown photo"}, status_code=400)
        data = await file.read()
        if not data.startswith(b"\xff\xd8"):
            return JSONResponse({"error": "not a JPEG"}, status_code=400)
        stem = Path(source).stem
        n = 1
        while (folder / f"{stem}-crop{n}.jpg").exists():
            n += 1
        name = f"{stem}-crop{n}.jpg"
        (folder / name).write_bytes(data)
        raw = catalog.load_raw(folder)
        cfg = raw.get("photos") or {}
        order = [p.name for p in catalog.images_in(folder, cfg.get("order"), cfg.get("skip"))]
        order = [name if x == source else x for x in order if x != name]
        if name not in order:
            order.append(name)
        raw["photos"] = {"order": order, "skip": sorted(set(cfg.get("skip") or []) | {source})}
        try:
            catalog.save_listing(folder, raw, settings.catalog())
        except ValueError as exc:      # listing not valid yet: keep the file, report why order was not saved
            return JSONResponse({"name": name, "warning": str(exc)})
        return JSONResponse({"name": name})

    @app.post("/items/{key}/ai/{what}")
    async def ai_assist(request: Request, key: str, what: str):
        """Proposals for the editor (not saved): improved text, titles, categories. Body: current form values."""
        if folder_of(key) is None or what not in ("text", "titles", "categories"):
            return JSONResponse({"error": "unknown"}, status_code=400)
        data = await request.json()
        title, body = str(data.get("title") or ""), str(data.get("description") or "")
        complete = lambda prompt: llm.complete(settings, prompt, json_mode=True)  # noqa: E731
        try:
            if what == "text":
                return JSONResponse({"text": ai_assist_mod.improve_text(complete, title, body)})
            if what == "titles":
                return JSONResponse({"titles": ai_assist_mod.propose_titles(complete, title, body)})
            return JSONResponse({"categories": ai_assist_mod.propose_categories(
                complete, title, body, sorted(bot.categories()))})
        except Exception as exc:  # noqa: BLE001 - shown in the dialog
            return JSONResponse({"error": str(exc)[:300]}, status_code=502)

    @app.post("/items/{key}/trash")
    def trash_item(key: str):
        if folder_of(key) is None:
            return back("/", "unknown item", "destructive")
        st = store()
        try:
            target = engine.trash_folder(settings, st, key)
            return back("/", f"@ok.trash|{key} → {target.parent.name}/{target.name}", "success")
        except ValueError as exc:
            return back(f"/items/{quote(key)}", f"❌ {exc}", "destructive")
        finally:
            st.close()

    @app.post("/items/{key}/approve-changes")
    def approve_changes(key: str):
        if folder_of(key) is None:
            return back("/", "unknown item", "destructive")
        st = store()
        try:
            engine.approve_changes(settings, st, key)
            return back(f"/items/{quote(key)}", "@ok.changes", "success")
        except Exception as exc:  # noqa: BLE001
            return back(f"/items/{quote(key)}", f"❌ {exc}", "destructive")
        finally:
            st.close()

    @app.post("/items/{key}/photos/capture")
    async def capture_photo(key: str, file: UploadFile = File(...)):
        """A photo from the camera dialog, saved as cam-<date>-<time>.jpg next to the others."""
        folder = folder_of(key)
        if folder is None:
            return JSONResponse({"error": "unknown item"}, status_code=400)
        data = await file.read()
        if not data.startswith(b"\xff\xd8"):
            return JSONResponse({"error": "not a JPEG"}, status_code=400)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name, n = f"cam-{stamp}.jpg", 1
        while (folder / name).exists():
            n += 1
            name = f"cam-{stamp}-{n}.jpg"
        (folder / name).write_bytes(data)
        return JSONResponse({"name": name})

    @app.post("/items/{key}/run")
    def run_now(key: str):
        if folder_of(key) is None:
            return back("/", "unknown item", "destructive")
        st = store()
        try:
            listing = engine.listings(settings).get(key)
            if listing is not None and planner.staged(listing, st.item(key)) == "pending":
                engine.approve_changes(settings, st, key)      # "send changes" = approve and send in one step
            return back(f"/items/{quote(key)}", "@ok.done|" + engine.run_item(settings, st, key), "success")
        except Exception as exc:  # noqa: BLE001
            return back(f"/items/{quote(key)}", f"❌ {exc}", "destructive")
        finally:
            st.close()

    @app.get("/thumb/{key}/{name}")
    def thumbnail(key: str, name: str, s: int = 160):
        folder = folder_of(key)
        path = (folder / name) if folder and SAFE_NAME.match(name) else None
        if path is None or path.parent != folder or path.suffix.lower() not in catalog.IMAGE_SUFFIXES or not path.is_file():
            return PlainTextResponse("not found", status_code=404)
        try:
            small = thumbs.thumb(path, s, settings.data_dir / "thumbs")
        except Exception:  # noqa: BLE001 - unreadable image: fall back to the original
            return FileResponse(path)
        return FileResponse(small, headers={"Cache-Control": "max-age=86400"})

    @app.get("/fragments/items", response_class=HTMLResponse)
    def item_rows(request: Request):
        """The table body of the items list, loaded after the page skeleton is shown."""
        st = store()
        try:
            lang = i18n.pick(request.headers.get("accept-language"))
            return HTMLResponse(env.get_template("_item_rows.html").render(
                t=i18n.TABLES[lang], rows=viewmodels.items(settings, st, scanned=scanned())))
        finally:
            st.close()

    @app.get("/photo/{key}/{name}")
    def photo(key: str, name: str):
        folder = folder_of(key)
        path = (folder / name) if folder and SAFE_NAME.match(name) else None
        if path is None or path.parent != folder or path.suffix.lower() not in catalog.IMAGE_SUFFIXES or not path.is_file():
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(path)

    @app.post("/items/{key}/prices")
    async def check_prices(request: Request, key: str):
        if folder_of(key) is None:
            return back("/", "unknown item", "destructive")
        form = await request.form()
        st = store()
        try:
            s = engine.price_check(settings, st, key=key, query=str(form.get("query") or "").strip() or None,
                                   ai=form.get("ai") == "on")
            msg = f"{s['query']}: median {s.get('median', '–')} €" + (
                f" · AI {s['ai'].get('median', '–')} €" if s.get("ai") else "")
            return back(f"/items/{quote(key)}", msg, "success")
        except Exception as exc:  # noqa: BLE001
            return back(f"/items/{quote(key)}", f"❌ {exc}", "destructive")
        finally:
            st.close()

    @app.post("/items/{key}/action")
    async def item_action(request: Request, key: str):
        what = str((await request.form()).get("what") or "")
        if folder_of(key) is None or what not in engine.MANUAL:
            return back("/", "unknown action", "destructive")
        st = store()
        try:
            return back(f"/items/{quote(key)}", "@ok.done|" + engine.manual(settings, st, key, what), "success")
        except Exception as exc:  # noqa: BLE001
            return back(f"/items/{quote(key)}", f"❌ {exc}", "destructive")
        finally:
            st.close()

    @app.post("/tick")
    def tick():
        st = store()
        try:
            return back("/", "\n".join(engine.tick(settings, st)) or "–", "info")
        except Exception as exc:  # noqa: BLE001
            return back("/", f"❌ {exc}", "destructive")
        finally:
            st.close()

    # --- sync and browser ------------------------------------------------------------------------
    @app.post("/sync")
    def sync_now(request: Request):
        back_to = urlparse(request.headers.get("referer") or "/").path or "/"
        if sched._thread is not None:            # background loop runs: let it do the work
            sched.trigger()
            return back(back_to)
        sched.run_once("sync")                     # no loop (e.g. `kassist panel`): run here
        report = "\n".join(sched.last_report) or "–"
        return back(back_to, report, "destructive" if sched.last_error else "info")

    @app.get("/status")
    def status():
        return JSONResponse(sched.status())

    @app.get("/ping")
    def ping():
        return JSONResponse({"app": "kassist", "version": __version__})

    @app.post("/window")
    def open_window():
        opener = getattr(app.state, "open_window", None)
        if opener is None:
            return JSONResponse({"error": "no window here (kassist panel)"}, status_code=409)
        opener()
        return JSONResponse({"ok": True})

    @app.post("/quit")
    def quit_app(request: Request):
        stop = getattr(app.state, "quit", None)
        if stop is None:
            return JSONResponse({"error": "not started by the launcher"}, status_code=409)
        stop()
        if "text/html" in (request.headers.get("accept") or ""):
            lang = i18n.pick(request.headers.get("accept-language"))
            return HTMLResponse(f"<!doctype html><meta charset='utf-8'><title>kassist</title>"
                                f"<p style='font-family:sans-serif;padding:2rem'>{i18n.TABLES[lang]['quit.done']}</p>")
        return JSONResponse({"ok": True})

    @app.post("/browser/show")
    def show_browser(request: Request):
        try:
            browser.show(settings)
        except Exception as exc:  # noqa: BLE001
            return back(urlparse(request.headers.get("referer") or "/").path or "/", f"❌ {exc}", "destructive")
        return back(urlparse(request.headers.get("referer") or "/").path or "/")

    # --- settings ------------------------------------------------------------------------------
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return page(request, "settings.html", vm=viewmodels.settings_view(settings))

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        rows = {k: [str(v) for v in form.getlist(k)] for k in
                ("seller.key", "seller.name", "seller.zipcode", "seller.city", "block.key", "block.text", "d.texts")}
        try:
            raw = forms.settings_values({k: str(v) for k, v in form.items()}, rows, settings.raw)
            key = str(form.get("llm.key") or "").strip()
            if key:
                config.set_secret(llm.MISTRAL_KEY, key)
            config.save(raw, config_path)
            reload_settings()
            if "a.login_start" in form or desktop.autostart_enabled():
                desktop.set_autostart(form.get("a.login_start") == "on")
        except (ValueError, RuntimeError, OSError) as exc:
            return back("/settings", str(exc), "destructive")
        return back("/settings", "@ok.settings", "success")

    @app.post("/settings/ai-test")
    async def ai_test(request: Request):
        """Save the form first (key, provider, model), then ask the model for a one-word answer."""
        form = await request.form()
        rows = {k: [str(v) for v in form.getlist(k)] for k in
                ("seller.key", "seller.name", "seller.zipcode", "seller.city", "block.key", "block.text", "d.texts")}
        try:
            raw = forms.settings_values({k: str(v) for k, v in form.items()}, rows, settings.raw)
            key = str(form.get("llm.key") or "").strip()
            if key:
                config.set_secret(llm.MISTRAL_KEY, key)
            config.save(raw, config_path)
            reload_settings()
            provider = request.query_params.get("provider") or settings["llm"].get("provider", "command")
            if provider not in llm.PROVIDERS:
                raise ValueError(f"unknown provider {provider!r}")
            answer = llm.ask(settings, provider, "Antworte nur mit einem kurzen deutschen Satz: Bist du bereit?")
        except Exception as exc:  # noqa: BLE001
            return back("/settings#ai", f"❌ {exc}", "destructive")
        return back("/settings#ai", f"@ok.ai|{provider}: {answer[:200]}", "success")

    @app.get("/settings/browse", response_class=HTMLResponse)
    def browse(request: Request, path: str = ""):
        return page(request, "browse.html", vm=viewmodels.browse(Path(path) if path else settings.source_dir))

    @app.post("/settings/folder")
    async def choose_folder(request: Request):
        path = Path(str((await request.form()).get("path") or "")).expanduser()
        if not path.is_dir():
            return back("/settings", "not a folder", "destructive")
        raw = dict(settings.raw)
        raw["source_dir"] = str(path)
        config.save(raw, config_path)
        reload_settings()
        return back("/settings", f"@ok.folder|{path}", "success")

    # --- inbox ---------------------------------------------------------------------------------
    @app.post("/poll")
    def poll():
        st = store()
        try:
            changed = engine.poll_inbox(settings, st, force=True)
            return back("/inbox", f"@ok.poll|{len(changed)}", "success")
        except Exception as exc:  # noqa: BLE001
            return back("/inbox", f"❌ {exc}", "destructive")
        finally:
            st.close()

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox_page(request: Request):
        return page(request, "inbox.html")

    @app.get("/fragments/inbox", response_class=HTMLResponse)
    def inbox_rows(request: Request):
        st = store()
        try:
            convs = [dict(c) | {"draft": st.open_draft(c["id"]) is not None} for c in st.conversations()]
            lang = i18n.pick(request.headers.get("accept-language"))
            return HTMLResponse(env.get_template("_inbox_rows.html").render(t=i18n.TABLES[lang], convs=convs))
        finally:
            st.close()

    @app.get("/inbox/{cid}", response_class=HTMLResponse)
    def thread_page(request: Request, cid: str):
        st = store()
        try:
            conv = st.conversation(cid)
            if conv is None:
                return back("/inbox", "unknown conversation", "destructive")
            return page(request, "thread.html", conv=conv, msgs=st.messages(cid), draft=st.open_draft(cid))
        finally:
            st.close()

    @app.post("/inbox/{cid}/send")
    async def send(request: Request, cid: str):
        form = await request.form()
        text = str(form.get("text") or "").strip()
        draft_id = str(form.get("draft_id") or "")
        text = drafts_mod.strip_markers(text)
        if not text:
            return back(f"/inbox/{cid}", "empty reply", "destructive")
        st = store()
        try:
            engine.send_reply(settings, st, cid, text, int(draft_id) if draft_id.isdigit() else None)
            return back(f"/inbox/{cid}", "@ok.sent", "success")
        except Exception as exc:  # noqa: BLE001
            return back(f"/inbox/{cid}", f"❌ {exc}", "destructive")
        finally:
            st.close()

    @app.post("/inbox/{cid}/redraft")
    def redraft(cid: str):
        st = store()
        try:
            text = engine.make_draft(settings, st, cid)
            return back(f"/inbox/{cid}", "@ok.draft" if text else "@ok.nodraft", "success" if text else "info")
        except Exception as exc:  # noqa: BLE001
            return back(f"/inbox/{cid}", f"❌ {exc}", "destructive")
        finally:
            st.close()

    @app.get("/log", response_class=HTMLResponse)
    def log_page(request: Request):
        st = store()
        try:
            return page(request, "log.html", events=st.events(300))
        finally:
            st.close()

    return app
