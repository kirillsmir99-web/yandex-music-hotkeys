from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime

from .models import OverlayMessage, TrackState

try:
    from pycaw.pycaw import AudioUtilities
except ImportError:  # pragma: no cover - handled at runtime
    AudioUtilities = None

try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    )
    from winsdk.windows.storage.streams import DataReader
except ImportError:  # pragma: no cover - handled at runtime
    SessionManager = None
    DataReader = None


MessageCallback = Callable[[OverlayMessage], None]
TrackCallback = Callable[[TrackState], None]


def session_score(source_id: str) -> int:
    """Prefer the native Yandex Music media session and reject browsers."""
    source = source_id.casefold()
    if "yandexmusic" in source or "yandex.music" in source or "яндекс музыка" in source:
        return 100
    if "yandex" in source and "music" in source:
        return 90
    if any(
        name in source for name in ("chrome", "msedge", "firefox", "opera", "brave", "vivaldi", "browser")
    ):
        return -100
    return 0


class MediaController:
    """One persistent worker owns WinRT, COM, the command queue, and session cache."""

    def __init__(self, on_message: MessageCallback, on_track: TrackCallback):
        self._on_message = on_message
        self._on_track = on_track
        self._thread = threading.Thread(target=self._thread_main, name="ym-media", daemon=True)
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[str] | None = None
        self._manager = None
        self._manager_token = None
        self._session = None
        self._session_tokens: list[tuple[str, object]] = []
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._cover_retry_task: asyncio.Task | None = None
        self._media_revision = 0
        self._last_track_key = ""
        self._cover_cache: dict[str, bytes] = {}
        self._state = TrackState()
        self._state_at = time.monotonic()
        self._state_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return SessionManager is not None

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def submit(self, action: str) -> None:
        if not self._loop or not self._queue or self._stopping.is_set():
            return
        asyncio.run_coroutine_threadsafe(self._queue.put(action), self._loop)

    def current_position(self) -> float:
        with self._state_lock:
            state = self._state
            elapsed = time.monotonic() - self._state_at if state.playing else 0.0
            position = state.position + elapsed
            return min(position, state.duration) if state.duration > 0 else position

    def stop(self) -> None:
        self._stopping.set()
        if self._loop and self._queue:
            asyncio.run_coroutine_threadsafe(self._queue.put("__stop__"), self._loop)
        self._thread.join(timeout=2.5)

    def _thread_main(self) -> None:
        # winsdk initializes the apartment required by Windows Media Control.
        # Explicit STA initialization here prevents WinRT session discovery.
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=32)
        if SessionManager:
            try:
                self._manager = await SessionManager.request_async()
                self._manager_token = self._manager.add_sessions_changed(
                    self._on_sessions_changed
                )
            except Exception:
                self._manager = None
        self._ready.set()
        monitor = asyncio.create_task(self._monitor())
        try:
            while not self._stopping.is_set():
                action = await self._queue.get()
                if action == "__stop__":
                    break
                try:
                    await self._execute(action)
                except Exception:
                    self._on_message(OverlayMessage("Яндекс Музыка", "Команда не выполнена", "error"))
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            if self._refresh_handle:
                self._refresh_handle.cancel()
            if self._cover_retry_task:
                self._cover_retry_task.cancel()
            self._unsubscribe_session()
            if self._manager and self._manager_token is not None:
                with suppress(Exception):
                    self._manager.remove_sessions_changed(self._manager_token)

    def _select_session(self):
        if not self._manager:
            return None
        sessions = list(self._manager.get_sessions())
        if not sessions:
            return None
        ranked = sorted(
            sessions,
            key=lambda item: session_score(item.source_app_user_model_id or ""),
            reverse=True,
        )
        return ranked[0] if session_score(ranked[0].source_app_user_model_id or "") > 0 else None

    async def _ensure_session(self):
        selected = self._select_session()
        if selected is None:
            self._unsubscribe_session()
            self._session = None
            return None
        if self._session is not None:
            try:
                current_id = self._session.source_app_user_model_id or ""
                selected_id = selected.source_app_user_model_id or ""
                if current_id == selected_id:
                    return self._session
            except Exception:
                pass
        self._unsubscribe_session()
        self._session = selected
        self._subscribe_session(selected)
        self._media_revision += 1
        return selected

    def _subscribe_session(self, session) -> None:
        subscriptions = (
            ("media_properties_changed", self._on_media_properties_changed),
            ("playback_info_changed", self._on_playback_info_changed),
            ("timeline_properties_changed", self._on_timeline_properties_changed),
        )
        for event_name, callback in subscriptions:
            try:
                token = getattr(session, f"add_{event_name}")(callback)
                self._session_tokens.append((event_name, token))
            except Exception:
                continue

    def _unsubscribe_session(self) -> None:
        session = self._session
        if session:
            for event_name, token in self._session_tokens:
                with suppress(Exception):
                    getattr(session, f"remove_{event_name}")(token)
        self._session_tokens.clear()

    def _on_sessions_changed(self, *_args) -> None:
        self._schedule_refresh(session_changed=True)

    def _on_media_properties_changed(self, *_args) -> None:
        self._schedule_refresh(media_changed=True)

    def _on_playback_info_changed(self, *_args) -> None:
        self._schedule_refresh()

    def _on_timeline_properties_changed(self, *_args) -> None:
        self._schedule_refresh()

    def _schedule_refresh(
        self, *, media_changed: bool = False, session_changed: bool = False
    ) -> None:
        loop = self._loop
        if not loop or self._stopping.is_set():
            return
        loop.call_soon_threadsafe(
            self._queue_refresh, media_changed, session_changed
        )

    def _queue_refresh(self, media_changed: bool, session_changed: bool) -> None:
        if media_changed:
            self._media_revision += 1
        if self._refresh_handle:
            self._refresh_handle.cancel()

        async def refresh() -> None:
            if session_changed:
                await self._ensure_session()
            await self._publish_track(force=False)

        delay = 0.055 if media_changed or session_changed else 0.025
        self._refresh_handle = self._loop.call_later(
            delay, lambda: asyncio.create_task(refresh())
        )

    async def _execute(self, action: str) -> None:
        if action in {"vol_up", "vol_down", "vol_mute"}:
            self._change_volume(action)
            return
        session = await self._ensure_session()
        if not session:
            self._on_message(OverlayMessage("Яндекс Музыка", "Запустите воспроизведение", "error"))
            return
        if action == "next":
            old_key = self._last_track_key
            await session.try_skip_next_async()
            await self._wait_for_track_change(old_key)
            return
        elif action == "prev":
            old_key = self._last_track_key
            await session.try_skip_previous_async()
            await self._wait_for_track_change(old_key)
            return
        elif action == "play_pause":
            await session.try_toggle_play_pause_async()
        elif action in {"seek_left", "seek_right"}:
            delta = -10 if action == "seek_left" else 10
            timeline = session.get_timeline_properties()
            duration = max(0.0, timeline.end_time.total_seconds())
            playback = session.get_playback_info()
            playing = bool(playback and int(playback.playback_status) == 4)
            current = self._timeline_position(timeline, playing)
            target = max(0.0, min(duration or current + delta, current + delta))
            await session.try_change_playback_position_async(int(target * 10_000_000))
            sign = "−10" if delta < 0 else "+10"
            self._on_message(OverlayMessage(f"Перемотка {sign} сек", self._format_time(target), "seek"))
            return
        await asyncio.sleep(0.08)
        await self._publish_track(force=False)

    async def _wait_for_track_change(self, old_key: str) -> None:
        """Publish new metadata as soon as the native session exposes it."""
        for _ in range(18):
            await asyncio.sleep(0.085)
            await self._publish_track(force=False)
            if self._last_track_key and self._last_track_key != old_key:
                return

    def _change_volume(self, action: str) -> None:
        if AudioUtilities is None:
            self._on_message(OverlayMessage("Громкость", "Модуль управления недоступен", "error"))
            return
        target = None
        for session in AudioUtilities.GetAllSessions():
            process = session.Process
            if not process:
                continue
            try:
                identity = f"{process.name()} {process.exe()}".casefold()
            except Exception:
                continue
            if "yandexmusic" in identity or ("yandex" in identity and "music" in identity):
                target = session.SimpleAudioVolume
                break
        if target is None:
            self._on_message(OverlayMessage("Громкость", "Яндекс Музыка не найдена", "error"))
            return
        if action == "vol_mute":
            muted = not bool(target.GetMute())
            target.SetMute(muted, None)
            value = 0 if muted else round(target.GetMasterVolume() * 100)
            subtitle = "Windows · звук выключен" if muted else f"Windows · {value}%"
        else:
            delta = 0.05 if action == "vol_up" else -0.05
            volume = max(0.0, min(1.0, target.GetMasterVolume() + delta))
            target.SetMute(False, None)
            target.SetMasterVolume(volume, None)
            value = round(volume * 100)
            subtitle = f"Windows · {value}%"
        self._on_message(OverlayMessage("Громкость", subtitle, "volume", value))

    async def _monitor(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._ensure_session()
                if self._session:
                    await self._publish_track(force=False)
            except Exception:
                self._unsubscribe_session()
                self._session = None
            await asyncio.sleep(6.0 if self._session else 1.5)

    async def _publish_track(self, *, force: bool) -> None:
        session = self._session
        if not session:
            return
        revision = self._media_revision
        properties = await session.try_get_media_properties_async()
        if not properties or not properties.title:
            return
        timeline = session.get_timeline_properties()
        playback = session.get_playback_info()
        key = f"{properties.artist}\0{properties.title}"
        changed = key != self._last_track_key
        cover = self._cover_cache.get(key, b"")
        with self._state_lock:
            current_cover = self._state.cover if not changed else b""
        if not cover and (changed or force or not current_cover):
            cover = await self._read_cover(properties)
            if revision != self._media_revision:
                return
            if cover:
                self._cover_cache[key] = cover
                while len(self._cover_cache) > 8:
                    self._cover_cache.pop(next(iter(self._cover_cache)))
        playing = bool(playback and int(playback.playback_status) == 4)
        state = TrackState(
            title=properties.title,
            artist=properties.artist or "Яндекс Музыка",
            cover=cover,
            position=self._timeline_position(timeline, playing),
            duration=max(0.0, timeline.end_time.total_seconds()),
            playing=playing,
            album=properties.album_title or "",
        )
        with self._state_lock:
            previous_cover = self._state.cover
            if not state.cover and key == self._last_track_key:
                state = TrackState(
                    title=state.title,
                    artist=state.artist,
                    cover=previous_cover,
                    position=state.position,
                    duration=state.duration,
                    playing=state.playing,
                    album=state.album,
                )
            self._state = state
            self._state_at = time.monotonic()
        self._last_track_key = key
        self._on_track(state)
        if changed or force:
            self._on_message(OverlayMessage(state.title, state.artist, "track", cover=state.cover))
        if changed and not state.cover:
            if self._cover_retry_task:
                self._cover_retry_task.cancel()
            self._cover_retry_task = asyncio.create_task(self._retry_cover(key))

    async def _retry_cover(self, key: str) -> None:
        for delay in (0.10, 0.18, 0.32, 0.55):
            await asyncio.sleep(delay)
            if key != self._last_track_key or self._stopping.is_set():
                return
            await self._publish_track(force=False)
            with self._state_lock:
                if self._state.cover:
                    return

    @staticmethod
    def _timeline_position(timeline, playing: bool) -> float:
        """Turn WinRT's anchored timeline into the current playback position."""
        position = max(0.0, timeline.position.total_seconds())
        if playing:
            try:
                updated = timeline.last_updated_time
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                position += max(
                    0.0, (datetime.now(UTC) - updated).total_seconds()
                )
            except Exception:
                pass
        duration = max(0.0, timeline.end_time.total_seconds())
        return min(position, duration) if duration > 0 else position

    @staticmethod
    async def _read_cover(properties) -> bytes:
        if DataReader is None or not properties.thumbnail:
            return b""
        try:
            stream = await properties.thumbnail.open_read_async()
            if not stream or stream.size <= 0 or stream.size > 8 * 1024 * 1024:
                return b""
            reader = DataReader(stream)
            await reader.load_async(stream.size)
            data = bytearray(stream.size)
            reader.read_bytes(data)
            return bytes(data)
        except Exception:
            return b""

    @staticmethod
    def _format_time(seconds: float) -> str:
        value = max(0, int(seconds))
        return f"{value // 60}:{value % 60:02d}"
