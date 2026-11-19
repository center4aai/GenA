"""Метод чанкинга llm: модель размечает абзацы (типы блоков и границы), текст не переписывает.

Пайплайн: документ → абзацы (`paragraphs`) → окна (`windows`) → разметка окон
моделью (`contract`, `llm_client`) → дерево (`assemble`) → чанки тем же
упаковщиком правил (`main.create_llm_chunks`), с границами body от модели.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .assemble import LLMBoundarySegmenter, label_window_fallback, segments_to_tree
from .blocks import Paragraph, Segment
from .contract import ContractError, parse_segments_response, render_window_prompt, system_prompt, validate_segments
from .llm_client import SegmentationLLM, WindowRunner, resolve_model
from .paragraphs import document_to_paragraphs
from .windows import Window, build_windows

logger = logging.getLogger(__name__)

__all__ = [
    "segment_document_with_llm", "segment_paragraphs_with_llm", "document_to_paragraphs",
    "build_windows", "label_window", "Paragraph", "Segment", "Window", "ContractError",
]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw and raw.strip() else default
    except ValueError:
        return default


def label_window(window: Window, llm: SegmentationLLM, paragraphs_text: Optional[Dict[int, str]] = None) -> Tuple[List[Segment], Dict[str, Any]]:
    """Один вызов модели на окно → валидированные сегменты + info."""
    raw = llm.complete(system_prompt(), render_window_prompt(window))
    data, truncated = parse_segments_response(raw)
    texts = paragraphs_text or {p.idx: p.text for p in window.paras}
    segments, info = validate_segments(data.get("segments") or [], window.first_idx, window.last_idx, window.index, texts)
    info["truncated"] = truncated
    info["continues_previous"] = bool(data.get("continues_previous"))
    return segments, info


def segment_paragraphs_with_llm(
    paragraphs: List[Paragraph],
    *,
    llm: Optional[SegmentationLLM],
    deadline: Optional[float],
    stats: Dict[str, Any],
    window_chars: Optional[int] = None,
    concurrency: Optional[int] = None,
    retries: int = 3,
    sleep=time.sleep,
) -> List[Segment]:
    """Абзацы → сегменты (с фолбэком на правила по окнам)."""
    window_chars = window_chars or _env_int("CHUNKER_LLM_SEG_WINDOW_CHARS", 12000)
    concurrency = concurrency or _env_int("CHUNKER_LLM_SEG_CONCURRENCY", 2)
    max_windows = _env_int("CHUNKER_LLM_SEG_MAX_WINDOWS", 400)
    windows = build_windows(paragraphs, window_chars=window_chars, context_paras=_env_int("CHUNKER_LLM_SEG_CONTEXT_PARAS", 3))
    if len(windows) > max_windows:
        raise ValueError(f"document too large for LLM segmentation: {len(windows)} windows > {max_windows}")
    texts = {p.idx: p.text for p in paragraphs}
    runner = WindowRunner(llm, concurrency=concurrency, retries=retries, deadline=deadline, fallback=label_window_fallback, sleep=sleep)
    results = runner.run(windows, lambda w, m: label_window(w, m, texts))
    seg_stats = stats.setdefault("llm_segmentation", {})
    seg_stats.update({
        "windows": len(windows),
        "llm_ok": sum(1 for r in results.values() if r.source == "llm"),
        "fallback": sum(1 for r in results.values() if r.source == "fallback"),
        "gapfill_segments": sum(r.gapfill for r in results.values()),
        "salvaged_truncated": sum(1 for r in results.values() if r.truncated),
        "suspicious_windows": [i for i, r in results.items() if r.suspicious],
        "errors": {i: r.error for i, r in results.items() if r.error},
        "paragraphs": len(paragraphs),
        "chars_total": sum(len(p.text) for p in paragraphs),
        **runner.stats,
    })
    segments: List[Segment] = []
    continued = 0
    for idx in sorted(results):
        window_segments = results[idx].segments
        if results[idx].continues_previous and idx > 0:
            # Первый содержательный сегмент окна продолжает предыдущее окно:
            # граница на стыке окон не нужна.
            for seg_ in window_segments:
                if seg_.type in ("body", "list"):
                    seg_.continues = True
                    continued += 1
                break
        segments.extend(window_segments)
    seg_stats["continues_previous"] = continued
    return segments


def segment_document_with_llm(
    path: str,
    *,
    document_name: str,
    kind: str = "textbook",
    min_size: int = 50,
    prose_min_chars: Optional[int] = None,
    deadline: Optional[float] = None,
    stats: Optional[Dict[str, Any]] = None,
    model_id: Optional[str] = None,
    llm: Optional[SegmentationLLM] = None,
    paragraphs: Optional[List[Paragraph]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Документ → чанки методом llm. Возвращает (chunks, заголовки для identify_document_type)."""
    import main as chunker_main  # lazy: main импортирует этот пакет по требованию

    stats = stats if stats is not None else {}
    if llm is None:
        cfg = resolve_model(model_id)
        llm = SegmentationLLM(
            cfg,
            timeout=float(_env_int("CHUNKER_LLM_SEG_WINDOW_TIMEOUT", 240)),
            max_tokens=_env_int("CHUNKER_LLM_SEG_MAX_TOKENS", 3000),
        )
        stats.setdefault("llm_segmentation", {})["model"] = cfg.model_name
    paragraphs = paragraphs if paragraphs is not None else document_to_paragraphs(path, kind, stats)
    if not paragraphs:
        return [], []
    segments = segment_paragraphs_with_llm(paragraphs, llm=llm, deadline=deadline, stats=stats)
    tree, boundaries, topics, titles = segments_to_tree(paragraphs, segments, stats)
    # Сегмент модели — единица упаковки: до CHUNKER_LLM_SEG_MAX_CHARS целиком,
    # длиннее — на примерно равные части (правила 1500/2000 здесь не действуют).
    max_chars = _env_int("CHUNKER_LLM_SEG_MAX_CHARS", 4000)
    chunks = chunker_main.create_llm_chunks(
        tree,
        min_size=min_size,
        document_name=document_name,
        document_kind=kind,
        prose_min_chars=prose_min_chars,
        stats=stats,
        segmenter=LLMBoundarySegmenter(boundaries, topics),
        pack_limits=(max_chars, max_chars),
    )
    return chunks, titles
