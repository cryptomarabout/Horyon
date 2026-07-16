"""Regression tests for the reasoning-model leak guard in app/briefing.py (2026-07-03 incident):
nvidia/nemotron narrated its own planning inline under 'HOST:'/'EXPERT:' labels — syntactically
identical to real turns — so a chapter-marker-rehearsal preamble ("HOST: We need to produce a
script with at least 1035 words... Must include cold open...") and placeholder turns ("HOST: ...
(lead story)") reached the stored script and the TTS engine.

Pure functions only — no DB, LLM, or TTS needed."""
from app.briefing import _is_leak_turn, _normalize_for_speech, _speaker_turns

# Verbatim (trimmed) fragments from the incident's stored script, after `_SPEAKER_RE` has already
# stripped the leading 'HOST:'/'EXPERT:' label — this is the shape `_is_leak_turn` sees.
_LEAK_PREAMBLE = (
    "We need to produce a script with at least 1035 words, likely around 1100-1200 words. Must "
    "include cold open, then each story with chapter marker before each story. Must not invent new "
    "facts."
)
_LEAK_RECAP = (
    "... ... etc. We need to ensure each story has a marker line before its first turn. The cold "
    "open has no marker. We must not include any markdown, just plain text with labels."
)
_LEAK_PLACEHOLDERS = ["... (maybe tease lead story)", "... (lead story)", "... (follow-up question)",
                      "... (answer)", "...", "(transition)"]

_REAL_TURNS = [
    "Good morning. It's Friday, July 3, 2026, and this is Horyon Daily.",
    "So Binance is leading a fresh round for Mesh, the crypto-payments infrastructure provider.",
    "Mesh builds the on-and-off ramps and merchant tools that let users move between fiat and crypto.",
    "That's your Horyon briefing. The full feed and analysis are at Horyon dot X Y Z.",
    # The exact false-positive case caught during validation: a natural hand-off line containing
    # "let's start" must NOT be treated as a leak.
    "Daniel, let's start with the money moving into stablecoin credit.",
    "We must keep an eye on regulatory risk here, that's the real tail risk.",
]


def test_leak_preamble_and_recap_detected():
    assert _is_leak_turn(_LEAK_PREAMBLE)
    assert _is_leak_turn(_LEAK_RECAP)


def test_rehearsal_placeholders_detected():
    for p in _LEAK_PLACEHOLDERS:
        assert _is_leak_turn(p), f"placeholder not caught: {p!r}"


def test_real_dialogue_not_flagged():
    for t in _REAL_TURNS:
        assert not _is_leak_turn(t), f"false positive on real dialogue: {t!r}"


def test_stage_note_suffix_stripped_but_rest_of_turn_kept():
    spoken = _normalize_for_speech(
        "That feels like a signal about where the money's flowing next. Now marker.")
    assert "now marker" not in spoken.lower()
    assert "money's flowing next" in spoken.lower() or "money’s flowing next" in spoken.lower()

    spoken2 = _normalize_for_speech(
        "Not just the depth of the order book. Now move to next story.")
    assert "next story" not in spoken2.lower()
    assert "depth of the order book" in spoken2.lower()


def test_speaker_turns_end_to_end_drops_leak_keeps_real_dialogue():
    # Reproduces the incident's shape: a leaked planning preamble + rehearsal placeholders under
    # 'HOST:'/'EXPERT:' labels, followed by the real show.
    raw = (
        f"HOST: {_LEAK_PREAMBLE}\n\n"
        "HOST: ... (maybe tease lead story)\n\n"
        "EXPERT: ... (lead story)\n\n"
        f"EXPERT: {_LEAK_RECAP}\n\n"
        "HOST: Good morning. It's Friday, July 3, 2026, and this is Horyon Daily. Now marker.\n\n"
        "EXPERT: Mesh builds the on-and-off ramps and merchant tools for crypto payments.\n\n"
        "HOST: That's your Horyon briefing. The full feed and analysis are at Horyon dot X Y Z."
    )
    turns, _titles = _speaker_turns(raw)
    texts = [t for _sp, t, _ch in turns]
    assert len(turns) == 3, f"expected only the 3 real turns, got: {texts}"
    assert not any(_is_leak_turn(t) for t in texts)
    assert "now marker" not in texts[0].lower()
    assert any("mesh builds" in t.lower() for t in texts)
