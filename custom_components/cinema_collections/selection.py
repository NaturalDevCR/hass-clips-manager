"""Worker-backed next-clip selection without media filesystem access."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

from .const import PlaybackMode, normalize_clip_order
from .history import PlaybackHistoryStore
from .models import WorkerClip


@dataclass(frozen=True, slots=True)
class ClipAvailability:
    """The public Worker catalog fields required to make a safe selection."""

    id: str
    collection_id: str
    state: str
    relative_output_path: str | None
    duration_seconds: float
    output_available: bool

    @classmethod
    def from_worker_clip(cls, clip: WorkerClip) -> ClipAvailability:
        """Adapt the Worker client model without inspecting any local media path."""
        return cls(
            id=clip.id,
            collection_id=clip.collection_id,
            state=clip.state,
            relative_output_path=clip.relative_output_path,
            duration_seconds=clip.duration_seconds,
            output_available=clip.output_available,
        )


class ClipAvailabilityClient(Protocol):
    """The only Worker capability selection needs."""

    async def async_list_clips(self) -> Sequence[WorkerClip] | Sequence[ClipAvailability]:
        """Return Worker catalog availability, not filesystem paths."""
        ...


@dataclass(frozen=True, slots=True)
class SelectRequest:
    """A request to select from one already-resolved collection."""

    collection_id: str | None = None
    dry_run: bool = False
    playback_mode: PlaybackMode | str = PlaybackMode.RANDOM
    ordered_clip_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.collection_id is not None and not self.collection_id:
            raise ValueError("select request requires a collection ID")
        mode = PlaybackMode(self.playback_mode)
        object.__setattr__(self, "playback_mode", mode)
        ordered = normalize_clip_order(self.ordered_clip_ids)
        if mode is PlaybackMode.CUSTOM and not ordered:
            raise ValueError("custom playback requires ordered clip IDs")
        if mode is not PlaybackMode.CUSTOM and ordered:
            raise ValueError("ordered clip IDs require custom playback")
        object.__setattr__(self, "ordered_clip_ids", ordered)


@dataclass(frozen=True, slots=True)
class SelectResponse:
    """The next media details supplied to an automation, without controlling it."""

    collection_id: str | None
    clip_id: str | None
    relative_output_path: str | None
    media_uri: str | None
    duration_seconds: float | None
    history_reset: bool
    output_is_stale: bool = False


class SelectionService:
    """Filter current Worker output availability then make one durable history claim."""

    def __init__(
        self,
        history: PlaybackHistoryStore,
        client: ClipAvailabilityClient,
        *,
        media_uri_builder: Callable[[str], str] | None = None,
    ) -> None:
        self._history = history
        self._client = client
        self._media_uri_builder = media_uri_builder or _default_media_uri

    async def async_select(self, request: SelectRequest) -> SelectResponse:
        """Return one currently-ready Worker clip and atomically record a real selection."""
        if request.collection_id is None:
            return SelectResponse(
                collection_id=None,
                clip_id=None,
                relative_output_path=None,
                media_uri=None,
                duration_seconds=None,
                history_reset=False,
            )
        worker_clips = await self._client.async_list_clips()
        available = tuple(
            clip if isinstance(clip, ClipAvailability) else ClipAvailability.from_worker_clip(clip)
            for clip in worker_clips
        )
        # A clip whose compiled file still exists is playable even when its state
        # is no longer "ready". Editing a processing profile marks every clip in
        # the collection stale at once, and treating those as unplayable left a
        # library full of working files with nothing to show. Prefer up-to-date
        # output, then fall back to whatever still has a file on disk.
        playable = tuple(
            clip
            for clip in available
            if clip.collection_id == request.collection_id
            and clip.output_available
            and clip.relative_output_path
            and clip.state != "deleted"
        )
        ready = tuple(clip for clip in playable if clip.state == "ready")
        candidates = ready or playable
        if request.playback_mode is not PlaybackMode.RANDOM:
            sequential = tuple(sorted(candidates, key=_sequential_key))
            if request.playback_mode is PlaybackMode.CUSTOM:
                by_id = {clip.id: clip for clip in sequential}
                candidates = tuple(
                    by_id[clip_id] for clip_id in request.ordered_clip_ids if clip_id in by_id
                ) + tuple(clip for clip in sequential if clip.id not in request.ordered_clip_ids)
            else:
                candidates = sequential
        selected = await self._history.async_select(
            request.collection_id,
            tuple(clip.id for clip in candidates),
            request.dry_run,
            playback_mode=PlaybackMode(request.playback_mode),
        )
        if selected.clip_id is None:
            return SelectResponse(
                collection_id=request.collection_id,
                clip_id=None,
                relative_output_path=None,
                media_uri=None,
                duration_seconds=None,
                history_reset=selected.history_reset,
            )
        chosen = next(clip for clip in candidates if clip.id == selected.clip_id)
        output_path = chosen.relative_output_path
        assert output_path is not None
        return SelectResponse(
            collection_id=request.collection_id,
            clip_id=chosen.id,
            relative_output_path=output_path,
            media_uri=self._media_uri_builder(output_path),
            duration_seconds=chosen.duration_seconds,
            history_reset=selected.history_reset,
            output_is_stale=chosen.state != "ready",
        )


def _default_media_uri(relative_output_path: str) -> str:
    """Build the default Home Assistant media-source URI from a Worker-relative path."""
    return f"media-source://media_source/local/{quote(relative_output_path, safe='/')}"


def _sequential_key(clip: ClipAvailability) -> tuple[str, str, str]:
    path = clip.relative_output_path or ""
    return (path.casefold(), path, clip.id)


def normalize_media_uri_prefix(value: object) -> str:
    """Validate the configured HA Media Source directory mapping."""

    if not isinstance(value, str):
        raise ValueError("media URI prefix must be a string")
    prefix = value.strip().rstrip("/")
    required = "media-source://media_source/local/"
    if not prefix.startswith(required) or prefix == required.rstrip("/"):
        raise ValueError("media URI prefix must map to a local Media Source directory")
    if "?" in prefix or "#" in prefix or ".." in prefix.split("/"):
        raise ValueError("media URI prefix contains an unsafe path")
    return prefix


def build_media_uri(prefix: str, relative_output_path: str) -> str:
    """Map one Worker-relative compiled path below the configured media prefix."""

    normalized = normalize_media_uri_prefix(prefix)
    relative = relative_output_path.strip("/")
    if not relative or ".." in relative.split("/"):
        raise ValueError("Worker output path is not a safe relative path")
    return f"{normalized}/{quote(relative, safe='/')}"
