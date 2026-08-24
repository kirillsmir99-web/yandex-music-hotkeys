from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from urllib.error import HTTPError

from .storage import app_data_dir

_TIMESTAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_WORD_TIMESTAMP = re.compile(r"<(\d+):(\d+(?:\.\d+)?)>")
_YANDEX_SIGN_KEY = b"p93jhgh689SBReK6ghtw62"


@dataclass(frozen=True, slots=True)
class LyricWord:
    start: float
    text: str


@dataclass(frozen=True, slots=True)
class LyricLine:
    start: float
    text: str
    words: tuple[LyricWord, ...] = ()
    estimated: bool = False
    source: str = ""


def parse_lrc(text: str, *, source: str = "") -> list[LyricLine]:
    lines: list[LyricLine] = []
    for raw_line in (text or "").splitlines():
        body = _TIMESTAMP.sub("", raw_line)
        lyric = _WORD_TIMESTAMP.sub("", body).strip()
        if not lyric:
            continue
        word_matches = list(_WORD_TIMESTAMP.finditer(body))
        words: list[LyricWord] = []
        for index, match in enumerate(word_matches):
            end = word_matches[index + 1].start() if index + 1 < len(word_matches) else len(body)
            word_text = body[match.end() : end].strip()
            if word_text:
                words.append(
                    LyricWord(int(match.group(1)) * 60 + float(match.group(2)), word_text)
                )
        for minute, second in _TIMESTAMP.findall(raw_line):
            lines.append(
                LyricLine(
                    int(minute) * 60 + float(second),
                    lyric,
                    tuple(words),
                    source=source,
                )
            )
    return sorted(lines, key=lambda item: item.start)


def estimate_plain_lyrics(text: str, duration: float) -> list[LyricLine]:
    """Provide an explicitly marked fallback when only untimed lyrics exist."""
    raw_lines = [line.strip() for line in (text or "").splitlines()]
    text_lines = [
        line
        for line in raw_lines
        if line and not (line.startswith("[") and line.endswith("]"))
    ]
    if not text_lines:
        return []
    intro = min(8.0, duration * 0.06) if duration > 0 else 4.0
    outro = min(5.0, duration * 0.04) if duration > 0 else 0.0
    available = (
        max(len(text_lines) * 1.2, duration - intro - outro)
        if duration > 0
        else len(text_lines) * 3.2
    )
    weights = [max(1.0, len(line) ** 0.35) for line in text_lines]
    unit = available / sum(weights)
    cursor = intro
    result: list[LyricLine] = []
    for line, weight in zip(text_lines, weights, strict=True):
        result.append(LyricLine(cursor, line, estimated=True, source="Обычный текст"))
        cursor += weight * unit
    return result


