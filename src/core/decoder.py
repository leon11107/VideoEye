"""Video frame decoder using PyAV."""

import av
import numpy as np
from typing import Optional

from .frame_info import FrameInfo


class Decoder:
    """Decodes video frames to RGB images."""

    def __init__(self):
        self._container: Optional[av.container.InputContainer] = None
        self._video_stream: Optional[av.video.stream.VideoStream] = None
        self._file_path: str = ""
        self._current_frame_index: int = -1
        self._codec_context: Optional[av.codec.context.CodecContext] = None

    def open(self, file_path: str) -> bool:
        """Open a video file for decoding."""
        try:
            self.close()
            self._file_path = file_path
            self._container = av.open(file_path)

            # Find first video stream
            for stream in self._container.streams:
                if stream.type == 'video':
                    self._video_stream = stream
                    break

            if not self._video_stream:
                raise ValueError("No video stream found")

            return True

        except Exception as e:
            print(f"Error opening file for decoding: {e}")
            self.close()
            return False

    def close(self) -> None:
        """Close the decoder."""
        if self._container:
            self._container.close()
        self._container = None
        self._video_stream = None
        self._current_frame_index = -1

    def decode_frame(self, frame_index: int) -> Optional[np.ndarray]:
        """Decode a specific frame by index, returning RGB numpy array."""
        if not self._container or not self._video_stream:
            return None

        try:
            # Seek to nearest keyframe before target
            stream = self._video_stream
            time_base = stream.time_base

            # We need to seek and then decode until we reach the target frame
            # First, seek to beginning or nearest keyframe
            self._container.seek(0, stream=stream)

            current_index = 0
            for packet in self._container.demux(stream):
                for frame in packet.decode():
                    if current_index == frame_index:
                        # Convert to RGB
                        rgb_frame = frame.to_ndarray(format='rgb24')
                        self._current_frame_index = frame_index
                        return rgb_frame
                    current_index += 1

            return None

        except Exception as e:
            print(f"Error decoding frame {frame_index}: {e}")
            return None

    def decode_frame_at_pts(self, pts: int) -> Optional[np.ndarray]:
        """Decode frame at specific PTS."""
        if not self._container or not self._video_stream:
            return None

        try:
            stream = self._video_stream

            # Seek to the target PTS
            self._container.seek(pts, stream=stream)

            for packet in self._container.demux(stream):
                for frame in packet.decode():
                    # Convert to RGB
                    rgb_frame = frame.to_ndarray(format='rgb24')
                    return rgb_frame

            return None

        except Exception as e:
            print(f"Error decoding frame at PTS {pts}: {e}")
            return None

    @property
    def width(self) -> int:
        """Get video width."""
        if self._video_stream:
            return self._video_stream.width
        return 0

    @property
    def height(self) -> int:
        """Get video height."""
        if self._video_stream:
            return self._video_stream.height
        return 0

    @property
    def is_open(self) -> bool:
        """Check if decoder is open."""
        return self._container is not None
