"""Regression tests for the audio-briefing clean-close safeguard in app/briefing.py.

The 2026-07-05 truncation bug: the standard + explainer scripts hit the LLM `max_tokens` ceiling
and were stored (and synthesized) CUT OFF mid-sentence, with no sign-off — the last story trailed
off ("...It's a way to", "...You mentioned the token lives on"). The fix has three layers; this
file pins the deterministic backstop (`_finalize_close`) that must guarantee a clean close on EVERY
variant regardless of why a draft came in short.

Pure functions only — no DB, LLM, or TTS needed."""
from app import config
from app.briefing import (_SIGN_OFF, _finalize_close, _has_signoff,
                          _trim_partial_sentence)


def test_sign_off_defined_for_every_variant():
    # _finalize_close guarantees a close on all three shows, so each needs a canonical line.
    for v in config.BRIEFING_VARIANTS:
        assert _SIGN_OFF.get(v), f"{v}: missing canonical sign-off"


def test_trim_keeps_a_complete_sentence_untouched():
    t = "Binance launched bStocks on the BNB Chain. It settles instantly on chain."
    assert _trim_partial_sentence(t) == t


def test_trim_drops_a_dangling_fragment_but_keeps_prior_sentences():
    # A truncated turn: two complete sentences, then a cut-off fragment.
    t = "Binance launched bStocks. It converts securities to stocks. It's a way to"
    assert _trim_partial_sentence(t) == "Binance launched bStocks. It converts securities to stocks."


def test_trim_returns_empty_when_no_complete_sentence():
    # A turn that is nothing but a fragment → caller drops the whole turn.
    assert _trim_partial_sentence("You mentioned the token lives on") == ""


def test_trim_does_not_cut_on_a_decimal_point():
    # The '.' in "2.5" is not a sentence boundary (no following whitespace) — must not truncate there.
    t = "TVL rose to 2.5 billion after the launch"
    assert _trim_partial_sentence(t) == ""  # no real sentence end at all → whole thing is a fragment


def test_finalize_drops_mid_sentence_fragment_turn_and_appends_sign_off():
    # Mirrors the stored explainer bug: last turn is a bare fragment.
    turns = [
        ("HOST", "Good morning, this is the deep dive.", 0),
        ("EXPERT", "Binance launched bStocks on the BNB Chain. It settles on chain.", 1),
        ("HOST", "You mentioned the token lives on", 1),  # truncated fragment
    ]
    titles = ["Intro", "bStocks"]
    out, out_titles = _finalize_close(turns, titles, "explainer")
    # The fragment turn is gone; the show ends on the canonical explainer sign-off.
    assert all(txt != "You mentioned the token lives on" for _, txt, _ in out)
    assert out[-1][1] == _SIGN_OFF["explainer"]
    assert out[-1][0] == "HOST"
    assert _has_signoff(out[-1][1])


def test_finalize_trims_partial_tail_within_the_last_turn():
    # Mirrors the stored standard bug: last turn has good sentences then a cut-off tail.
    turns = [
        ("HOST", "Welcome to the briefing.", 0),
        ("EXPERT", "Binance launched bStocks. It converts securities. It's a way to", 1),
    ]
    out, _ = _finalize_close(turns, ["Intro", "bStocks"], "standard")
    # The dangling "It's a way to" is trimmed, and the standard sign-off is appended.
    assert "It's a way to" not in out[-2][1]
    assert out[-2][1].endswith("It converts securities.")
    assert out[-1][1] == _SIGN_OFF["standard"]


def test_finalize_does_not_double_up_an_existing_sign_off():
    turns = [
        ("HOST", "Welcome.", 0),
        ("EXPERT", "Aave shipped V4.", 1),
        ("HOST", _SIGN_OFF["standard"], 1),
    ]
    out, _ = _finalize_close(turns, ["Intro", "Aave"], "standard")
    signoffs = [txt for _, txt, _ in out if _has_signoff(txt)]
    assert len(signoffs) == 1
    assert out[-1][1] == _SIGN_OFF["standard"]


def test_finalize_is_idempotent():
    turns = [
        ("HOST", "Welcome.", 0),
        ("EXPERT", "Aave shipped V4. It's live now.", 1),
        ("HOST", "You mentioned the token lives on", 1),
    ]
    once = _finalize_close(turns, ["Intro", "Aave"], "explainer")
    twice = _finalize_close(list(once[0]), list(once[1]), "explainer")
    assert once == twice


def test_finalize_trims_titles_to_surviving_chapters():
    # If a trailing chapter is entirely a fragment, its title should be dropped with it.
    turns = [
        ("HOST", "Welcome to the deep dive.", 0),
        ("EXPERT", "Aave shipped V4. It's live.", 1),
        ("HOST", "And now the last story is cut off here with no", 2),  # fragment-only chapter
    ]
    titles = ["Intro", "Aave", "Truncated Story"]
    out, out_titles = _finalize_close(turns, titles, "explainer")
    # Chapter 2 had no complete sentence → dropped; the sign-off rides on the surviving last chapter.
    assert max(ch for _, _, ch in out) == 1
    assert "Truncated Story" not in out_titles


def test_has_signoff_does_not_match_ordinary_dialogue():
    assert not _has_signoff("Aave V4 is the biggest lending market on Ethereum.")
    assert _has_signoff("That's your Horyon briefing. The full feed is at Horyon dot X Y Z.")
