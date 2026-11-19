"""Метод semantic: границы чанков по эмбеддингам (chunker/semantic_segmenter.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
if not _CHUNKER.is_dir():
    _CHUNKER = _REPO
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

import docx2json_outline  # noqa: E402,F401  (pin canonical modules, see test_text_utils)
import main as chunker_mod  # noqa: E402,F401
from semantic_segmenter import SemanticSegmenter, cosine, EMBED_BATCH  # noqa: E402


def _topic_embed(texts):
    """Two topics: paragraphs mentioning «экономика» point one way, «право» the other."""
    out = []
    for t in texts:
        t = t.lower()
        if "экономик" in t:
            out.append([1.0, 0.1])
        elif "прав" in t:
            out.append([0.1, 1.0])
        else:
            out.append([0.7, 0.7])
    return out


def test_boundary_where_similarity_drops():
    paras = [
        "Экономика изучает хозяйство. " * 3,
        "Рыночная экономика основана на обмене. " * 3,
        "Право регулирует отношения людей. " * 3,
        "Нормы права закреплены в законах. " * 3,
    ]
    seg = SemanticSegmenter(_topic_embed, threshold=0.5, percentile=0)
    assert seg.boundaries(paras) == {2}
    assert seg.stats["semantic"]["boundaries"] == 1
    assert seg.stats["semantic"]["calls"] == 1


def test_no_boundary_for_single_or_uniform_paragraphs():
    seg = SemanticSegmenter(_topic_embed, threshold=0.5, percentile=0)
    assert seg.boundaries(["Экономика."]) == set()
    assert seg.boundaries(["Экономика A.", "Экономика B.", "Экономика C."]) == set()


def test_percentile_adds_adaptive_boundaries():
    # Ten paragraphs with slowly drifting vectors: no absolute drop below 0.5,
    # but the lowest 20 % of similarities become boundaries.
    vectors = [[1.0, 0.0 + 0.08 * i] for i in range(10)]
    vectors[5] = [0.2, 1.0]  # the one real topic shift
    seg = SemanticSegmenter(lambda ts: [vectors[int(t)] for t in ts], threshold=0.0, percentile=20)
    bounds = seg.boundaries([str(i) for i in range(10)])
    assert 5 in bounds and 6 in bounds  # entering and leaving the odd paragraph
    assert len(bounds) <= 3


def test_embedding_batches_and_length_mismatch():
    calls = []

    def embed(texts):
        calls.append(len(texts))
        return [[1.0, 0.0] for _ in texts]

    seg = SemanticSegmenter(embed, threshold=0.5, percentile=0)
    seg.boundaries([f"абзац {i}" for i in range(EMBED_BATCH + 3)])
    assert calls == [EMBED_BATCH, 3]

    seg_bad = SemanticSegmenter(lambda ts: [[1.0, 0.0]], threshold=0.5, percentile=0)
    with pytest.raises(ValueError, match="returned 1 vectors"):
        seg_bad.boundaries(["a", "b", "c"])


def test_from_env_requires_url(monkeypatch):
    monkeypatch.delenv("CHUNKER_EMBEDDING_URL", raising=False)
    with pytest.raises(ValueError, match="CHUNKER_EMBEDDING_URL"):
        SemanticSegmenter.from_env()
    monkeypatch.setenv("CHUNKER_EMBEDDING_URL", "http://embed:8000/v1")
    monkeypatch.setenv("CHUNKER_SEMANTIC_THRESHOLD", "0.42")
    seg = SemanticSegmenter.from_env()
    assert seg.threshold == pytest.approx(0.42)


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 1]) == 0.0
