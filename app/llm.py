"""OpenRouter (OpenAI-compatible) wrapper: a plain completion and a tool-use loop."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from typing import Callable

from openai import (OpenAI, AuthenticationError, BadRequestError,
                    APIConnectionError, APIStatusError, APITimeoutError)

from . import config

log = logging.getLogger(__name__)

MAX_TOKENS = 2000

# Transient server errors (a provider's "no healthy upstream" 503, gateway 5xx, or a
# dropped connection) are often brief — retry the SAME model once before falling through
# to the next model in OPENROUTER_MODELS. 429 (rate-limited) and 404 (no such model) are
# NOT transient: skip straight to the next model. See OPENROUTER_MODELS in config/.env.
_TRANSIENT_STATUSES = {500, 502, 503, 504}
_TRANSIENT_RETRIES = 1      # extra attempts per model on a transient error
_BACKOFF_SEC = 1.5

_clients: dict[str, OpenAI] = {}

# ── Optional global rate limiter (config.LLM_MAX_CALLS_PER_MIN, 0 = off) ──────
# A sliding 60s window over provider calls, shared across threads (bullet analyses run
# 3-wide). Used to stay under free-tier limits during bulk backfills.
_RL_LOCK = threading.Lock()
_RL_TIMES: deque = deque()


def _rate_limit() -> None:
    cap = config.LLM_MAX_CALLS_PER_MIN
    if cap <= 0:
        return
    while True:
        with _RL_LOCK:
            now = time.monotonic()
            while _RL_TIMES and now - _RL_TIMES[0] > 60.0:
                _RL_TIMES.popleft()
            if len(_RL_TIMES) < cap:
                _RL_TIMES.append(now)
                return
            wait = 60.0 - (now - _RL_TIMES[0]) + 0.05
        time.sleep(max(0.05, wait))


def _client_for(provider: str) -> OpenAI:
    """Lazily build (and cache) the OpenAI-compatible client for a provider."""
    if provider not in _clients:
        if provider == "nim":
            _clients[provider] = OpenAI(api_key=config.NIM_API_KEY, base_url=config.NIM_BASE_URL,
                                        timeout=config.LLM_TIMEOUT_SEC, max_retries=0)
        else:
            _clients[provider] = OpenAI(api_key=config.OPENROUTER_API_KEY, base_url=config.OPENROUTER_BASE_URL,
                                        timeout=config.LLM_TIMEOUT_SEC, max_retries=0)
    return _clients[provider]


def _model_chain(skip_models: "frozenset[str] | set[str] | None" = None) -> list[tuple[str, str]]:
    """Ordered (provider, model) attempts: NVIDIA NIM first, then OpenRouter.

    ``skip_models`` filters models out of the chain (long-form prose callers skip the
    reasoning models — see config.REASONING_MODELS). The filter is IGNORED when it would
    empty the chain: multiple-models-always beats a perfect exclusion."""
    chain: list[tuple[str, str]] = []
    if config.NIM_API_KEY and config.NIM_MODELS:
        chain += [("nim", m) for m in config.NIM_MODELS]
    chain += [("openrouter", m) for m in config.OPENROUTER_MODELS]
    if skip_models:
        filtered = [(p, m) for p, m in chain if m not in skip_models]
        if filtered:
            return filtered
    return chain


def _retryable(exc: Exception) -> bool:
    # Brief blips worth a same-model retry. A timeout is NOT retried (it'd just hang
    # again) — fall straight through to the next model.
    if isinstance(exc, APIConnectionError):
        return True
    return getattr(exc, "status_code", None) in _TRANSIENT_STATUSES


def _chat(skip_models: "frozenset[str] | set[str] | None" = None, **kwargs) -> tuple:
    """Try each (provider, model) in the chain; retry brief 5xx/conn blips once per model.

    Falls through to the next model on ANY API error (429/404/5xx/timeout/auth/400) so a
    single bad provider, model, or key never kills the pipeline. Returns (response,
    model_name); raises the last error only when every model is exhausted.
    """
    chain = _model_chain(skip_models)
    if not chain:
        raise RuntimeError("no LLM models configured (set NIM_MODELS or OPENROUTER_MODELS)")
    last_exc: Exception | None = None
    for provider, model in chain:
        for attempt in range(_TRANSIENT_RETRIES + 1):
            try:
                _rate_limit()  # global throttle (no-op unless LLM_MAX_CALLS_PER_MIN > 0)
                log.debug("llm call: %s/%s (attempt %d)", provider, model, attempt + 1)
                resp = _client_for(provider).chat.completions.create(model=model, **kwargs)
                return resp, model
            except (APIStatusError, APIConnectionError, APITimeoutError,
                    AuthenticationError, BadRequestError) as exc:
                last_exc = exc
                status = getattr(exc, "status_code", "—")
                if _retryable(exc) and attempt < _TRANSIENT_RETRIES:
                    log.warning("%s/%s transient (status=%s) — retry in %.1fs",
                                provider, model, status, _BACKOFF_SEC)
                    time.sleep(_BACKOFF_SEC)
                    continue
                log.warning("%s/%s failed (status=%s) — trying next model", provider, model, status)
                break
    raise last_exc or RuntimeError("no models available")


def complete_ex(system: str, user: str, max_tokens: int = MAX_TOKENS,
                temperature: float | None = None,
                json_mode: bool = False,
                skip_reasoning: bool = False,
                timeout: float | None = None) -> tuple[str, str, str]:
    """Like ``complete()`` but also returns the provider ``finish_reason`` — ``(content,
    model_used, finish_reason)``.

    ``finish_reason == "length"`` means the model hit ``max_tokens`` and its output was CUT OFF
    mid-generation (a truncated, un-closed response). Callers that render long free-form output
    (e.g. the audio-briefing script) need this to tell a genuine, cleanly-finished draft from a
    silently-truncated one — the content alone looks the same until you notice it ends mid-sentence.

    ``timeout`` (seconds) overrides config.LLM_TIMEOUT_SEC for THIS request only. The global 60s
    ceiling exists so a hung model falls through fast, but a ~7200-token explainer script at
    ~50 tok/s legitimately needs ~145s — without the override the primary model times out on
    exactly the longest (most valuable) generations (2026-07-15 explainer incident).
    """
    kwargs: dict = {
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if timeout is not None:
        kwargs["timeout"] = timeout  # per-request option, honored by the OpenAI client
    # skip_reasoning: long-form prose paths (audio-briefing scripts, entity briefs) exclude
    # the reasoning models — their <think> planning burns the token budget and has leaked as
    # the answer itself (2026-07-07 brief incident). JSON-shaped tasks keep the full chain.
    resp, model = _chat(skip_models=config.REASONING_MODELS if skip_reasoning else None,
                        **kwargs)
    choice = resp.choices[0]
    finish = (getattr(choice, "finish_reason", None) or "").lower()
    return (choice.message.content or "").strip(), model, finish


def complete(system: str, user: str, max_tokens: int = MAX_TOKENS,
             temperature: float | None = None, json_mode: bool = False,
             skip_reasoning: bool = False, timeout: float | None = None) -> tuple[str, str]:
    """Returns (content, model_used).

    temperature: lower = more deterministic; pass ~0.2 for JSON/format tasks, ~0.5 for prose.
    json_mode: request `response_format=json_object` (provider falls through if a model rejects it).
    skip_reasoning: exclude config.REASONING_MODELS from the chain — for long-form prose paths
    where visible chain-of-thought has leaked past format guards (never empties the chain).
    timeout: per-request override of config.LLM_TIMEOUT_SEC (long-form generations need >60s).
    """
    content, model, _ = complete_ex(system, user, max_tokens=max_tokens,
                                    temperature=temperature, json_mode=json_mode,
                                    skip_reasoning=skip_reasoning, timeout=timeout)
    return content, model


# ── Reasoning-leak strip (shared primitive) ──────────────────────────────────
# A reasoning fallback model can emit its chain-of-thought in <think>…</think> tags
# (or an unclosed <think> that runs to the end). Every persist path strips these
# BEFORE its own content guard; the guards themselves (digest._keep_bullets_only,
# entity_brief._clean_brief, podcasts._clean_map_note, briefing's inline-planning
# detector) stay per-module — their rejection phrases are anchored to each prompt.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    """Remove <think> blocks (closed or unclosed-to-EOF) from model output and trim."""
    text = _THINK_RE.sub("", text or "")
    return _THINK_OPEN_RE.sub("", text).strip()


# ── Robust JSON parsing for model output ─────────────────────────────────────
def parse_json_loose(text: str):
    """Extract a JSON value from a model response that may wrap it in prose or ``` fences.

    Strips code fences, then returns the first balanced {...} object or [...] array.
    Raises (json.JSONDecodeError / ValueError) if nothing parseable is found — callers
    keep their existing try/except fallbacks.
    """
    if not text:
        raise ValueError("empty response")
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = re.sub(r"^[a-zA-Z]+\n", "", s, count=1)  # drop a leading ```json language tag
    candidates = [i for i in (s.find("{"), s.find("[")) if i != -1]
    if not candidates:
        return json.loads(s)  # nothing bracket-like → let json raise a clear error
    start = min(candidates)
    stack: list[str] = []   # expected closers, innermost last (handles mixed { } / [ ])
    in_str = esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]":
            if stack:
                stack.pop()
            if not stack:
                return json.loads(s[start:i + 1])
    # Truncated (likely max_tokens cutoff): salvage as much as parses instead of losing
    # the whole structured result. Callers keep their try/except fallbacks.
    return _salvage_json(s[start:], in_str, stack)


