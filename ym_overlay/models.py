from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrackState:
    title: str = "Яндекс Музыка"
    artist: str = ""
    cover: bytes = b""
    position: float = 0.0
    duration: float = 0.0
    playing: bool = False
    album: str = ""


@dataclass(frozen=True, slots=True)
class OverlayMessage:
    title: str
    subtitle: str = ""
    kind: str = "track"
    value: int = -1
    cover: bytes = b""
