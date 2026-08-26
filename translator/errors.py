from __future__ import annotations

from enum import Enum


class TranslationErrorCode(str, Enum):
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    INVALID_JSON = "INVALID_JSON"
    API_ERROR = "API_ERROR"
    SEARCH_ERROR = "SEARCH_ERROR"
    TRANSLATION_ERROR = "TRANSLATION_ERROR"


class TranslationError(RuntimeError):
    """A translator failure with a stable, log-friendly error code."""

    def __init__(self, code: TranslationErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)
