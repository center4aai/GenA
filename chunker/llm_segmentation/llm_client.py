"""LLM-клиент сегментации: модель из MODEL_ENDPOINTS или LLM_URL_MODEL, ретраи, дедлайн, фолбэк."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .blocks import Segment
from .windows import Window

logger = logging.getLogger(__name__)


class SegLLMBusy(Exception):
    """429/503 — хост занят."""


class SegLLMTimeout(Exception):
    pass


class SegLLMError(Exception):
    pass


@dataclass
class SegModelConfig:
    base_url: str
    model_name: str
    api_key: str = "none"
    extra: Dict[str, Any] = field(default_factory=dict)


def _env_key_ok(e: Dict[str, Any]) -> bool:
    """``api_key_env`` (as in agent_api): take the key from that variable; unset → skip the entry."""
    env_name = e.get("api_key_env")
    if not env_name:
        return True
    key = os.getenv(str(env_name), "").strip()
    if key:
        e["api_key"] = key
    return bool(key)


def resolve_model(model_id: Optional[str] = None) -> SegModelConfig:
    """CHUNKER_LLM_SEG_MODEL_ID из MODEL_ENDPOINTS, иначе LLM_URL_MODEL / LLM_MODEL_NAME."""
    wanted = (model_id or os.getenv("CHUNKER_LLM_SEG_MODEL_ID", "")).strip()
    raw = os.getenv("MODEL_ENDPOINTS", "").strip()
    endpoints: List[Dict[str, Any]] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                endpoints = [e for e in parsed if isinstance(e, dict) and _env_key_ok(e)]
        except json.JSONDecodeError as exc:
            logger.warning("MODEL_ENDPOINTS is not valid JSON: %s", exc)
    if wanted:
        for e in endpoints:
            if e.get("id") == wanted:
                return SegModelConfig(e["base_url"], e["model_name"], e.get("api_key") or "none", dict(e.get("extra") or {}))
        raise ValueError(f"chunking method 'llm' is not configured: model '{wanted}' not in MODEL_ENDPOINTS")
    url = os.getenv("LLM_URL_MODEL", "").strip()
    name = os.getenv("LLM_MODEL_NAME", "").strip()
    if url and name:
        extra = {}
        for e in endpoints:
            if e.get("base_url") == url and e.get("model_name") == name:
                extra = dict(e.get("extra") or {})
        return SegModelConfig(url, name, os.getenv("LLM_API_KEY", "").strip() or "none", extra)
    if endpoints:
        e = endpoints[0]
        return SegModelConfig(e["base_url"], e["model_name"], e.get("api_key") or "none", dict(e.get("extra") or {}))
    raise ValueError("chunking method 'llm' is not configured: LLM_URL_MODEL / MODEL_ENDPOINTS are not set")


class SegmentationLLM:
    """Один вызов chat.completions с выключенным thinking (как llm_factory agent_api)."""

    def __init__(self, cfg: SegModelConfig, *, timeout: float = 240.0, max_tokens: int = 1500, seed: int = 0) -> None:
        from openai import OpenAI

        self.cfg = cfg
        self.max_tokens = max_tokens
        self.seed = seed
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "none", timeout=timeout, max_retries=0)

    def complete(self, system: str, user: str) -> str:
        import openai

        extra_body: Dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}, "seed": self.seed, "cache_prompt": True}
        if self.cfg.extra.get("reasoning_effort"):
            extra_body["reasoning_effort"] = self.cfg.extra["reasoning_effort"]
        try:
            resp = self.client.chat.completions.create(
                model=self.cfg.model_name,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.0,
                max_tokens=self.max_tokens,
                extra_body=extra_body,
            )
        except openai.APITimeoutError as exc:
            raise SegLLMTimeout(str(exc)) from exc
        except openai.RateLimitError as exc:
            raise SegLLMBusy(str(exc)) from exc
        except openai.APIStatusError as exc:
            if getattr(exc, "status_code", 0) in (429, 503, 502, 504):
                raise SegLLMBusy(str(exc)) from exc
            raise SegLLMError(str(exc)) from exc
        except openai.APIConnectionError as exc:
            raise SegLLMBusy(str(exc)) from exc
        choice = resp.choices[0] if resp.choices else None
        return (choice.message.content if choice and choice.message else "") or ""


@dataclass
class WindowResult:
    segments: List[Segment]
    source: str            # llm | fallback
    attempts: int = 0
    seconds: float = 0.0
    truncated: bool = False
    error: str = ""
    gapfill: int = 0
    suspicious: bool = False
    continues_previous: bool = False


LabelFn = Callable[[Window, SegmentationLLM], "tuple"]   # → (segments, info)
FallbackFn = Callable[[Window], List[Segment]]

BACKOFF_STEPS = (5.0, 20.0, 60.0)


class WindowRunner:
    """Параллельная разметка окон с ретраями по busy, дедлайном и фолбэком."""

    def __init__(self, llm: Optional[SegmentationLLM], *, concurrency: int = 2, retries: int = 3,
                 deadline: Optional[float] = None, fallback: FallbackFn, sleep: Callable[[float], None] = time.sleep) -> None:
        self.llm = llm
        self.concurrency = max(1, concurrency)
        self.retries = max(0, retries)
        self.deadline = deadline
        self.fallback = fallback
        self.sleep = sleep
        self.stats: Dict[str, Any] = {"llm_calls": 0, "busy_retries": 0, "llm_seconds": 0.0, "breaker_pauses": 0}
        self._busy_streak = 0
        self._lock = threading.Lock()
        self._avg_seconds = 0.0
        self._done = 0
        self._deadline_hit = False

    def _time_left(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - time.monotonic()

    def _should_fallback_for_time(self, remaining_windows: int) -> bool:
        if self.deadline is None:
            return False
        if self._time_left() <= 0:
            return True
        if self._avg_seconds and remaining_windows * self._avg_seconds / self.concurrency > self._time_left():
            return True
        return False

    def _run_one(self, window: Window, label: LabelFn, total: int) -> WindowResult:
        from .contract import ContractError

        if self.llm is None or self._deadline_hit or self._should_fallback_for_time(total - self._done):
            self._deadline_hit = self._deadline_hit or self.llm is not None
            return WindowResult(self.fallback(window), "fallback", error="deadline" if self.llm else "no llm")
        attempts = 0
        t0 = time.monotonic()
        last_error = ""
        while True:
            attempts += 1
            try:
                with self._lock:
                    self.stats["llm_calls"] += 1
                segments, info = label(window, self.llm)
                with self._lock:
                    self._busy_streak = 0
                    dt = time.monotonic() - t0
                    self.stats["llm_seconds"] += dt
                    self._done += 1
                    self._avg_seconds = ((self._avg_seconds * (self._done - 1)) + dt) / self._done
                return WindowResult(segments, "llm", attempts, time.monotonic() - t0,
                                    info.get("truncated", False), gapfill=info.get("gapfill", 0),
                                    suspicious=info.get("suspicious", False),
                                    continues_previous=bool(info.get("continues_previous")))
            except SegLLMBusy as exc:
                last_error = f"busy: {exc}"
                with self._lock:
                    self.stats["busy_retries"] += 1
                    self._busy_streak += 1
                    streak = self._busy_streak
                if attempts > self.retries or self._time_left() <= 0:
                    break
                delay = BACKOFF_STEPS[min(attempts - 1, len(BACKOFF_STEPS) - 1)]
                if streak >= 3 * self.concurrency:
                    with self._lock:
                        self.stats["breaker_pauses"] += 1
                    delay = max(delay, 60.0)
                self.sleep(min(delay, max(0.0, self._time_left())))
            except SegLLMTimeout as exc:
                last_error = f"timeout: {exc}"
                if attempts > 1 or self._time_left() <= 0:
                    break
            except ContractError as exc:
                last_error = f"contract: {exc}"
                break
            except SegLLMError as exc:
                last_error = f"error: {exc}"
                break
        with self._lock:
            self._done += 1
        # Непарсибельный ответ при temperature 0 повторять бесполезно: делим окно пополам.
        if last_error.startswith("contract") and len(window.paras) >= 4:
            mid = len(window.paras) // 2
            halves = [
                Window(window.index, window.context, window.paras[:mid]),
                Window(window.index, window.paras[max(0, mid - 3):mid], window.paras[mid:]),
            ]
            merged: List[Segment] = []
            ok = True
            first_continues = False
            for hi, half in enumerate(halves):
                try:
                    segs, info = label(half, self.llm)
                    with self._lock:
                        self.stats["llm_calls"] += 1
                    merged.extend(segs)
                    if hi == 0:
                        first_continues = bool(info.get("continues_previous"))
                except (ContractError, SegLLMBusy, SegLLMTimeout, SegLLMError):
                    ok = False
                    break
            if ok and merged:
                return WindowResult(merged, "llm", attempts + 2, time.monotonic() - t0, error="split",
                                    continues_previous=first_continues)
        return WindowResult(self.fallback(window), "fallback", attempts, time.monotonic() - t0, error=last_error)

    def run(self, windows: List[Window], label: LabelFn) -> Dict[int, WindowResult]:
        results: Dict[int, WindowResult] = {}
        total = len(windows)
        if self.concurrency == 1 or self.llm is None:
            for w in windows:
                results[w.index] = self._run_one(w, label, total)
            return results
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self._run_one, w, label, total): w.index for w in windows}
            for fut, idx in futures.items():
                results[idx] = fut.result()
        return results