class LyricsService:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ym-lyrics")
        self._cache: dict[tuple[str, str, str, int], tuple[float, list[LyricLine]]] = {}
        self._inflight: dict[
            tuple[str, str, str, int], list[tuple[str, Callable[[str, list[LyricLine]], None]]]
        ] = {}
        self._lock = Lock()
        cache_root = app_data_dir()
        self._cache_path = cache_root / "lyrics-cache-v2.json"
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
            for item in payload[-96:]:
                key = (
                    item["artist"],
                    item["title"],
                    str(item.get("album", "")),
                    int(item.get("duration", 0)),
                )
                lines = [
                    LyricLine(
                        float(line["start"]),
                        str(line["text"]),
                        tuple(LyricWord(float(word[0]), str(word[1])) for word in line.get("words", [])),
                        bool(line.get("estimated", False)),
                        str(line.get("source", "")),
                    )
                    for line in item["lines"]
                ]
                if lines:
                    self._cache[key] = (time.monotonic(), lines)
        except (OSError, ValueError, KeyError, TypeError):
            return

    def _save_cache(self) -> None:
        with self._lock:
            positive = [(key, lines) for key, (_, lines) in self._cache.items() if lines][-96:]
        payload = [
            {
                "artist": key[0],
                "title": key[1],
                "album": key[2],
                "duration": key[3],
                "lines": [
                    {
                        "start": line.start,
                        "text": line.text,
                        "words": [[word.start, word.text] for word in line.words],
                        "estimated": line.estimated,
                        "source": line.source,
                    }
                    for line in lines
                ],
            }
            for key, lines in positive
        ]
        try:
            temporary = self._cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self._cache_path)
        except OSError:
            return

    def request(
        self,
        artist: str,
        title: str,
        callback: Callable[[str, list[LyricLine]], None],
        *,
        duration: float = 0.0,
        album: str = "",
        refresh: bool = False,
    ) -> None:
        key = (
            artist.casefold().strip(),
            title.casefold().strip(),
            album.casefold().strip(),
            round(duration) if duration > 0 else 0,
        )
        public_key = f"{artist}\0{title}"
        with self._lock:
            cache_entry = self._cache.get(key)
            if refresh and cache_entry and (
                not cache_entry[1] or all(line.estimated for line in cache_entry[1])
            ):
                self._cache.pop(key, None)
                cache_entry = None
            cached = cache_entry[1] if cache_entry else None
            if cache_entry and not cached and time.monotonic() - cache_entry[0] > 15.0:
                cached = None
                self._cache.pop(key, None)
            if cached is None and key in self._inflight:
                self._inflight[key].append((public_key, callback))
                return
            if cached is None:
                self._inflight[key] = [(public_key, callback)]
        if cached is not None:
            callback(public_key, cached)
            return

        def done(future) -> None:
            try:
                result = future.result()
            except Exception:
                result = []
            with self._lock:
                self._cache[key] = (time.monotonic(), result)
                listeners = self._inflight.pop(key, [])
            if result:
                self._save_cache()
            for listener_key, listener in listeners:
                listener(listener_key, result)

        self._pool.submit(self._fetch, artist, title, album, duration).add_done_callback(done)

    @staticmethod
    def _fetch(
        artist: str,
        title: str,
        album: str = "",
        duration: float = 0.0,
    ) -> list[LyricLine]:
        yandex_lines = LyricsService._fetch_yandex(artist, title, duration)
        if yandex_lines:
            return yandex_lines
        headers = {"User-Agent": "ElarionMusicControl/4.0 (BRAT12344321)"}
        if album and duration > 0:
            exact_query = urllib.parse.urlencode(
                {
                    "artist_name": artist.strip(),
                    "track_name": title.strip(),
                    "album_name": album.strip(),
                    "duration": round(duration),
                }
            )
            exact_request = urllib.request.Request(
                f"https://lrclib.net/api/get?{exact_query}", headers=headers
            )
            try:
                with urllib.request.urlopen(exact_request, timeout=1.6) as response:
                    exact = json.loads(response.read().decode("utf-8"))
                if exact.get("syncedLyrics"):
                    return parse_lrc(exact["syncedLyrics"], source="LRCLIB · точное совпадение")
                if exact.get("plainLyrics"):
                    return estimate_plain_lyrics(exact["plainLyrics"], duration)
            except HTTPError as error:
                if error.code not in {404, 429}:
                    raise
            except (OSError, ValueError, TypeError):
                pass
        clean_artist = re.split(r",| feat\.? | ft\.? ", artist, maxsplit=1, flags=re.I)[0].strip()
        clean_title = re.split(r" \(| - ", title, maxsplit=1)[0].strip()
        variants = [(artist.strip(), title.strip())]
        if (clean_artist, clean_title) != variants[0]:
            variants.append((clean_artist, clean_title))
        candidates = []
        for query_artist, query_title in variants:
            search_query = urllib.parse.urlencode(
                {"artist_name": query_artist, "track_name": query_title}
            )
            search_request = urllib.request.Request(
                f"https://lrclib.net/api/search?{search_query}",
                headers=headers,
            )
            try:
                with urllib.request.urlopen(search_request, timeout=1.8) as response:
                    candidates = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                if error.code != 404:
                    raise
            if candidates:
                break
        synced = [item for item in candidates if item.get("syncedLyrics")]
        plain = [item for item in candidates if item.get("plainLyrics")]
        if not synced:
            if not plain:
                return []
            if duration > 0:
                plain.sort(key=lambda item: abs(float(item.get("duration") or 0) - duration))
            return estimate_plain_lyrics(plain[0].get("plainLyrics") or "", duration)
        if duration > 0:
            synced.sort(key=lambda item: abs(float(item.get("duration") or 0) - duration))
        return parse_lrc(synced[0].get("syncedLyrics") or "", source="LRCLIB")

    @staticmethod
    def _fetch_yandex(artist: str, title: str, duration: float) -> list[LyricLine]:
        """Use Yandex synchronized lyrics when a transient OAuth token is provided."""
        token = os.environ.get("YANDEX_MUSIC_TOKEN", "").strip()
        if not token:
            return []
        headers = {
            "Authorization": f"OAuth {token}",
            "User-Agent": "ElarionMusicControl/4.0 (BRAT12344321)",
        }
        query = urllib.parse.urlencode(
            {"text": f"{artist} {title}", "type": "track", "page": 0, "nocorrect": "false"}
        )
        try:
            search = LyricsService._read_json(
                f"https://api.music.yandex.net/search?{query}", headers
            )
            candidates = search.get("result", {}).get("tracks", {}).get("results", [])
            track = LyricsService._select_yandex_track(candidates, artist, title, duration)
            if not track:
                return []
            info = track.get("lyricsInfo") or {}
            if not info.get("hasAvailableSyncLyrics"):
                return []
            track_id = str(track["id"])
            timestamp = int(time.time())
            signature = base64.b64encode(
                hmac.new(
                    _YANDEX_SIGN_KEY,
                    f"{track_id}{timestamp}".encode(),
                    hashlib.sha256,
                ).digest()
            ).decode("ascii")
            params = urllib.parse.urlencode(
                {"format": "LRC", "timeStamp": timestamp, "sign": signature}
            )
            metadata = LyricsService._read_json(
                f"https://api.music.yandex.net/tracks/{track_id}/lyrics?{params}", headers
            )
            download_url = str(metadata.get("result", {}).get("downloadUrl", ""))
            if not download_url.startswith("https://"):
                return []
            request = urllib.request.Request(download_url, headers=headers)
            with urllib.request.urlopen(request, timeout=2.2) as response:
                return parse_lrc(response.read().decode("utf-8"), source="Яндекс Музыка")
        except (OSError, ValueError, KeyError, TypeError, HTTPError):
            return []

    @staticmethod
    def _read_json(url: str, headers: dict[str, str]) -> dict:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=2.2) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _select_yandex_track(candidates, artist: str, title: str, duration: float):
        def normalize(value) -> str:
            return re.sub(r"[^\w]+", "", str(value).casefold())
        wanted_title = normalize(title)
        wanted_artist = normalize(artist)
        matches = []
        for track in candidates:
            track_title = normalize(track.get("title", ""))
            artists = [normalize(item.get("name", "")) for item in track.get("artists", [])]
            if track_title != wanted_title or wanted_artist not in artists:
                continue
            track_duration = float(track.get("durationMs") or 0) / 1000.0
            delta = abs(track_duration - duration) if duration > 0 and track_duration > 0 else 0.0
            if delta <= 5.0:
                matches.append((delta, track))
        return min(matches, key=lambda item: item[0])[1] if matches else None

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
