"""Build narrated slide videos from deterministic timeline metadata."""

from .timeline import FrameSpan, SlideCue, Timeline, TimelineError, parse_timeline, validate_timeline

__all__ = [
    "FrameSpan",
    "SlideCue",
    "Timeline",
    "TimelineError",
    "parse_timeline",
    "validate_timeline",
]
