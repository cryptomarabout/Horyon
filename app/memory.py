"""Per-chat conversation memory, stored in the ``chat_history`` table.

Schema: (id, chat_id, role, content, created_at). ``role`` is already the
OpenAI role ('user'|'assistant'), so retrieval needs no translation.
"""
from __future__ import annotations

from . import config, db


def load(chat_id: str | int, window: int | None = None) -> list[dict]:
    """Return the last ``window`` turns as ``[{role, content}]``.

    Rows are written as user/assistant pairs in order, so the result is already
    alternating; we still drop a leading assistant and a trailing user so the
    caller can append the current user message and stay valid for the API.
    """
    window = window or config.MEMORY_WINDOW
    with db._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT role, content FROM chat_history WHERE chat_id = %s "
            "ORDER BY id DESC LIMIT %s",
            (str(chat_id), window),
        )
        rows = cur.fetchall()[::-1]

    msgs = [{"role": r, "content": c} for r, c in rows if r and c]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    return msgs


def save(chat_id: str | int, user_text: str, assistant_text: str) -> None:
    with db._conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chat_history (chat_id, role, content) VALUES (%s, %s, %s)",
            [
                (str(chat_id), "user", user_text),
                (str(chat_id), "assistant", assistant_text),
            ],
        )
