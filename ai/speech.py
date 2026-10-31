"""Optional speech adapter contract for future video voiceovers; no model is loaded."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import wave

from services.cancellation import CancelCheck, no_cancel
from utils.output_files import reserved_output

QWEN_TTS_06B_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"


@dataclass(frozen=True)
class SpeechRequest:
    text: str
    model: str = QWEN_TTS_06B_MODEL
    voice: str = ""
    language: str = "zh_CN"
    style: str = ""


@dataclass(frozen=True)
class SpeechResult:
    path: Path
    duration_seconds: float
    sample_rate: int


class SpeechProvider(Protocol):
    def synthesize(
        self,
        request: SpeechRequest,
        output_path: Path,
        cancel_check: CancelCheck = no_cancel,
    ) -> None:
        """Write a complete PCM WAV to output_path; raise on failure or cancellation.

        Adapters own model loading or HTTP credentials, language/voice mapping and
        timeouts. Long-running adapters must call cancel_check during generation.
        """
        ...


def generate_speech(
    request: SpeechRequest,
    output_dir: Path,
    *,
    provider: SpeechProvider | None = None,
    cancel_check: CancelCheck = no_cancel,
) -> SpeechResult:
    """Publish a separate WAV asset only after successful, explicit adapter use."""
    cancel_check()
    for name in ("text", "model", "language"):
        value = getattr(request, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Speech: {name} must be a nonempty string")
    if not isinstance(request.voice, str):
        raise ValueError("Speech: voice must be a string")
    if provider is None:
        raise RuntimeError("语音生成接口已预留，尚未接入语音服务")

    with reserved_output(Path(output_dir), ".wav") as (path, temporary):
        provider.synthesize(request, temporary, cancel_check=cancel_check)
        cancel_check()
        try:
            with wave.open(str(temporary), "rb") as audio:
                frames = audio.getnframes()
                sample_rate = audio.getframerate()
                if frames <= 0 or sample_rate <= 0:
                    raise ValueError("Speech: audio must contain samples")
                # Check the declared last frame without loading the entire audio.
                audio.setpos(frames - 1)
                if len(audio.readframes(1)) != audio.getnchannels() * audio.getsampwidth():
                    raise ValueError("Speech: incomplete WAV audio")
        except (wave.Error, EOFError) as error:
            raise ValueError("Speech: provider must write a valid PCM WAV") from error
        cancel_check()
    return SpeechResult(path, frames / sample_rate, sample_rate)
