from __future__ import annotations

from lubo.core.models import Quality
from lubo.resolvers.base import NoCompatibleStreamError, ResolverStream


QUALITY_HEIGHTS = {
    Quality.BLUE_RAY: 2160,
    Quality.ULTRA: 1440,
    Quality.HIGH: 1080,
    Quality.STANDARD: 720,
    Quality.SMOOTH: 480,
}

PROTOCOL_PRIORITY = {"flv": 0, "http": 1, "https": 1, "hls": 2}


def select_stream(
    streams: tuple[ResolverStream, ...],
    quality: Quality,
) -> ResolverStream:
    candidates = tuple(stream for stream in streams if stream.url)
    if not candidates:
        raise NoCompatibleStreamError("No compatible stream candidates")

    known_heights = tuple(
        stream.height for stream in candidates if stream.height is not None
    )
    if quality is Quality.ORIGINAL:
        target_height = max(known_heights, default=0)
    else:
        limit = QUALITY_HEIGHTS[quality]
        fitting_heights = tuple(height for height in known_heights if height <= limit)
        target_height = (
            max(fitting_heights)
            if fitting_heights
            else min(known_heights, default=0)
        )

    target_candidates = tuple(
        stream
        for stream in candidates
        if (stream.height if stream.height is not None else 0) == target_height
    )
    return min(
        target_candidates,
        key=lambda stream: PROTOCOL_PRIORITY.get(stream.protocol.lower(), float("inf")),
    )
