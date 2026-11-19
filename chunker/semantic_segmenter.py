"""Метод чанкинга semantic: границы чанков по эмбеддингам поверх правил.

Чанкер по правилам (`main._split_prose_units`) даёт абзацы узла; сегментер
получает их списком и возвращает индексы абзацев, с которых обязан начинаться
новый чанк — там, где косинусная близость соседних абзацев проваливается.
Упаковщик не рвёт списки и словарь, но не склеивает абзацы через такую границу.

Эмбеддинги — любой OpenAI-совместимый эндпоинт `/v1/embeddings`
(llama.cpp с embedding-моделью, Ollama, vLLM): `CHUNKER_EMBEDDING_URL`,
`CHUNKER_EMBEDDING_MODEL`, `CHUNKER_EMBEDDING_API_KEY`.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

EmbedFn = Callable[[Sequence[str]], List[List[float]]]

# Абзац длиннее этого режем перед эмбеддингом: смысл границы задаёт начало.
EMBED_MAX_CHARS = 2000
EMBED_BATCH = 64


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, using default %s", name, raw, default)
        return default


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticSegmenter:
    """Границы чанков по провалам близости соседних абзацев.

    threshold — абсолютный порог близости: ниже него всегда граница.
    percentile — при n ≥ min_paragraphs_for_percentile абзацах границей
    считается ещё и близость из нижних `percentile` процентов (адаптивно
    к документу: у плотного текста близости в целом выше).
    """

    def __init__(
        self,
        embed_fn: EmbedFn,
        threshold: float = 0.5,
        percentile: float = 20.0,
        min_paragraphs_for_percentile: int = 8,
        stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.embed_fn = embed_fn
        self.threshold = threshold
        self.percentile = percentile
        self.min_paragraphs_for_percentile = min_paragraphs_for_percentile
        self.stats = stats if stats is not None else {}
        self.stats.setdefault("semantic", {"calls": 0, "paragraphs": 0, "boundaries": 0, "nodes": 0})

    # ------------------------------------------------------------------ config
    @classmethod
    def from_env(cls, stats: Optional[Dict[str, Any]] = None) -> "SemanticSegmenter":
        url = os.getenv("CHUNKER_EMBEDDING_URL", "").strip()
        if not url:
            raise ValueError("chunking method 'semantic' is not configured: CHUNKER_EMBEDDING_URL is not set")
        model = os.getenv("CHUNKER_EMBEDDING_MODEL", "").strip() or "text-embedding"
        api_key = os.getenv("CHUNKER_EMBEDDING_API_KEY", "").strip() or "none"
        timeout = _env_float("CHUNKER_EMBEDDING_TIMEOUT", 60.0)
        embed_fn = make_openai_embed_fn(url, model, api_key, timeout)
        return cls(
            embed_fn,
            threshold=_env_float("CHUNKER_SEMANTIC_THRESHOLD", 0.5),
            percentile=_env_float("CHUNKER_SEMANTIC_PERCENTILE", 20.0),
            stats=stats,
        )

    # ------------------------------------------------------------------ core
    def _embed(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            batch = [t[:EMBED_MAX_CHARS] for t in texts[i:i + EMBED_BATCH]]
            got = self.embed_fn(batch)
            if len(got) != len(batch):
                raise ValueError(f"embedding endpoint returned {len(got)} vectors for {len(batch)} texts")
            vectors.extend(got)
            self.stats["semantic"]["calls"] += 1
        return vectors

    def boundaries(self, paragraphs: Sequence[str]) -> Set[int]:
        """Индексы абзацев, перед которыми должна быть граница чанка."""
        n = len(paragraphs)
        self.stats["semantic"]["nodes"] += 1
        self.stats["semantic"]["paragraphs"] += n
        if n < 2:
            return set()
        vectors = self._embed(paragraphs)
        sims = [cosine(vectors[i - 1], vectors[i]) for i in range(1, n)]
        pct_cut: Optional[float] = None
        if n >= self.min_paragraphs_for_percentile and self.percentile > 0:
            ordered = sorted(sims)
            k = max(0, min(len(ordered) - 1, int(round(len(ordered) * self.percentile / 100.0)) - 1))
            pct_cut = ordered[k]
        result = {
            i + 1 for i, s in enumerate(sims)
            if s < self.threshold or (pct_cut is not None and s <= pct_cut)
        }
        self.stats["semantic"]["boundaries"] += len(result)
        return result


def make_openai_embed_fn(base_url: str, model: str, api_key: str = "none", timeout: float = 60.0) -> EmbedFn:
    """embed_fn поверх OpenAI-совместимого /v1/embeddings (клиент `openai`)."""
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)

    def _embed(texts: Sequence[str]) -> List[List[float]]:
        resp = client.embeddings.create(model=model, input=list(texts))
        data = sorted(resp.data, key=lambda d: getattr(d, "index", 0))
        return [list(d.embedding) for d in data]

    return _embed
