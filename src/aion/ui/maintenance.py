"""Self-update and on-demand CUDA download, with progress pushed to the UI."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from loguru import logger

from aion import __version__, cuda, updater
from aion.core.events import Notification
from aion.edition import EDITION

if TYPE_CHECKING:
    from aion.app import Aion

Broadcast = Callable[[dict[str, Any]], Awaitable[None]]

FIRST_CHECK_DELAY = 20.0
CHECK_INTERVAL = 6 * 3600.0
PROGRESS_INTERVAL = 0.25


class Maintenance:
    def __init__(self, app: Aion, broadcast: Broadcast) -> None:
        self.aion = app
        self._broadcast = broadcast
        self.release: updater.Release | None = None
        self.checked_at: float | None = None
        self.check_error: str | None = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._last_progress: dict[str, float] = {}
        # Closes the window and stops the core; set by the runtime.
        self.request_quit: Callable[[], None] | None = None

    def start(self) -> None:
        updater.clean_downloads(self.aion.config.paths.data_dir / "updates")
        if updater.supported() and self.aion.config.updates.auto_check:
            self._spawn(self._auto_check())

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # -- updates --------------------------------------------------------------------------

    def update_status(self) -> dict[str, Any]:
        return {
            "edition": EDITION,
            "version": __version__,
            "supported": updater.supported(),
            "latest": asdict(self.release) if self.release else None,
            "checked_at": self.checked_at,
            "error": self.check_error,
            "job": self.jobs.get("update"),
        }

    async def check_update(self) -> dict[str, Any]:
        try:
            self.release = await updater.check()
            self.check_error = None
        except Exception as e:
            logger.warning("Не удалось проверить обновления: {}", e)
            self.check_error = f"Не удалось проверить обновления: {e}"
        self.checked_at = time.time()
        return self.update_status()

    def install_update(self) -> None:
        if not updater.supported():
            raise RuntimeError("Обновление доступно только в установленной версии Aion")
        release = self.release
        if release is None:
            raise RuntimeError("Новой версии нет — сначала проверьте обновления")

        async def run() -> None:
            folder = self.aion.config.paths.data_dir / "updates"
            installer = await updater.download(release, folder, self._progress("update"))
            updater.launch_installer(installer)
            await asyncio.sleep(0.5)  # let the UI receive the "done" state
            if self.request_quit is not None:
                self.request_quit()

        self._start_job("update", f"Aion {release.version}", run)

    async def _auto_check(self) -> None:
        await asyncio.sleep(FIRST_CHECK_DELAY)
        notified: str | None = None
        while True:
            await self.check_update()
            release = self.release
            if release is not None and release.version != notified:
                notified = release.version
                await self._broadcast({"type": "update_available", "version": release.version})
                await self.aion.bus.publish(
                    Notification(
                        title=f"Доступна новая версия Aion {release.version}",
                        text="Обновить можно на странице «О программе»",
                    )
                )
            await asyncio.sleep(CHECK_INTERVAL)

    # -- CUDA -----------------------------------------------------------------------------

    def cuda_status(self) -> dict[str, Any]:
        data_dir = self.aion.config.paths.data_dir
        return {
            "gpu": cuda.has_nvidia_gpu(),
            "available": cuda.available(data_dir),
            "bundled": cuda.bundled(),
            "size": cuda.DOWNLOAD_SIZE,
            "job": self.jobs.get("cuda"),
        }

    def download_cuda(self) -> None:
        data_dir = self.aion.config.paths.data_dir

        async def run() -> None:
            await cuda.download(data_dir, self._progress("cuda"))

        self._start_job("cuda", "Библиотеки CUDA", run)

    # -- jobs -----------------------------------------------------------------------------

    def _start_job(self, name: str, label: str, run: Callable[[], Awaitable[None]]) -> None:
        if self.jobs.get(name, {}).get("state") == "running":
            raise RuntimeError("Загрузка уже идёт")
        self.jobs[name] = {"state": "running", "label": label, "done": 0, "total": 0}

        async def wrapper() -> None:
            job = self.jobs[name]
            try:
                await run()
                job["state"] = "done"
            except Exception as e:
                logger.exception("Задача {} не выполнена", name)
                job.update(state="error", error=str(e))
            await self._send_job(name)

        self._spawn(wrapper())
        self._spawn(self._send_job(name))

    def _progress(self, name: str) -> Callable[[str, int, int], None]:
        def report(label: str, done: int, total: int) -> None:
            job = self.jobs[name]
            job.update(label=label, done=done, total=total)
            now = time.monotonic()
            if now - self._last_progress.get(name, 0) >= PROGRESS_INTERVAL:
                self._last_progress[name] = now
                self._spawn(self._send_job(name))

        return report

    async def _send_job(self, name: str) -> None:
        await self._broadcast({"type": "job", "name": name, **self.jobs[name]})

    def _spawn(self, coro: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
