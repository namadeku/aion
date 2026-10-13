"""Web UI backend: FastAPI REST + WebSocket event stream.

Security: the server listens on localhost only, and every request must carry the per-run
session token (``X-Aion-Token`` header, ``?token=`` for WebSocket/static avatar files).
Without it any website open in the user's browser could call ``localhost`` and, for example,
install a plugin — i.e. run arbitrary code.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import secrets
import time
import wave
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from loguru import logger
from pydantic import BaseModel, ValidationError

from aion import __version__
from aion.config.loader import set_secret
from aion.config.schema import Profile
from aion.core.events import AudioLevel, Event, StateChanged
from aion.edition import EDITION, is_public
from aion.ui.history import ConversationLog, LogBuffer
from aion.ui.maintenance import Maintenance

if TYPE_CHECKING:
    from aion.app import Aion

STATIC_DIR = Path(__file__).parent / "static"
LEVEL_EVENT_INTERVAL = 1 / 30
DOWNLOAD_EVENT_INTERVAL = 0.25
SECRET_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HOME_ASSISTANT_TOKEN")


class TextIn(BaseModel):
    text: str


class SourceIn(BaseModel):
    source: str
    force: bool = False


class PreviewIn(BaseModel):
    text: str = "Добрый вечер, сэр. Все системы работают в штатном режиме."
    engine: str | None = None
    voice: str | None = None
    rate: float | None = None
    pitch: float | None = None
    effect: str | None = None


class SecretIn(BaseModel):
    name: str
    value: str


class ToggleIn(BaseModel):
    enabled: bool


class FolderToggleIn(BaseModel):
    path: str
    enabled: bool


class PackIn(BaseModel):
    enabled: bool = True
    lang: Literal["ru", "en", "both"] | None = None


#: fields of GET /api/commands/{id} that are not part of the YAML
_COMMAND_VIEW_FIELDS = {"id", "folder", "dangerous", "error", "raw"}


class UiServer:
    def __init__(self, app: Aion, token: str | None = None) -> None:
        self.aion = app
        # AION_UI_TOKEN pins the token (end-to-end tests, scripted access)
        self.token = token or os.environ.get("AION_UI_TOKEN") or secrets.token_urlsafe(24)
        self.conversation = ConversationLog(app.bus)
        # the public edition has no log viewer: logs go to the file only
        self.logs: LogBuffer | None = None if is_public() else LogBuffer()
        self._sockets: set[WebSocket] = set()
        self._last_level: dict[str, float] = {}
        self._server: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # desktop mascot controller (aion.ui.mascot.DesktopMascot), set by the desktop app
        self.desktop: Any = None
        # startup progress shown by the page: "loading" (voice models), "ready" or "failed"
        self.startup: dict[str, Any] = {"stage": "ready", "error": None}
        self._downloads: dict[str, tuple[int, int]] = {}
        self._download_sent: dict[str, float] = {}
        self.maintenance = Maintenance(app, self._broadcast_raw)
        self.api = self._build()
        app.bus.subscribe("*", self._broadcast)
        app.bus.subscribe(StateChanged, self._on_state)

    # -- lifecycle ------------------------------------------------------------------------

    @property
    def url(self) -> str:
        ui = self.aion.config.ui
        return f"http://{ui.host}:{ui.port}/?token={self.token}"

    async def start(self) -> None:
        import uvicorn

        self._loop = asyncio.get_running_loop()
        if self.logs is not None:
            self.logs.install(self.aion.bus, self._loop)
        ui = self.aion.config.ui
        config = uvicorn.Config(
            self.api,
            host=ui.host,
            port=ui.port,
            log_level="warning",
            access_log=False,
            ws="auto",
        )
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # pyright: ignore[reportAttributeAccessIssue]
        server = self._server

        async def serve() -> None:
            # uvicorn calls sys.exit(1) when the port is busy; SystemExit would escape the
            # loop and kill the app silently, so end the task and let the check below report it
            with contextlib.suppress(SystemExit):
                await server.serve()

        task = asyncio.get_running_loop().create_task(serve(), name="ui-server")
        while not self._server.started:
            if task.done():
                raise RuntimeError(
                    f"Не удалось открыть порт {ui.port} — возможно, Aion уже запущен "
                    "(или задайте другой ui.port в config.yaml)"
                )
            await asyncio.sleep(0.05)
        logger.info("Интерфейс: {}", self.url.split("?")[0])
        self.maintenance.start()

    async def stop(self) -> None:
        await self.maintenance.stop()
        if self.logs is not None:
            self.logs.remove()
        for ws in list(self._sockets):
            with contextlib.suppress(Exception):
                await ws.close()
        if self._server is not None:
            self._server.should_exit = True
            await asyncio.sleep(0.2)

    # -- event fan-out --------------------------------------------------------------------

    async def _broadcast(self, event: Event) -> None:
        if not self._sockets:
            return
        if isinstance(event, AudioLevel):
            now = time.monotonic()
            if now - self._last_level.get(event.channel, 0) < LEVEL_EVENT_INTERVAL and event.level:
                return
            self._last_level[event.channel] = now
        message = event.to_message()
        dead: list[WebSocket] = []
        for ws in list(self._sockets):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._sockets.discard(ws)

    def send_threadsafe(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all pages from any thread (used by the desktop mascot)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(lambda: loop.create_task(self._broadcast_raw(message)))

    def set_startup(self, stage: str, error: str | None = None) -> None:
        self.startup = {"stage": stage, "error": error}
        self._downloads.clear()  # unfinished ones failed or had no known size
        self.send_threadsafe(self._startup_message())

    def report_download(self, label: str, done: int, total: int) -> None:
        """Model download progress (``aion.models.Progress``), callable from any thread."""
        finished = bool(total) and done >= total
        if finished:
            self._downloads.pop(label, None)
            self._download_sent.pop(label, None)
        else:
            self._downloads[label] = (done, total)
            now = time.monotonic()
            if now - self._download_sent.get(label, 0) < DOWNLOAD_EVENT_INTERVAL:
                return
            self._download_sent[label] = now
        self.send_threadsafe(self._startup_message())

    def _startup_message(self) -> dict[str, Any]:
        return {
            "type": "startup",
            **self.startup,
            "voice_enabled": self.aion.voice is not None,
            "downloads": [
                {"label": label, "done": done, "total": total}
                for label, (done, total) in list(self._downloads.items())
            ],
        }

    def _on_state(self, event: StateChanged) -> None:
        if self.desktop is not None:
            self.desktop.set_busy(event.new != "idle")

    def snapshot(self) -> dict[str, Any]:
        a = self.aion
        return {
            "type": "snapshot",
            "state": a.state.state,
            "name": a.config.profile.name,
            "profile": a.config.assistant.profile,
            "avatar": {
                **a.config.profile.avatar.model_dump(mode="json"),
                "clips": avatar_animations(a.config.profile.avatar.path),
            },
            "voice_enabled": a.voice is not None,
            "muted": a.voice.muted if a.voice else False,
            "llm": a.config.llm.provider if a.llm else None,
            "history": self.conversation.dump(),
            "desktop": bool(self.desktop is not None and self.desktop.enabled),
            "desktop_available": self.desktop is not None,
            "mood": a.mood.state(),
            "edition": EDITION,
            "version": __version__,
            "update": self.maintenance.release.version if self.maintenance.release else None,
            "startup": self._startup_message(),
        }

    # -- app ------------------------------------------------------------------------------

    def _check(self, request: Request) -> None:
        supplied = request.headers.get("x-aion-token") or request.query_params.get("token")
        if not supplied or not secrets.compare_digest(supplied, self.token):
            raise HTTPException(401, "Нужен токен сессии")

    def _build(self) -> FastAPI:  # noqa: PLR0915 - route table
        api = FastAPI(title="Aion", docs_url=None, redoc_url=None, openapi_url=None)
        auth = [Depends(self._check)]
        a = self.aion

        @api.get("/", response_class=HTMLResponse)
        async def index() -> Response:
            page = STATIC_DIR / "index.html"
            if not page.exists():
                return HTMLResponse(
                    "<h1>Aion</h1><p>Интерфейс не собран: cd frontend && pnpm build</p>",
                    status_code=503,
                )
            return HTMLResponse(page.read_text(encoding="utf-8"))

        @api.get("/assets/{name:path}")
        async def assets(name: str) -> Response:
            path = (STATIC_DIR / "assets" / name).resolve()
            if not path.is_relative_to((STATIC_DIR / "assets").resolve()) or not path.is_file():
                raise HTTPException(404)
            return FileResponse(path)

        @api.websocket("/ws")
        async def websocket(ws: WebSocket) -> None:
            token = ws.query_params.get("token", "")
            origin = ws.headers.get("origin", "")
            if not secrets.compare_digest(token, self.token) or not _local_origin(origin):
                await ws.close(code=4401)
                return
            await ws.accept()
            self._sockets.add(ws)
            await ws.send_json(self.snapshot())
            try:
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if (data := message.get("bytes")) is not None:
                        if self.desktop is not None:  # a frame of the desktop mascot
                            self.desktop.on_frame(data)
                        continue
                    with contextlib.suppress(ValueError):
                        parsed = json.loads(message.get("text") or "")
                        if isinstance(parsed, dict):
                            await self._on_ws_message(parsed)
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                self._sockets.discard(ws)

        # -- conversation ---------------------------------------------------------------

        @api.post("/api/say", dependencies=auth)
        async def submit(body: TextIn) -> dict[str, bool]:
            await a.dialog.submit(body.text, source="ui")
            return {"ok": True}

        @api.get("/api/history", dependencies=auth)
        async def history() -> list[dict[str, Any]]:
            return self.conversation.dump()

        @api.get("/api/history/search", dependencies=auth)
        async def history_search(q: str = "", limit: int = 30) -> list[dict[str, Any]]:
            return await self.search_history(q, max(1, min(limit, 200)))

        if self.logs is not None:
            log_buffer = self.logs

            @api.get("/api/logs", dependencies=auth)
            async def logs(limit: int = 300) -> list[dict[str, Any]]:
                return list(log_buffer.records)[-limit:]

        # -- config -----------------------------------------------------------------------

        @api.get("/api/config", dependencies=auth)
        async def get_config() -> dict[str, Any]:
            return a.config.model_dump(mode="json")

        @api.patch("/api/config", dependencies=auth)
        async def patch_config(patch: dict[str, Any]) -> Any:
            try:
                return a.store.update(patch).model_dump(mode="json")
            except ValidationError as e:
                return JSONResponse({"error": _errors(e)}, status_code=422)

        @api.get("/api/mcp", dependencies=auth)
        async def mcp_servers() -> list[dict[str, Any]]:
            return a.mcp.status()

        @api.put("/api/mcp/{name}", dependencies=auth)
        async def mcp_put(name: str, server: dict[str, Any]) -> Any:
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", name):
                raise HTTPException(422, "Имя: латиница, цифры, _ и -, до 32 символов")
            data = a.config.model_dump(mode="json")
            data["mcp"][name] = server
            try:
                a.store.replace(data)
            except ValidationError as e:
                return JSONResponse({"error": _errors(e)}, status_code=422)
            return {"ok": True}

        @api.delete("/api/mcp/{name}", dependencies=auth)
        async def mcp_delete(name: str) -> dict[str, bool]:
            data = a.config.model_dump(mode="json")
            if data["mcp"].pop(name, None) is None:
                raise HTTPException(404, "Сервер не найден")
            a.store.replace(data)
            return {"ok": True}

        @api.get("/api/secrets", dependencies=auth)
        async def get_secrets() -> dict[str, bool]:
            names = {
                *SECRET_NAMES,
                a.config.llm.anthropic.api_key_env,
                a.config.llm.openai.api_key_env,
            }
            return {n: bool(os.environ.get(n)) for n in sorted(names)}

        @api.put("/api/secrets", dependencies=auth)
        async def put_secret(body: SecretIn) -> dict[str, bool]:
            if not body.name.replace("_", "").isalnum():
                raise HTTPException(400, "Некорректное имя переменной")
            set_secret(body.name, body.value.strip())
            if "llm" in body.name.lower() or body.name.endswith("API_KEY"):
                await a.reload_llm()
            return {"ok": True}

        # -- profiles ---------------------------------------------------------------------

        @api.post("/api/profiles/{profile_id}", dependencies=auth)
        async def create_profile(profile_id: str, body: dict[str, Any] | None = None) -> Any:
            if not profile_id.isidentifier():
                raise HTTPException(400, "id профиля: латиница, цифры, _")
            base = a.config.profile.model_dump(mode="json")
            try:
                profile = Profile.model_validate({**base, **(body or {})})
            except ValidationError as e:
                return JSONResponse({"error": _errors(e)}, status_code=422)
            data = a.config.model_dump(mode="json")
            data["profiles"][profile_id] = profile.model_dump(mode="json")
            return a.store.replace(data).model_dump(mode="json")

        @api.delete("/api/profiles/{profile_id}", dependencies=auth)
        async def delete_profile(profile_id: str) -> Any:
            data = a.config.model_dump(mode="json")
            if profile_id not in data["profiles"] or len(data["profiles"]) == 1:
                raise HTTPException(400, "Нельзя удалить единственный или несуществующий профиль")
            del data["profiles"][profile_id]
            if data["assistant"]["profile"] == profile_id:
                data["assistant"]["profile"] = next(iter(data["profiles"]))
            return a.store.replace(data).model_dump(mode="json")

        # -- plugins ----------------------------------------------------------------------

        @api.get("/api/plugins", dependencies=auth)
        async def plugins() -> list[dict[str, Any]]:
            out = []
            for record in sorted(a.plugins.records.values(), key=lambda r: (not r.builtin, r.name)):
                info = record.info()
                if record.manifest:
                    overrides = a.config.plugins.settings.get(record.name, {})
                    info["settings"] = record.manifest.resolve_settings(overrides)
                    for key, spec in record.manifest.settings.items():
                        if spec.type == "secret":
                            info["settings"][key] = bool(info["settings"][key])
                out.append(info)
            return out

        @api.post("/api/plugins/{name}/{action}", dependencies=auth)
        async def plugin_action(name: str, action: str) -> dict[str, bool]:
            if name not in a.plugins.records:
                raise HTTPException(404, "Плагин не найден")
            match action:
                case "enable":
                    await a.plugins.enable(name)
                case "disable":
                    await a.plugins.disable(name)
                case "reload":
                    await a.plugins.reload(name)
                case _:
                    raise HTTPException(400, "Неизвестное действие")
            return {"ok": True}

        @api.put("/api/plugins/{name}/settings", dependencies=auth)
        async def plugin_settings(name: str, values: dict[str, Any]) -> Any:
            try:
                return await a.plugins.update_settings(name, values)
            except KeyError as e:
                raise HTTPException(404, "Плагин не найден") from e
            except (ValueError, TypeError) as e:
                raise HTTPException(422, str(e)) from e

        @api.post("/api/plugins/install", dependencies=auth)
        async def plugin_install(body: SourceIn) -> dict[str, str]:
            from aion.plugins.installer import InstallError, install_dependencies, install_plugin
            from aion.plugins.manifest import ManifestError

            paths = a.config.paths
            try:
                manifest = await install_plugin(body.source, paths.plugins, force=body.force)
                await install_dependencies(manifest, paths.plugin_deps / manifest.name)
            except (InstallError, ManifestError) as e:
                raise HTTPException(400, str(e)) from e
            await a.plugins._on_dir_changed(paths.plugins / manifest.name)  # pyright: ignore[reportPrivateUsage]
            return {"name": manifest.name}

        @api.delete("/api/plugins/{name}", dependencies=auth)
        async def plugin_remove(name: str) -> dict[str, bool]:
            from aion.plugins.installer import InstallError, remove_plugin

            record = a.plugins.records.get(name)
            if record is None or record.builtin:
                raise HTTPException(400, "Встроенные плагины можно только выключить")
            try:
                remove_plugin(name, [record.path.parent], a.config.paths.plugin_deps)
            except InstallError as e:
                raise HTTPException(400, str(e)) from e
            await a.plugins._on_dir_changed(record.path)  # pyright: ignore[reportPrivateUsage]
            return {"ok": True}

        self._command_routes(api, auth)

        # -- voice ------------------------------------------------------------------------

        @api.get("/api/voices", dependencies=auth)
        async def voices(engine: str) -> list[dict[str, Any]]:
            from aion.tts.output import create_engine

            try:
                tts = create_engine(engine, a.config.paths.models)
                listed = await tts.voices(a.config.assistant.language)
            except Exception as e:
                raise HTTPException(400, str(e)) from e
            return [v.__dict__ for v in listed]

        @api.post("/api/voice/preview", dependencies=auth)
        async def preview(body: PreviewIn) -> Response:
            from aion.tts.output import create_engine, render_voice

            patch = {k: v for k, v in body.model_dump().items() if k != "text" and v is not None}
            voice = a.config.profile.voice.model_copy(update=patch)
            try:
                tts = create_engine(voice.engine, a.config.paths.models, voice=voice)
                await tts.prepare(voice.voice)
                audio = await render_voice(tts, body.text, voice)
            except Exception as e:
                logger.exception("Ошибка предпрослушивания голоса")
                raise HTTPException(400, str(e)) from e
            return Response(_wav(audio.samples, audio.sample_rate), media_type="audio/wav")

        @api.get("/api/voice/presets", dependencies=auth)
        async def voice_presets() -> list[dict[str, Any]]:
            """Character presets; apply one by sending its ``patch`` to PATCH /api/config."""
            from aion.tts.presets import load_presets

            current = a.config.profile.voice
            profile_id = (
                a.config.assistant.profile
                if a.config.assistant.profile in a.config.profiles
                else next(iter(a.config.profiles))
            )
            piper_dir = a.config.paths.models / "piper"
            return [
                {
                    **preset.model_dump(),
                    "patch": preset.patch(profile_id),
                    "current": preset.matches(current),
                    # None = nothing to download (online engine or not file-based)
                    "installed": (piper_dir / f"{preset.voice}.onnx").exists()
                    if preset.engine == "piper"
                    else None,
                }
                for preset in load_presets()
            ]

        @api.get("/api/devices", dependencies=auth)
        async def devices() -> list[dict[str, Any]]:
            from aion.audio.devices import list_devices

            try:
                return [d.__dict__ for d in list_devices()]
            except Exception as e:
                raise HTTPException(500, f"Аудиоустройства недоступны: {e}") from e

        # -- llm --------------------------------------------------------------------------

        @api.get("/api/llm/models", dependencies=auth)
        async def llm_models(provider: str = "ollama") -> list[str]:
            if provider != "ollama":
                return []
            import httpx

            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    r = await client.get(f"{a.config.llm.ollama.base_url}/api/tags")
                    return sorted(m["name"] for m in r.json().get("models", []))
            except (httpx.HTTPError, ValueError):
                return []

        @api.post("/api/llm/test", dependencies=auth)
        async def llm_test() -> dict[str, Any]:
            if a.llm is None:
                return {"ok": False, "error": "LLM выключена"}
            started = time.perf_counter()
            try:
                answer = await asyncio.wait_for(
                    a.llm.complete("Ответь одним словом: работаешь?", max_tokens=200), 60
                )
            except Exception as e:
                return {"ok": False, "error": str(e)}
            return {
                "ok": True,
                "answer": answer,
                "seconds": round(time.perf_counter() - started, 2),
            }

        # -- updates and downloads --------------------------------------------------------

        m = self.maintenance

        @api.get("/api/update", dependencies=auth)
        async def update_status() -> dict[str, Any]:
            return m.update_status()

        @api.post("/api/update/check", dependencies=auth)
        async def update_check() -> dict[str, Any]:
            return await m.check_update()

        @api.post("/api/update/install", dependencies=auth)
        async def update_install() -> dict[str, bool]:
            try:
                m.install_update()
            except RuntimeError as e:
                raise HTTPException(409, str(e)) from e
            return {"ok": True}

        @api.get("/api/cuda", dependencies=auth)
        async def cuda_status() -> dict[str, Any]:
            return await asyncio.to_thread(m.cuda_status)

        @api.post("/api/cuda/download", dependencies=auth)
        async def cuda_download() -> dict[str, bool]:
            try:
                m.download_cuda()
            except RuntimeError as e:
                raise HTTPException(409, str(e)) from e
            return {"ok": True}

        # -- avatar files -----------------------------------------------------------------

        @api.get("/api/avatar/clips", dependencies=auth)
        async def avatar_clips() -> dict[str, Any]:
            """VRM Animation clips next to the current profile's model (settings page)."""
            return {"clips": avatar_animations(a.config.profile.avatar.path)}

        # The token is part of the path so relative URLs inside model files keep it.
        @api.get("/avatar/{session}/{name:path}")
        async def avatar_file(session: str, name: str) -> Response:
            if not secrets.compare_digest(session, self.token):
                raise HTTPException(401)
            model = a.config.profile.avatar.path
            if model is None:
                raise HTTPException(404)
            root = Path(model).expanduser().resolve().parent
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise HTTPException(404)
            return FileResponse(path)

        return api

    def _command_routes(self, api: FastAPI, auth: list[Any]) -> None:  # noqa: PLR0915
        """User commands and scenarios (docs/commands-api.md, section 6)."""
        from aion.commands import Command, CommandError, explain

        a = self.aion
        engine = a.commands
        store = engine.store

        def invalid(lines: list[str]) -> JSONResponse:
            return JSONResponse({"error": lines}, status_code=422)

        # fixed paths first: "{command_id:path}" below would swallow them

        @api.get("/api/commands", dependencies=auth)
        async def commands_list() -> dict[str, Any]:
            return engine.listing()

        @api.post("/api/commands/folders/toggle", dependencies=auth)
        async def commands_folder_toggle(body: FolderToggleIn) -> dict[str, bool]:
            try:
                store.toggle_folder(body.path, body.enabled)
            except KeyError as e:
                raise HTTPException(404, "Папка не найдена") from e
            engine.register()
            return {"ok": True}

        @api.post("/api/commands/import", dependencies=auth)
        async def commands_import(request: Request, filename: str = "") -> Any:
            name, data = filename, b""
            if request.headers.get("content-type", "").startswith("multipart/form-data"):
                form = await request.form()
                upload = form.get("file")
                if upload is None or isinstance(upload, str):
                    raise HTTPException(400, "Нужен файл в поле file")
                name, data = upload.filename or name, await upload.read()
            else:  # the raw file as the body, its name in ?filename=
                data = await request.body()
            try:
                imported, errors = await asyncio.to_thread(store.import_file, name, data)
            except CommandError as e:
                return invalid([str(e)])
            engine.reload()
            return {"imported": imported, "errors": errors}

        @api.get("/api/commands/export", dependencies=auth)
        async def commands_export(ids: str = "") -> Response:
            chosen = [i.strip() for i in ids.split(",") if i.strip()]
            data = await asyncio.to_thread(store.export, chosen)
            return Response(
                data,
                media_type="application/zip",
                headers={"Content-Disposition": 'attachment; filename="aion-commands.zip"'},
            )

        @api.get("/api/commands/packs", dependencies=auth)
        async def commands_packs() -> list[dict[str, Any]]:
            return store.packs()

        @api.post("/api/commands/packs/{name}", dependencies=auth)
        async def commands_pack(name: str, body: PackIn) -> dict[str, bool]:
            try:
                await asyncio.to_thread(store.set_pack, name, body.enabled, body.lang)
            except KeyError as e:
                raise HTTPException(404, "Пак не найден") from e
            engine.reload()
            return {"ok": True}

        @api.post("/api/commands/record/start", dependencies=auth)
        async def commands_record_start() -> dict[str, bool]:
            await asyncio.to_thread(engine.recorder.start)
            return {"ok": True}

        @api.post("/api/commands/record/stop", dependencies=auth)
        async def commands_record_stop() -> dict[str, Any]:
            return {"actions": await asyncio.to_thread(engine.recorder.stop)}

        # -- one command --------------------------------------------------------------------

        @api.post("/api/commands/{command_id:path}/toggle", dependencies=auth)
        async def commands_toggle(command_id: str, body: ToggleIn) -> dict[str, bool]:
            try:
                store.toggle(command_id, body.enabled)
            except KeyError as e:
                raise HTTPException(404, "Команда не найдена") from e
            engine.register()
            return {"ok": True}

        @api.post("/api/commands/{command_id:path}/test", dependencies=auth)
        async def commands_test(command_id: str) -> dict[str, str]:
            try:
                return {"run_id": await engine.start_test(command_id)}
            except KeyError as e:
                raise HTTPException(404, "Команда не найдена или в ней ошибка") from e

        @api.get("/api/commands/{command_id:path}", dependencies=auth)
        async def commands_get(command_id: str) -> dict[str, Any]:
            entry = store.get(command_id)
            if entry is None:
                raise HTTPException(404, "Команда не найдена")
            view: dict[str, Any] = {"id": entry.id, "folder": entry.folder}
            if entry.command is None:  # broken file: the editor shows the text to fix it
                raw = await asyncio.to_thread(entry.path.read_text, "utf-8", "replace")
                return {**view, "error": entry.error, "raw": raw}
            return {
                **view,
                **entry.command.to_yaml_data(),
                "enabled": store.is_enabled(entry),
                "dangerous": engine.is_dangerous(entry),
                "error": None,
            }

        @api.put("/api/commands/{command_id:path}", dependencies=auth)
        async def commands_put(command_id: str, body: dict[str, Any]) -> Any:
            data = {k: v for k, v in body.items() if k not in _COMMAND_VIEW_FIELDS}
            try:
                command = Command.model_validate(data)
                entry = store.save(command_id, command)
                if command.enabled and entry.id in store.state.disabled:
                    store.toggle(entry.id, True)  # "enabled" in the editor means enabled
            except ValidationError as e:
                return invalid(explain(e))
            except CommandError as e:
                return invalid([str(e)])
            engine.register()
            return engine.summary(entry)

        @api.delete("/api/commands/{command_id:path}", dependencies=auth)
        async def commands_delete(command_id: str) -> dict[str, bool]:
            if not store.delete(command_id):
                raise HTTPException(404, "Команда не найдена")
            engine.register()
            return {"ok": True}

    async def search_history(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        """Messages of the UI journal and diary entries (past conversations) matching ``query``."""
        from rapidfuzz import fuzz

        from aion.text import normalize

        needle = normalize(query, numbers=False)
        if not needle:
            return []
        found: list[dict[str, Any]] = []
        for entry in reversed(self.conversation.entries):
            text = normalize(entry.text, numbers=False)
            fuzzy = len(needle) >= 4 and fuzz.partial_ratio(needle, text) >= 85
            if needle in text or fuzzy:
                found.append({"kind": "message", **asdict(entry)})
        try:
            episodes = await self.aion.diary.store.search(query, limit)
        except Exception:  # the database may be closed or busy: messages are still useful
            logger.exception("Поиск по дневнику не удался")
            episodes = []
        for episode in episodes:
            found.append(
                {
                    "kind": "episode",
                    "role": "diary",
                    "text": episode.summary,
                    "source": "",
                    "ts": episode.started,
                    "ended": episode.ended,
                }
            )
        found.sort(key=lambda item: item["ts"], reverse=True)
        return found[:limit]

    def _on_confirm(self, message: dict[str, Any]) -> None:
        """ "Да"/"Нет" pressed in the work log for a dangerous step."""
        answered = self.aion.commands.answer(
            str(message.get("run_id", "")),
            str(message.get("step_id", "")),
            message.get("answer") is True,
        )
        if not answered:
            logger.debug("Ответ на подтверждение опоздал: {}", message)

    async def _on_ws_message(self, message: dict[str, Any]) -> None:
        a = self.aion
        match message.get("type"):
            case "text":
                await a.dialog.submit(str(message.get("text", "")), source="ui")
            case "interrupt":  # also cancels a running command (it is the current turn)
                await a.dialog.interrupt("hotkey")
            case "confirm":
                self._on_confirm(message)
            case "push_to_talk":
                if a.voice is not None:
                    await a.voice.push_to_talk()
            case "desktop_mode":
                if self.desktop is not None:  # pywebview calls may block: off the event loop
                    await asyncio.to_thread(self.desktop.set_enabled, bool(message.get("value")))
            case "mascot_hello" | "mascot_layout":
                if self.desktop is not None:
                    self.desktop.on_message(message)
            case "mute":
                if a.voice is not None:
                    a.voice.set_muted(bool(message.get("value")))
                    await self._broadcast_raw({"type": "muted", "value": a.voice.muted})
            case _:
                logger.debug("Неизвестное сообщение UI: {}", message)

    async def _broadcast_raw(self, message: dict[str, Any]) -> None:
        for ws in list(self._sockets):
            with contextlib.suppress(Exception):
                await ws.send_json(message)


def _local_origin(origin: str) -> bool:
    """Allow the bundled page (and pywebview, which may send no Origin) only."""
    if not origin or origin == "null":
        return True
    from urllib.parse import urlparse

    return urlparse(origin).hostname in {"127.0.0.1", "localhost"}


def avatar_animations(model: Path | None) -> list[str]:
    """VRM Animation clips (``*.vrma``) next to the avatar model, served via ``/avatar/``."""
    if model is None:
        return []
    folder = Path(model).expanduser().parent
    with contextlib.suppress(OSError):
        return sorted(p.name for p in folder.glob("*.vrma") if p.is_file())
    return []


def _errors(e: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]


def _wav(samples: Any, sample_rate: int) -> bytes:
    data = (np.clip(np.asarray(samples), -1, 1) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data.tobytes())
    return buffer.getvalue()