def _salvage_json(frag: str, in_str: bool, stack: list[str]):
    """Best-effort parse of a truncated JSON fragment.

    Closes an open string and any still-open containers; if that doesn't parse, drops a
    dangling partial trailing element (back to the last comma / complete close) and retries.
    Raises if nothing parses, so callers' existing fallbacks still trigger.
    """
    closers = "".join(reversed(stack))
    attempts: list[str] = []
    if in_str:
        attempts.append(frag + '"' + closers)
    attempts.append(frag + closers)
    # Trim a half-written trailing value: cut back to the last complete element boundary.
    cut = max(frag.rfind(","), frag.rfind("]"), frag.rfind("}"))
    if cut != -1:
        head = frag[:cut + 1] if frag[cut] in "]}" else frag[:cut]
        attempts.append(head + closers)
    for cand in attempts:
        try:
            return json.loads(cand)
        except (ValueError, TypeError):
            continue
    return json.loads(frag)  # nothing salvageable → let json raise the clearest error


def run_agent(
    system: str,
    history: list[dict],
    user_text: str,
    tools: list[dict],
    tool_impls: dict[str, Callable[[dict], str]],
    max_steps: int = config.AGENT_MAX_STEPS,
) -> tuple[str, str]:
    """ReAct loop via OpenAI function calling. Returns (content, model_used).

    ``history`` is prior turns (role user/assistant, alternating, ends on assistant).
    Loops while the model emits ``tool_calls``, feeding each result back as a
    ``role=tool`` message, until it answers or the step cap is hit.
    """
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    msg = None
    _chain = _model_chain()
    last_model = _chain[0][1] if _chain else ""
    for step in range(max_steps):
        # On the final allowed step, drop tools so the model must answer.
        use_tools = step < max_steps - 1
        kwargs: dict = {"max_tokens": MAX_TOKENS, "messages": messages}
        if use_tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp, last_model = _chat(**kwargs)
        msg = resp.choices[0].message

        if not msg.tool_calls:
            return (msg.content or "").strip(), last_model

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            impl = tool_impls.get(tc.function.name)
            try:
                output = impl(args) if impl else f"unknown tool {tc.function.name}"
            except Exception as exc:
                log.exception("tool %s failed", tc.function.name)
                output = f"tool error: {exc}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": output})

    log.warning("agent hit max_steps (%d) without finishing", max_steps)
    return (msg.content or "").strip() if msg else "⚠️ Could not complete the request.", last_model
