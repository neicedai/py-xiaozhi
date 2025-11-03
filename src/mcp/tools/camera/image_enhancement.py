"""Utility helpers for improving captured camera frames."""

import cv2

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def enhance_frame_brightness(frame):
    """Improve frame brightness and contrast for clearer captures.

    The adjustment works in HSV space so that lightness is boosted while
    keeping the original colors as natural as possible.
    """

    if frame is None:
        return frame

    try:
        hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h_channel, s_channel, v_channel = cv2.split(hsv_frame)

        # Boost the value (brightness) channel with saturation arithmetic.
        v_channel = cv2.add(v_channel, 40)

        enhanced_hsv = cv2.merge((h_channel, s_channel, v_channel))
        enhanced_frame = cv2.cvtColor(enhanced_hsv, cv2.COLOR_HSV2BGR)

        # Apply a gentle contrast/brightness scaling to avoid flat highlights.
        enhanced_frame = cv2.convertScaleAbs(enhanced_frame, alpha=1.1, beta=5)
        return enhanced_frame
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Failed to enhance frame brightness: %s", exc)
        return frame
