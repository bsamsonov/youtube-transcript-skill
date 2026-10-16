"""Offline tests for the pure logic of yt_transcript.py (no network)."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "skills/youtube-transcript/scripts/yt_transcript.py"
spec = importlib.util.spec_from_file_location("yt_transcript", SCRIPT)
yt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yt)


@pytest.mark.parametrize("url", [
    "aircAruvnKk",
    "https://youtu.be/aircAruvnKk?si=AbCdEfGhIjKlMnOp",
    "https://www.youtube.com/watch?v=aircAruvnKk&t=42s",
    "https://m.youtube.com/watch?feature=share&v=aircAruvnKk",
    "https://www.youtube.com/shorts/aircAruvnKk",
    "https://www.youtube.com/live/aircAruvnKk",
    "https://www.youtube.com/embed/aircAruvnKk",
])
def test_extract_video_id(url):
    assert yt.extract_video_id(url) == "aircAruvnKk"


def test_extract_video_id_rejects_channel():
    with pytest.raises(ValueError):
        yt.extract_video_id("https://www.youtube.com/@SomeChannel")


class Track:
    def __init__(self, lang, generated, variant=None):
        self.language_code = lang
        self.is_generated = generated
        self._url = f"https://www.youtube.com/api/timedtext?kind=asr&lang={lang}"
        if variant:
            self._url += f"&variant={variant}"


class Tracks(list):
    video_id = "x"

    def find_transcript(self, langs):
        return next(t for t in self if t.language_code in langs)


def test_skips_auto_dub_translation():
    tl = Tracks([Track("en", True, "timing-optimized"), Track("uk", True)])
    assert yt.pick_original(tl, None).language_code == "uk"


def test_gemini_variant_is_original_speech():
    tl = Tracks([Track("en", True, "timing-optimized"), Track("ru", True, "gemini")])
    assert yt.pick_original(tl, None).language_code == "ru"


def test_manual_track_in_spoken_language_wins():
    tl = Tracks([Track("de", False), Track("ru", True), Track("ru", False)])
    picked = yt.pick_original(tl, None)
    assert picked.language_code == "ru" and not picked.is_generated


def test_forced_language():
    tl = Tracks([Track("en", True, "timing-optimized"), Track("ru", True)])
    assert yt.pick_original(tl, ["en"]).language_code == "en"


def test_paragraphs_break_on_sentence_after_a_minute():
    segs = [(0, "One."), (30, "Two."), (61, "Three."), (70, "Four.")]
    assert yt.to_paragraphs(segs) == "[0:00] One. Two. Three.\n\n[1:10] Four."
    assert yt.to_paragraphs(segs, plain=True) == "One. Two. Three.\n\nFour."


def test_fmt_ts():
    assert yt.fmt_ts(59) == "0:59"
    assert yt.fmt_ts(3725) == "1:02:05"


def test_slugify_keeps_unicode():
    assert yt.slugify("Café Olé: Tech Talks!") == "Café_Olé_Tech_Talks"
