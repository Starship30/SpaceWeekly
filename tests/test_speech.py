"""Speech contract checks with synthetic PCM; no model, network or GPU required."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import wave

from ai.speech import QWEN_TTS_06B_MODEL, SpeechRequest, generate_speech
from services.cancellation import CancellationToken, TaskCancelledError


def write_wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 12000)


class SpeechTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "build"
        scratch.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "voiceovers"
        self.request = SpeechRequest("本周航天新闻。", voice="Vivian")
        self.provider = Mock()
        self.provider.synthesize.side_effect = (
            lambda request, output_path, cancel_check: write_wav(output_path)
        )

    def generate(self, request=None, **kwargs):
        return generate_speech(request or self.request, self.output,
                               provider=self.provider, **kwargs)

    def test_qwen_request_reaches_adapter_and_returns_audio_metadata(self):
        result = self.generate()
        self.assertEqual(self.request.model, QWEN_TTS_06B_MODEL)
        self.assertEqual(result.sample_rate, 24000)
        self.assertEqual(result.duration_seconds, .5)
        self.assertEqual(result.path.suffix, ".wav")
        self.assertEqual(list(self.output.iterdir()), [result.path])
        call = self.provider.synthesize.call_args
        self.assertIs(call.args[0], self.request)
        self.assertEqual(call.args[1].suffix, ".wav")
        self.assertFalse(call.args[1].exists())

    def test_other_models_and_voices_are_passed_through(self):
        request = SpeechRequest("Launch update.", model="other-tts", voice="narrator",
                                language="en_US")
        self.generate(request)
        self.assertIs(self.provider.synthesize.call_args.args[0], request)

    def test_missing_adapter_is_explicit_and_creates_nothing(self):
        with self.assertRaisesRegex(RuntimeError, "尚未接入"):
            generate_speech(self.request, self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_input_never_invokes_adapter_or_creates_output(self):
        for name, value in [("text", " "), ("text", None), ("model", ""),
                            ("language", ""), ("voice", None)]:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.generate(replace(self.request, **{name: value}))
        self.provider.synthesize.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_regeneration_never_overwrites_previous_audio(self):
        first = self.generate()
        content = first.path.read_bytes()
        second = self.generate()
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.path.read_bytes(), content)
        self.assertEqual(len(list(self.output.iterdir())), 2)

    def test_provider_failure_cleans_partial_output_and_preserves_previous_audio(self):
        first = self.generate()
        content = first.path.read_bytes()

        def fail(request, output_path, cancel_check):
            output_path.write_bytes(b"partial")
            raise OSError("service disconnected")

        self.provider.synthesize.side_effect = fail
        with self.assertRaisesRegex(OSError, "disconnected"):
            self.generate()
        self.assertEqual(list(self.output.iterdir()), [first.path])
        self.assertEqual(first.path.read_bytes(), content)

    def test_invalid_empty_truncated_or_missing_audio_is_not_published(self):
        def broken_audio(request, output_path, cancel_check):
            write_wav(output_path)
            output_path.write_bytes(output_path.read_bytes()[:-2])

        writers = [lambda r, p, cancel_check: p.write_bytes(b"invalid"),
                   lambda r, p, cancel_check: p.write_bytes(b""),
                   lambda r, p, cancel_check: None, broken_audio]
        for writer in writers:
            self.provider.synthesize.side_effect = writer
            with self.subTest(writer=writer), self.assertRaises((ValueError, OSError)):
                self.generate()
            self.assertEqual(list(self.output.iterdir()), [])

    def test_cancellation_before_and_during_generation_leaves_no_new_audio(self):
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(TaskCancelledError):
            self.generate(cancel_check=token.check)
        self.provider.synthesize.assert_not_called()
        self.assertFalse(self.output.exists())

        token = CancellationToken()

        def cancel(request, output_path, cancel_check):
            write_wav(output_path)
            token.cancel()

        self.provider.synthesize.side_effect = cancel
        with self.assertRaises(TaskCancelledError):
            self.generate(cancel_check=token.check)
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
