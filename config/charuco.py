"""The ChArUco board on the desk (the one every calibration since 2026-10-06 used) and the calibration page."""

from __future__ import annotations

from config.settings import BASE_DIR


class CharucoConfig:
    # Squares (columns, rows), square and marker side (m), ArUco dictionary.
    SQUARES = (7, 10)
    SQUARE_M = 0.018
    MARKER_M = 0.0135
    DICTIONARY = "DICT_4X4_50"
    # A view counts when the board shows at least this many corners.
    MIN_CORNERS = 8
    # The calibration results (one JSON per solve, dated), and the captured sessions.
    RESULT_DIR = BASE_DIR / "config" / "calibration"
    SESSION_DIR = BASE_DIR / "data" / "calibration"
    # The page's live detection: at most this often per camera (s).
    DETECT_PERIOD_S = 0.8
