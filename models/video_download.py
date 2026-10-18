"""Public video metadata; never retain cookies or signed media URLs."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class VideoFormat:
    selector: str
    height: int
    codec: str
    requires_merge: bool = False
    ext: str = 'mp4'


@dataclass
class VideoInfo:
    id: str
    title: str
    url: str
    author: str = ''
    duration: str = ''
    thumbnail: str = ''
    published: str = ''
    platform: str = 'Bilibili'
    formats: list[VideoFormat] = field(default_factory=list)
    author_id: str = ""
    published_timestamp: float = 0


@dataclass
class VideoSearchResults:
    items: list[VideoInfo]
    method: str = 'api'
    warning: str = ''
    keyword: str = ''


@dataclass(frozen=True)
class DownloadProgress:
    phase: str
    percent: float | None = None
    speed: float | None = None
    eta: float | None = None
