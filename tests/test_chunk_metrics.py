"""Метрики качества чанков учебника (scripts/chunk_metrics.py) на мини-JSON."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "chunk_metrics.py"
_CHUNKER = _REPO / "chunker"
if _CHUNKER.is_dir() and str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

import docx2json_outline  # noqa: E402,F401  (pin canonical modules first)
import main as _chunker_main  # noqa: E402,F401
import pytest  # noqa: E402

if not _SCRIPT.is_file():
    pytest.skip("scripts/chunk_metrics.py missing", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("chunk_metrics_under_test", _SCRIPT)
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)


def _chunk(content, block_type, title, path, where="fragment_data"):
    fd = {"content": content, "block_type": block_type, "title": title, "page_start": 1, "page_end": 1}
    hc = {"breadcrumb_path": [{"level": i + 1, "title": t, "position": i} for i, t in enumerate(path)]}
    chunk = {"fragment_data": fd}
    if where == "fragment_data":
        fd["hierarchy_context"] = hc
    else:
        chunk["hierarchy_context"] = hc
    return chunk


def test_inset_title_and_chapter_in_path_metrics():
    data = {
        "filename": "book.pdf",
        "chunks_detailed": [
            _chunk("• 1946 г. — преобразование наркоматов в министерства.", "inset", "РОССИЯ МИР",
                   ["Глава I СССР В 1945—1991 гг.", "§ 1 Восстановление"]),
            # Текст врезки стал разделом: заголовок врезки в пути body (регистр/ё не важны).
            _chunk("Началась работа по восстановлению промышленности.", "body", "1 Влияние Победы",
                   ["Глава I СССР В 1945—1991 гг.", "§ 1 Восстановление", "Россия  мир"], where="top"),
            _chunk("Обычный абзац без проблем.", "body", "2 Потери", ["§ 1 Восстановление"]),
        ],
        "dropped_blocks": {"apparatus": {"count": 3, "chars": 400}, "inset_mixed": 2, "exercise": {"count": 1, "chars": 5}},
    }
    m = cm.compute_metrics(data)
    assert m["body_inset_title_in_path"] == 1
    assert m["chunks_chapter_in_path"] == 2
    assert m["dropped_apparatus"] == 3
    assert m["dropped_inset_mixed"] == 2
    assert m["_examples"]["inset_title_in_path"] == ["Глава I СССР В 1945—1991 гг. > § 1 Восстановление > Россия  мир"]
    assert len(m["_examples"]["chapter_in_path"]) == 2


def test_metrics_without_new_signals_are_zero():
    data = {"chunks_detailed": [_chunk("Текст.", "body", "§ 1", ["§ 1 Раздел"])]}
    m = cm.compute_metrics(data)
    assert m["body_inset_title_in_path"] == 0 and m["chunks_chapter_in_path"] == 0
    assert m["dropped_apparatus"] == 0 and m["dropped_inset_mixed"] == 0


def test_exercise_leaks_count_numbered_question_in_inset():
    data = {"chunks_detailed": [
        _chunk("Текст врезки кончается.\n4. Какие последствия имела реформа?", "inset", "Врезка", []),
        _chunk("• 1946 г. — выборы в Верховный Совет", "inset", "РОССИЯ МИР", []),
        _chunk("Причины:\n1. Рост цен.\n2. Нехватка товаров.", "body", "§ 1", []),
        _chunk("Подпись. 4. Какие последствия?", "caption", "Подпись к иллюстрации", []),
    ]}
    m = cm.compute_metrics(data)
    assert m["exercise_leaks"] == 1
    assert m["_examples"]["exercise_leaks"] == ["Текст врезки кончается.\n4. Какие последствия имела реформа?"]
