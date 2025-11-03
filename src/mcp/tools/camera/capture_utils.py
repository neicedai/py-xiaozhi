"""Camera capture helpers."""

from __future__ import annotations

import time
from typing import Optional, Tuple

import cv2

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def _measure_frame_brightness(frame: cv2.Mat) -> float:
    """Return the mean brightness of the frame in grayscale space."""

    if frame is None:
        return 0.0

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.mean())


def read_frame_with_warmup(
    cap: cv2.VideoCapture,
    warmup_frames: int = 5,
    settle_delay: float = 0.05,
    min_brightness: Optional[float] = 35.0,
    max_wait_seconds: float = 2.0,
) -> Tuple[bool, cv2.Mat]:
    """Read a frame once the camera delivers an adequately bright image.

    Cameras often emit several almost-black frames immediately after opening while the
    auto-exposure pipeline stabilises. We mimic the behaviour of the settings preview
    by keeping the capture alive for a short warm-up period, additionally monitoring the
    average brightness so that we prefer a brighter frame when available.

    Args:
        cap: An opened ``cv2.VideoCapture`` instance.
        warmup_frames: Minimum number of frames to read before we consider returning.
        settle_delay: Delay between frames to give the camera time to adjust.
        min_brightness: Target brightness (0-255). ``None`` disables the brightness check.
        max_wait_seconds: Upper bound for how long we wait for a bright frame.
    """

    frame: Optional[cv2.Mat] = None
    ret = False
    brightest_frame: Optional[cv2.Mat] = None
    brightest_value = -1.0

    frames_needed = max(warmup_frames, 1)
    deadline: Optional[float] = None
    if max_wait_seconds and max_wait_seconds > 0:
        deadline = time.monotonic() + max_wait_seconds

    frame_index = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            logger.debug("Warmup frame %s failed to read", frame_index)
        else:
            brightness = _measure_frame_brightness(frame)
            if brightness > brightest_value:
                brightest_value = brightness
                brightest_frame = frame

            if frame_index >= frames_needed - 1:
                if min_brightness is None or brightness >= min_brightness:
                    return True, frame

        frame_index += 1

        if deadline and time.monotonic() >= deadline:
            break

        if min_brightness is None and frame_index >= frames_needed:
            break

        if settle_delay:
            time.sleep(settle_delay)

    if brightest_frame is not None:
        if min_brightness is not None and brightest_value < min_brightness:
            logger.warning(
                "Camera warm-up timed out with dim frame (brightness=%.2f)",
                brightest_value,
            )
        return True, brightest_frame

    logger.error("Failed to read a frame from camera after warmup")
    return ret, frame
