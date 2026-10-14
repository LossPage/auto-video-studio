"""Text generation providers (scripts, SEO, channel bible). Returns parsed JSON for structured calls.

anthropic → official SDK (Claude), gemini → Google AI Studio / Vertex express key, openai → Chat Completions.
"""
from __future__ import annotations

import json
import os
import re

from .util import http_json, log, secret, t

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "gemini": "gemini-3.8-flash", "openai": "gpt-5"}
KEY_NAMES = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
# Models that accept server-side refusal fallbacks ("default" routing)
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

USAGE: list[dict] = []  # appended per call: {provider, model, input, output}


class LLMUnavailable(RuntimeError):
    pass


def available(cfg) -> bool:
    p = cfg["llm"]["provider"]
    return p in KEY_NAMES and bool(os.environ.get(KEY_NAMES[p]))


def _model(cfg) -> str:
    return cfg["llm"].get("model") or DEFAULT_MODELS.get(cfg["llm"]["provider"], "")


def parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            return json.loads(text[s:e + 1])
        raise


def generate(cfg, system: str, prompt: str, schema: dict | None = None, max_tokens: int = 64000):
    """→ dict when schema is given (JSON output), else str."""
    p = cfg["llm"]["provider"]
    if p == "none" or p not in KEY_NAMES:
        raise LLMUnavailable(t("Yapay zeka sağlayıcısı seçilmemiş (llm.provider).", "No AI provider configured (llm.provider)."))
    key = secret(KEY_NAMES[p])
    model = _model(cfg)
    log(f"LLM {p}/{model} …")
    if p == "anthropic":
        text = _anthropic(key, model, cfg["llm"].get("effort", "medium"), system, prompt, schema, max_tokens)
    elif p == "gemini":
        text = _gemini(cfg, key, model, system, prompt, schema is not None)
    else:
        text = _openai(key, model, system, prompt, schema is not None)
    return parse_json(text) if schema is not None else text


def _anthropic(key, model, effort, system, prompt, schema, max_tokens):
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    out_cfg = {"effort": effort}
    if schema is not None:
        out_cfg["format"] = {"type": "json_schema", "schema": schema}
    kwargs = dict(model=model, max_tokens=max_tokens, system=system, thinking={"type": "adaptive"},
                  output_config=out_cfg, messages=[{"role": "user", "content": prompt}])
    if model in _FALLBACK_MODELS:
        stream = client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    else:
        stream = client.messages.stream(**kwargs)
    with stream as s:
        msg = s.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(t("Model isteği reddetti; konsepti/istemi gözden geçirin.", "The model declined the request; review the concept/prompt."))
    if msg.stop_reason == "max_tokens":
        raise RuntimeError(t("Model çıktısı kesildi (max_tokens).", "Model output was cut off (max_tokens)."))
    USAGE.append(dict(provider="anthropic", model=model, input=msg.usage.input_tokens, output=msg.usage.output_tokens))
    return "".join(b.text for b in msg.content if b.type == "text")


def gemini_url(cfg, model: str, key: str) -> tuple[str, dict]:
    if cfg.get("google", {}).get("endpoint") == "vertex":
        return (f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent",
                {"x-goog-api-key": key})
    return (f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", {"x-goog-api-key": key})


def _gemini(cfg, key, model, system, prompt, as_json):
    url, hd = gemini_url(cfg, model, key)
    body = dict(systemInstruction=dict(parts=[dict(text=system)]),
                contents=[dict(role="user", parts=[dict(text=prompt)])],
                generationConfig=dict(responseMimeType="application/json") if as_json else {})
    r = http_json(url, body, hd, timeout=600)
    u = r.get("usageMetadata", {})
    USAGE.append(dict(provider="gemini", model=model, input=u.get("promptTokenCount", 0), output=u.get("candidatesTokenCount", 0)))
    try:
        return "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini: {json.dumps(r)[:400]}") from None


def _openai(key, model, system, prompt, as_json):
    body = dict(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}])
    if as_json:
        body["response_format"] = {"type": "json_object"}
    r = http_json("https://api.openai.com/v1/chat/completions", body, {"Authorization": f"Bearer {key}"}, timeout=600)
    u = r.get("usage", {})
    USAGE.append(dict(provider="openai", model=model, input=u.get("prompt_tokens", 0), output=u.get("completion_tokens", 0)))
    return r["choices"][0]["message"]["content"]
