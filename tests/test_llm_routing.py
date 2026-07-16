"""Tests for the reasoning-model routing in app/llm (_model_chain skip filter +
complete/complete_ex skip_reasoning plumbing). Long-form prose paths (audio-briefing scripts,
entity briefs) exclude config.REASONING_MODELS after their <think> planning leaked past the
format guards (2026-07-07 entity-brief template echo; 2026-07-06 truncated standard show).

No network: _chat is patched; _model_chain is pure given config.
"""
from __future__ import annotations

from types import SimpleNamespace

from app import llm


def _fake_resp(content="ok", finish="stop"):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


def _patch_chain(monkeypatch, nim=("nim-plain", "nim-think"), openrouter=("or-plain", "or-think")):
    monkeypatch.setattr(llm.config, "NIM_API_KEY", "test-key")
    monkeypatch.setattr(llm.config, "NIM_MODELS", list(nim))
    monkeypatch.setattr(llm.config, "OPENROUTER_MODELS", list(openrouter))


def test_model_chain_orders_nim_before_openrouter(monkeypatch):
    _patch_chain(monkeypatch)
    assert llm._model_chain() == [
        ("nim", "nim-plain"), ("nim", "nim-think"),
        ("openrouter", "or-plain"), ("openrouter", "or-think"),
    ]


def test_model_chain_skip_filters_across_providers(monkeypatch):
    _patch_chain(monkeypatch)
    assert llm._model_chain({"nim-think", "or-think"}) == [
        ("nim", "nim-plain"), ("openrouter", "or-plain"),
    ]


def test_model_chain_skip_never_empties(monkeypatch):
    # Filtering away EVERY model must fall back to the full chain — multiple-models-always
    # beats a perfect exclusion (a chain of zero models would kill the pipeline outright).
    _patch_chain(monkeypatch)
    full = llm._model_chain()
    assert llm._model_chain({"nim-plain", "nim-think", "or-plain", "or-think"}) == full


def test_complete_ex_forwards_reasoning_skip(monkeypatch):
    captured = {}

    def fake_chat(skip_models=None, **kwargs):
        captured["skip"] = skip_models
        return _fake_resp("hi"), "some-model"

    monkeypatch.setattr(llm, "_chat", fake_chat)
    content, model, finish = llm.complete_ex("sys", "user", skip_reasoning=True)
    assert content == "hi" and model == "some-model" and finish == "stop"
    assert captured["skip"] == llm.config.REASONING_MODELS

    llm.complete_ex("sys", "user")
    assert captured["skip"] is None


def test_complete_forwards_reasoning_skip(monkeypatch):
    captured = {}

    def fake_chat(skip_models=None, **kwargs):
        captured["skip"] = skip_models
        return _fake_resp("body"), "m"

    monkeypatch.setattr(llm, "_chat", fake_chat)
    content, model = llm.complete("sys", "user", skip_reasoning=True)
    assert (content, model) == ("body", "m")
    assert captured["skip"] == llm.config.REASONING_MODELS
