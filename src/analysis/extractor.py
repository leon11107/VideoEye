"""Per-codec analysis extractor registry.

A CodecExtractor turns decoder output (an av.VideoFrame plus whatever
side data the backend exposes) into a normalized FrameAnalysis.

Adding a codec:
    1. subclass CodecExtractor
    2. decorate with @register
The decoder and all views stay untouched.
"""

from typing import Optional

from .schema import FrameAnalysis

_REGISTRY: list[type["CodecExtractor"]] = []


def register(cls: type["CodecExtractor"]) -> type["CodecExtractor"]:
    _REGISTRY.append(cls)
    return cls


def create_extractor(codec_name: str) -> Optional["CodecExtractor"]:
    """Instantiate the extractor handling codec_name, or None."""
    name = (codec_name or "").lower()
    for cls in _REGISTRY:
        if name in cls.codec_names:
            return cls()
    return None


class CodecExtractor:
    """Base class for per-codec block analysis extraction."""

    # Lowercase FFmpeg codec names this extractor handles.
    codec_names: tuple[str, ...] = ()

    def decoder_options(self) -> dict[str, str]:
        """Codec options that must be set before the decoder opens."""
        return {}

    def extract(self, frame, frame_index: int) -> Optional[FrameAnalysis]:
        """Build a FrameAnalysis from a decoded av.VideoFrame.

        Must never raise: a failed extraction returns None (or a partial
        FrameAnalysis) so decoding/display is never disrupted.
        """
        raise NotImplementedError
