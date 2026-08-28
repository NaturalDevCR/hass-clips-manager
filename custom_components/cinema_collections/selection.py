"""Worker-backed next-clip selection without media filesystem access."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

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

    def __post_init__(self) -> None:
        if self.collection_id is not None and not self.collection_id:
            raise ValueError("select request requires a collection ID")


@dataclass(frozen=True, slots=True)
class SelectResponse:
    """The next media details supplied to an automation, without controlling it."""

    collection_id: str | None
    clip_id: str | None
    relative_output_path: str | None
    media_uri: str | None
    duration_seconds: float | None
    history_reset: bool


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
        ready = tuple(
            clip
            for clip in available
            if clip.collection_id == request.collection_id
            and clip.state == "ready"
            and clip.output_available
            and clip.relative_output_path
        )
        selected = await self._history.async_select(
            request.collection_id,
            tuple(clip.id for clip in ready),
            request.dry_run,
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
        chosen = next(clip for clip in ready if clip.id == selected.clip_id)
        output_path = chosen.relative_output_path
        assert output_path is not None
        return SelectResponse(
            collection_id=request.collection_id,
            clip_id=chosen.id,
            relative_output_path=output_path,
            media_uri=self._media_uri_builder(output_path),
            duration_seconds=chosen.duration_seconds,
            history_reset=selected.history_reset,
        )


def _default_media_uri(relative_output_path: str) -> str:
    """Build the default Home Assistant media-source URI from a Worker-relative path."""
    return f"media-source://media_source/local/{quote(relative_output_path, safe='/')}"
