"""Tests for app.telegram_html.sanitize — the gate every LLM string passes through before
it is sent to Telegram. CLAUDE.md: 'always pass LLM output through telegram_html.sanitize();
never send raw model output.' These lock in the escape/allowlist invariants."""
from app.telegram_html import sanitize, split_message, TELEGRAM_MAX


def test_escapes_bare_angle_brackets():
    assert sanitize("a < b > c") == "a &lt; b &gt; c"


def test_preserves_allowed_tags():
    assert sanitize("<b>bold</b> <i>it</i> <code>x</code>") == "<b>bold</b> <i>it</i> <code>x</code>"


def test_preserves_anchor_with_attrs():
    out = sanitize('<a href="https://x.com">link</a>')
    assert out == '<a href="https://x.com">link</a>'


def test_escapes_disallowed_tag():
    # script is not in the Telegram allowlist -> escaped, not preserved.
    assert sanitize("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_escapes_bare_ampersand():
    assert sanitize("Tom & Jerry") == "Tom &amp; Jerry"


def test_does_not_double_escape_entities():
    assert sanitize("already &amp; fine &lt;x&gt;") == "already &amp; fine &lt;x&gt;"


def test_non_string_input_is_coerced():
    assert sanitize(None) == ""
    assert sanitize(123) == "123"


def test_split_short_message_is_single_chunk():
    assert split_message("hello") == ["hello"]


def test_split_long_message_respects_limit():
    text = "\n".join(["x" * 100 for _ in range(100)])  # ~10k chars
    chunks = split_message(text)
    assert len(chunks) > 1
    assert all(len(c) <= TELEGRAM_MAX for c in chunks)


def test_split_hard_slices_overlong_line():
    chunks = split_message("y" * (TELEGRAM_MAX + 50))
    assert all(len(c) <= TELEGRAM_MAX for c in chunks)
