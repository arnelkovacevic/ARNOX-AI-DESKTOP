import builtins
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
from api.websocket_client import WebSocketClient
from core import wake_word

try:
    import pocketsphinx  # noqa: F401
    HAVE_POCKETSPHINX = True
except Exception:
    HAVE_POCKETSPHINX = False

CAN_SYNTHESIZE = sys.platform == "darwin" and bool(shutil.which("say")) and bool(shutil.which("afconvert"))


def _synthesize(directory: Path, name: str, text: str) -> bytes:
    aiff = directory / f"{name}.aiff"
    wav = directory / f"{name}.wav"
    subprocess.run(["say", "-o", str(aiff), text], check=True, capture_output=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
        check=True, capture_output=True,
    )
    with wave.open(str(wav)) as handle:
        return handle.readframes(handle.getnframes()) + b"\x00" * 16000


def _room_noise(seconds: float = 5.0, amplitude: int = 60) -> bytes:
    """Background the detector would have been listening to before the word is spoken."""
    import numpy as np

    rng = np.random.default_rng(1)
    return rng.normal(0, amplitude, int(16000 * seconds)).astype("int16").tobytes()


def _heard(detector, pcm: bytes) -> bool:
    detector.reset()
    pcm = _room_noise() + pcm
    heard = False
    for start in range(0, len(pcm), 2048):
        if detector.feed(pcm[start:start + 2048]):
            heard = True
    return heard


@unittest.skipUnless(HAVE_POCKETSPHINX and CAN_SYNTHESIZE, "needs pocketsphinx and macOS speech synthesis")
class WakeWordDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.detector = wake_word.WakeWordDetector()

    @classmethod
    def tearDownClass(cls):
        cls.detector.close()
        cls.tmp.cleanup()

    def test_hears_the_wake_word_alone_and_inside_phrases(self):
        for index, text in enumerate(("Arnox", "Arnox wake up", "Probudi se Arnox")):
            with self.subTest(text=text):
                self.assertTrue(_heard(self.detector, _synthesize(self.dir, f"pos{index}", text)))

    def test_ignores_ordinary_speech(self):
        sentences = (
            "Can you open the calendar and show me the meetings for Monday",
            "Please send an email to the team about the project deadline",
            "What is the weather like in Sarajevo today",
            "Turn the music down a little because I am on the phone",
        )
        for index, text in enumerate(sentences):
            with self.subTest(text=text):
                self.assertFalse(_heard(self.detector, _synthesize(self.dir, f"neg{index}", text)))

    def test_silence_never_wakes(self):
        self.assertFalse(_heard(self.detector, b"\x00" * 32000 * 5))


class WakeWordAvailabilityTests(unittest.TestCase):
    def test_missing_library_raises_a_clear_error(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pocketsphinx":
                raise ImportError("simulated missing library")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", fake_import):
            with self.assertRaises(wake_word.WakeWordUnavailable):
                wake_word.WakeWordDetector()

    def test_main_falls_back_to_none_when_detector_is_unavailable(self):
        with patch("core.wake_word.WakeWordDetector", side_effect=wake_word.WakeWordUnavailable("x")):
            self.assertIsNone(main.JarvisLive._create_wake_detector())

    def test_threshold_can_be_overridden_by_environment(self):
        if not HAVE_POCKETSPHINX:
            self.skipTest("pocketsphinx not installed")
        with patch.dict("os.environ", {"ARNOX_WAKE_THRESHOLD": "1e-25"}):
            detector = wake_word.WakeWordDetector()
            detector.close()


class _Detector:
    def __init__(self, hit=True):
        self.hit = hit
        self.fed = 0
        self.resets = 0

    def reset(self):
        self.resets += 1

    def feed(self, _pcm):
        self.fed += 1
        return self.hit


class _Audio:
    def tobytes(self):
        return b"\x00\x00" * 1024


class SleepListeningTests(unittest.TestCase):
    def _live(self, detector, muted=False):
        live = main.JarvisLive.__new__(main.JarvisLive)
        woken = []
        live.ui = SimpleNamespace(
            muted=muted,
            notify_user_activity=lambda text, typed=False: woken.append((text, typed)),
        )
        live._wake_detector = detector
        live._sleep_started_at = None
        return live, woken

    def test_first_sleeping_frame_warms_up_the_detector_without_waking(self):
        detector = _Detector()
        live, woken = self._live(detector)
        live._feed_wake_word(_Audio(), jarvis_speaking=False)
        self.assertEqual(detector.fed, 1)
        self.assertEqual(woken, [])

    def test_goodbye_window_ignores_hits_so_arnox_cannot_wake_itself(self):
        detector = _Detector(hit=True)
        live, woken = self._live(detector)
        live._feed_wake_word(_Audio(), jarvis_speaking=False)
        live._feed_wake_word(_Audio(), jarvis_speaking=False)
        self.assertGreaterEqual(detector.fed, 2)
        self.assertEqual(woken, [])

    def test_wake_word_after_the_delay_notifies_the_ui_by_voice(self):
        detector = _Detector()
        live, woken = self._live(detector)
        with patch.object(main, "WAKE_ARM_DELAY_SECONDS", 0.0):
            live._feed_wake_word(_Audio(), jarvis_speaking=False)
        self.assertEqual(woken, [("arnox", False)])
        self.assertIsNone(live._sleep_started_at)

    def test_nothing_is_fed_while_arnox_is_speaking_or_muted(self):
        detector = _Detector()
        live, woken = self._live(detector)
        with patch.object(main, "WAKE_ARM_DELAY_SECONDS", 0.0):
            live._feed_wake_word(_Audio(), jarvis_speaking=True)
        self.assertEqual(detector.fed, 0)
        live, woken = self._live(detector, muted=True)
        with patch.object(main, "WAKE_ARM_DELAY_SECONDS", 0.0):
            live._feed_wake_word(_Audio(), jarvis_speaking=False)
        self.assertEqual(detector.fed, 0)

    def test_missing_detector_means_typing_is_the_only_wake_path(self):
        live, woken = self._live(None)
        live._feed_wake_word(_Audio(), jarvis_speaking=False)
        self.assertEqual(woken, [])


class HostedClientCompatibilityTests(unittest.TestCase):
    def test_hosted_client_has_safe_sleep_methods(self):
        client = WebSocketClient.__new__(WebSocketClient)
        self.assertFalse(client.sleeping)
        self.assertIsNone(client.enter_sleep())
        self.assertIsNone(client.notify_user_activity("hello", typed=True))
        self.assertIsNone(client.notify_reply_finished())


if __name__ == "__main__":
    unittest.main()
