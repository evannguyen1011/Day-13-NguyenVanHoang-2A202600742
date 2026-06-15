"""Mitigation + observability layer around the opaque agent (a REAL LLM).

The agent is silent: run_output.json only carries answer+status. EVERYTHING you can
see about latency/cost/tokens/tools/PII/loops/drift is built HERE, because call_next()
returns the FULL result (meta+trace) to us.

Legal moves used below: per-request observability, retry/backoff on transient failures,
thread-safe caching of repeated questions, input sanitisation of injected order notes,
output PII redaction, and prompt routing (we inject our rewritten system prompt). We import
ONLY the Python standard library and the bundled telemetry/ package.
"""
from __future__ import annotations

import os
import re
import time
import threading

# --- telemetry toolkit (optional: wrapper still runs if it is missing) ----------------
try:
    from telemetry.logger import logger, new_correlation_id, set_correlation_id
    from telemetry.cost import cost_from_usage
    from telemetry.redact import redact
except Exception:  # pragma: no cover
    logger = None

    def new_correlation_id():
        return None

    def set_correlation_id(_):
        return None

    def cost_from_usage(model, usage):
        return 0.0

    def redact(s):
        return (s, 0)

# --- our rewritten system prompt, loaded once -----------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_prompt():
    for path in (os.path.join(_HERE, "prompt.txt"), "solution/prompt.txt"):
        try:
            with open(path, encoding="utf-8") as f:
                txt = f.read().strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


SYSTEM_PROMPT = _load_prompt()

# --- injection defence: neutralise instructions hidden in order notes -----------------
# The private twist embeds a fake "system"/price instruction inside an order note. We keep
# the order data but strip imperative/override phrasing so it cannot steer the agent. This
# is a backstop; the system prompt is the primary defence.
_INJECT_PATTERNS = [
    re.compile(r"(?is)\bghi\s*ch[uú]\b\s*[:\-].*$"),          # drop everything after a note marker
    re.compile(r"(?is)\b(he\s*thong|h[eệ]\s*th[oố]ng|system|assistant)\b\s*[:\-].*$"),
    re.compile(r"(?is)\b(b[oỏ]\s*qua|ignore|disregard|override|ghi\s*d[eè])\b.*$"),
]


def _sanitize(question: str):
    """Return (clean_question, n_stripped). Conservative: only removes note/override tails."""
    if not isinstance(question, str):
        return question, 0
    clean = question
    n = 0
    for pat in _INJECT_PATTERNS:
        clean, k = pat.subn(" ", clean)
        n += k
    clean = re.sub(r"\s+", " ", clean).strip()
    # never blank out a real order
    return (clean if clean else question), n


def _cache_key(question: str, config: dict) -> str:
    return "|".join([
        (question or "").strip().lower(),
        str(config.get("model", "")),
        str(config.get("self_consistency", "")),
    ])


def _is_failure(result: dict) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("status") in ("wrapper_error", "error", "max_steps", "loop", "no_action"):
        return True
    return result.get("answer") in (None, "")


def mitigate(call_next, question, config, context):
    cid = new_correlation_id()
    if cid:
        set_correlation_id(cid)

    cache = context.get("cache")
    cache_lock = context.get("cache_lock")

    # 1) input sanitisation (strip injected note instructions)
    clean_q, n_stripped = _sanitize(question)

    # 2) cache hit (thread-safe) for repeated identical questions
    key = _cache_key(clean_q, config)
    if cache is not None and cache_lock is not None:
        with cache_lock:
            hit = cache.get(key)
        if hit is not None:
            _log(context, hit, cache_hit=True, injection_stripped=n_stripped, wall_ms=0)
            return hit

    # 3) prompt routing: force our rewritten system prompt onto the agent
    conf = dict(config)
    if SYSTEM_PROMPT:
        conf["system_prompt"] = SYSTEM_PROMPT

    # 4) retry with backoff on transient failures (tool errors / timeouts / exceptions)
    rcfg = config.get("retry") or {}
    attempts = max(1, int(rcfg.get("max_attempts", 3)))
    backoff = float(rcfg.get("backoff_ms", 250)) / 1000.0

    t0 = time.time()
    result = None
    last_exc = None
    for attempt in range(attempts):
        try:
            result = call_next(clean_q, conf)
        except Exception as e:  # network/tool blips -> retry
            last_exc = e
            result = None
        if result is not None and not _is_failure(result):
            break
        if attempt < attempts - 1:
            time.sleep(backoff * (attempt + 1))

    wall_ms = int((time.time() - t0) * 1000)

    if result is None:
        result = {"answer": None, "status": "wrapper_error", "steps": 0, "trace": [],
                  "meta": {"latency_ms": wall_ms, "usage": {}, "error": str(last_exc)}}

    # 5) output PII redaction (defence in depth on top of redact_pii config)
    ans = result.get("answer")
    if isinstance(ans, str):
        red, npii = redact(ans)
        if npii:
            result["answer"] = red
        result["_pii_redacted"] = npii

    _log(context, result, cache_hit=False, injection_stripped=n_stripped, wall_ms=wall_ms)

    # 6) store successful answers in cache
    if cache is not None and cache_lock is not None and not _is_failure(result):
        with cache_lock:
            cache[key] = result

    return result


def _log(context, result, cache_hit, injection_stripped, wall_ms):
    if not logger:
        return
    meta = result.get("meta", {}) if isinstance(result, dict) else {}
    usage = meta.get("usage", {}) or {}
    try:
        logger.log_event("AGENT_CALL", {
            "qid": context.get("qid"),
            "session": context.get("session_id"),
            "turn_index": context.get("turn_index"),
            "status": result.get("status"),
            "steps": result.get("steps"),
            "cache_hit": cache_hit,
            "injection_stripped": injection_stripped,
            "pii_in_answer": int(result.get("_pii_redacted", 0)),
            "reported_latency_ms": meta.get("latency_ms"),
            "wall_ms": wall_ms,
            "tokens": usage,
            "cost_usd": cost_from_usage(meta.get("model", ""), usage),
            "tools_used": meta.get("tools_used", []),
            "n_tools": len(meta.get("tools_used", []) or []),
        })
    except Exception:
        pass
