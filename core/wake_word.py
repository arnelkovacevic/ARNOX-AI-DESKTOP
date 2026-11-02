"""Local wake-word spotting for ARNOX sleep mode.

While ARNOX sleeps the microphone is no longer streamed to Gemini. This module
listens locally, with no network access, for the single word "Arnox" and reports
when it is heard. It uses the CMU PocketSphinx keyword spotter that ships with
the `pocketsphinx` package, with a custom pronunciation for "Arnox".
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

WAKE_KEYPHRASE = "arnox"
# ARPAbet pronunciations: "ar-nox" with the two common vowel readings.
_PRONUNCIATIONS = (
    "AA R N AA K S",
    "AA R N AO K S",
    "AA R N OW K S",
)
# Lower is more sensitive (more false wakes); higher is stricter (more misses).
# Measured on synthetic speech: 1e-30 missed some voices, 1e-50 caught all voices but
# also woke on near-sounding words. 1e-40 is the balance. Override with ARNOX_WAKE_THRESHOLD.
DEFAULT_THRESHOLD = 1e-40
SAMPLE_RATE = 16000


class WakeWordUnavailable(RuntimeError):
    """Raised when the local spotter cannot be created (library or model missing)."""


def _write_dictionary(directory: Path) -> Path:
    path = directory / "arnox.dict"
    lines = [f"{WAKE_KEYPHRASE} {_PRONUNCIATIONS[0]}"]
    lines += [f"{WAKE_KEYPHRASE}({i + 2}) {p}" for i, p in enumerate(_PRONUNCIATIONS[1:])]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


class WakeWordDetector:
    """Feed 16 kHz mono int16 audio; `feed` returns True when "Arnox" is heard."""

    def __init__(self, threshold: float | None = None):
        if threshold is None:
            try:
                threshold = float(os.environ.get("ARNOX_WAKE_THRESHOLD", DEFAULT_THRESHOLD))
            except ValueError:
                threshold = DEFAULT_THRESHOLD
        try:
            from pocketsphinx import Decoder, get_model_path
        except Exception as exc:  # ImportError or a broken native build
            raise WakeWordUnavailable(f"pocketsphinx is not available: {exc}") from exc
        model = Path(get_model_path()) / "en-us" / "en-us"
        if not model.exists():
            raise WakeWordUnavailable(f"PocketSphinx acoustic model not found at {model}")
        self._tmp = tempfile.TemporaryDirectory(prefix="arnox-wake-")
        dictionary = _write_dictionary(Path(self._tmp.name))
        try:
            self._decoder = Decoder(
                hmm=str(model),
                dict=str(dictionary),
                keyphrase=WAKE_KEYPHRASE,
                kws_threshold=float(threshold),
                logfn=os.devnull,
            )
        except Exception as exc:
            self._tmp.cleanup()
            raise WakeWordUnavailable(f"Could not start the wake-word spotter: {exc}") from exc
        self._in_utterance = False

    def reset(self) -> None:
        if self._in_utterance:
            self._decoder.end_utt()
            self._in_utterance = False

    def feed(self, pcm_int16: bytes) -> bool:
        if not pcm_int16:
            return False
        if not self._in_utterance:
            self._decoder.start_utt()
            self._in_utterance = True
        self._decoder.process_raw(pcm_int16, False, False)
        if self._decoder.hyp() is not None:
            # Restart so one utterance triggers once and the search state is clean.
            self._decoder.end_utt()
            self._decoder.start_utt()
            return True
        return False

    def close(self) -> None:
        self.reset()
        self._tmp.cleanup()
