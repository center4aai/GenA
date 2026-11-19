"""Метод llm: сегментация абзацев моделью (chunker/llm_segmentation) на фейковой модели."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
if not _CHUNKER.is_dir():
    _CHUNKER = _REPO
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

import docx2json_outline  # noqa: E402,F401  (pin canonical modules first)
import main as chunker_mod  # noqa: E402
import llm_segmentation as seg  # noqa: E402
from llm_segmentation import assemble, contract, llm_client, paragraphs as paras_mod, windows as win_mod  # noqa: E402
from llm_segmentation.blocks import Paragraph, Segment  # noqa: E402

_LINE_RE = re.compile(r'^\[(\d+)(?:\|[^\]]*)?\] (.*)$', re.M)


class FakeLLM:
    """Размечает окно простыми правилами и отвечает JSON, как модель."""

    def __init__(self, wrap=None, fail=None):
        self.calls = 0
        self.wrap = wrap or (lambda s: s)
        self.fail = fail

    def complete(self, system, user):
        self.calls += 1
        if self.fail:
            exc = self.fail(self.calls)
            if exc:
                raise exc
        items = [(int(i), t) for i, t in _LINE_RE.findall(user.split("[АБЗАЦЫ ДЛЯ РАЗМЕТКИ", 1)[-1])]
        segs, body = [], None

        def close():
            nonlocal body
            if body:
                segs.append({"start": body[0], "end": body[1], "type": "body", "title": "Тема"})
                body = None

        for idx, text in items:
            if text.startswith(("§", "Глава")) or (text.isupper() and len(text) < 60):
                close(); segs.append({"start": idx, "end": idx, "type": "heading", "level": 2 if text.startswith("§") else 1 if text.startswith("Глава") else 3, "title": text})
            elif text.startswith(("Выясните", "1. Что", "2. Чем", "Вопросы и задания")):
                close(); segs.append({"start": idx, "end": idx, "type": "exercise"})
            elif text.startswith("Это интересно:"):
                close(); segs.append({"start": idx, "end": idx, "type": "inset", "title": "Это интересно"})
            elif text.startswith("Рис."):
                close(); segs.append({"start": idx, "end": idx, "type": "caption"})
            elif " — " in text and text.split(" — ")[0].istitle() and len(text.split(" — ")[0]) < 40:
                close(); segs.append({"start": idx, "end": idx, "type": "glossary_entry", "title": text.split(" — ")[0]})
            else:
                body = (body[0], idx) if body else (idx, idx)
        close()
        return self.wrap(json.dumps({"continues_previous": False, "segments": segs}, ensure_ascii=False))


SAMPLE_TEXT = """Глава 3. Политика

§ 12. Государство и его признаки

Государство — это политическая организация общества, обладающая суверенитетом и аппаратом управления. Оно возникло вместе с делением общества на классы.

Признаки государства изучаются уже много веков. Учёные выделяют территорию, население и публичную власть.

Это интересно: термин «государство» впервые употребил Никколо Макиавелли в трактате «Государь» в начале XVI века.

Рис. 14. Здание Государственной Думы

Функции государства делятся на внутренние и внешние. К внутренним относят охрану правопорядка и социальную защиту.

Выясните, какие государственные услуги можно получить с 14 лет.

Вопросы и задания

1. Что такое государство?

2. Чем государство отличается от общества?
"""


# ─── paragraphs ───────────────────────────────────────────────────────


def test_text_to_paragraphs_splits_lists_and_joins_page_breaks():
    text = "Абзац первый. Он длинный и заканчивается в 2022 г.\n\nутверждены Основы политики.\n\nК признакам относятся:\n1. территория;\n2. население."
    ps = paras_mod.text_to_paragraphs(text)
    texts = [p.text for p in ps]
    assert texts[0].startswith("Абзац первый.") and "в 2022 г. утверждены Основы" in texts[0]
    assert texts[1] == "К признакам относятся:"
    assert texts[2] == "1. территория;" and texts[3] == "2. население."
    assert [p.idx for p in ps] == [0, 1, 2, 3]


def test_document_to_paragraphs_reads_txt(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text(SAMPLE_TEXT, encoding="utf-8")
    stats = {}
    ps = paras_mod.document_to_paragraphs(str(f), stats=stats)
    assert ps[0].text == "Глава 3. Политика" and stats["llm_segmentation_source"] == "text"
    assert any(p.text.startswith("Выясните") for p in ps)


# ─── windows ──────────────────────────────────────────────────────────


def test_build_windows_covers_all_paragraphs_with_context_and_soft_boundary():
    ps = [Paragraph(i, ("§ 2. Заголовок" if i == 7 else f"Абзац {i}. " + "х" * 200)) for i in range(12)]
    ws = win_mod.build_windows(ps, window_chars=1000, context_paras=2)
    covered = [p.idx for w in ws for p in w.paras]
    assert covered == list(range(12))
    assert all(w.chars <= 1000 or len(w.paras) == 1 for w in ws)
    assert ws[0].context == [] and [p.idx for p in ws[1].context] == [ws[1].first_idx - 2, ws[1].first_idx - 1]
    # Заголовок в хвосте окна открывает следующее окно.
    assert any(w.first_idx == 7 for w in ws)


# ─── contract ─────────────────────────────────────────────────────────


def test_parse_segments_response_variants():
    good = '{"continues_previous": false, "segments": [{"start": 1, "end": 2, "type": "body", "title": "т"}]}'
    assert contract.parse_segments_response(good)[0]["segments"][0]["end"] == 2
    fenced = "```json\n" + good + "\n```"
    assert contract.parse_segments_response(fenced)[1] is False
    think = "<think>размышляю</think>\n" + good
    assert len(contract.parse_segments_response(think)[0]["segments"]) == 1
    missing_comma = '{"continues_previous": false, "segments": [{"start": 1, "end": 1, "type": "body"}\n{"start": 2, "end": 2, "type": "exercise"}]}'
    assert len(contract.parse_segments_response(missing_comma)[0]["segments"]) == 2
    trailing = '{"segments": [{"start": 1, "end": 1, "type": "body"},]}'
    assert len(contract.parse_segments_response(trailing)[0]["segments"]) == 1
    truncated = '{"continues_previous": true, "segments": [{"start": 1, "end": 1, "type": "body"}, {"start": 2, "end": 3, "type": "ins'
    data, trunc = contract.parse_segments_response(truncated)
    assert trunc is True and len(data["segments"]) == 1 and data["continues_previous"] is True
    with pytest.raises(contract.ContractError):
        contract.parse_segments_response("нет json")


def test_validate_segments_fills_gaps_trims_overlaps_and_normalizes():
    texts = {i: f"абзац {i}" for i in range(10, 20)}
    objs = [
        {"start": "12", "end": "11", "type": "Врезка", "title": "Справка"},   # строки, перепутанные границы, синоним
        {"start": 11, "end": 14, "type": "exercise"},                           # пересечение → обрезка
        {"start": 16, "end": 16, "type": "heading", "title": "Заголовок"},
        {"start": 30, "end": 31, "type": "body"},                               # вне диапазона
        {"start": 18, "end": 19, "type": "unknown-type"},
    ]
    segs, info = contract.validate_segments(objs, 10, 19, 0, texts)
    kinds = [(s.start, s.end, s.type, s.source) for s in segs]
    assert kinds == [
        (10, 10, "body", "gapfill"),
        (11, 12, "inset", "llm"),
        (13, 14, "exercise", "llm"),
        (15, 15, "body", "gapfill"),
        (16, 16, "heading", "llm"),
        (17, 17, "body", "gapfill"),
        (18, 19, "body", "llm"),
    ]
    assert info["gapfill"] == 3 and info["suspicious"] is False
    long_heading = [{"start": 10, "end": 12, "type": "heading", "title": "x"}, {"start": 13, "end": 19, "type": "junk"}]
    segs2, info2 = contract.validate_segments(long_heading, 10, 19, 0, texts)
    assert segs2[0].type == "body" and info2["suspicious"] is True
    with pytest.raises(contract.ContractError):
        contract.validate_segments([], 10, 19, 0, texts)


# ─── runner ───────────────────────────────────────────────────────────


def _windows_for(text, window_chars=400):
    ps = paras_mod.text_to_paragraphs(text)
    return ps, win_mod.build_windows(ps, window_chars=window_chars, context_paras=2)


def test_runner_retries_busy_then_succeeds_with_backoff():
    ps, ws = _windows_for(SAMPLE_TEXT)
    llm = FakeLLM(fail=lambda n: llm_client.SegLLMBusy("503") if n == 1 else None)
    delays = []
    runner = llm_client.WindowRunner(llm, concurrency=1, retries=3, fallback=assemble.label_window_fallback, sleep=delays.append)
    texts = {p.idx: p.text for p in ps}
    results = runner.run(ws, lambda w, m: seg.label_window(w, m, texts))
    assert all(r.source == "llm" for r in results.values())
    assert delays == [5.0]
    assert runner.stats["busy_retries"] == 1 and runner.stats["llm_calls"] == len(ws) + 1


def test_runner_contract_error_splits_window_then_falls_back():
    ps, ws = _windows_for(SAMPLE_TEXT, window_chars=100_000)
    assert len(ws) == 1 and len(ws[0].paras) >= 4
    # Первый вызов — мусор; половинки размечаются нормально.
    llm = FakeLLM(wrap=lambda s: s)
    bad_first = FakeLLM()
    calls = {"n": 0}

    def complete(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            return "ответ без json"
        return bad_first.complete(system, user)

    llm.complete = complete
    runner = llm_client.WindowRunner(llm, concurrency=1, retries=0, fallback=assemble.label_window_fallback, sleep=lambda _s: None)
    texts = {p.idx: p.text for p in ps}
    results = runner.run(ws, lambda w, m: seg.label_window(w, m, texts))
    res = results[0]
    assert res.source == "llm" and res.error == "split"
    covered = sorted(i for s in res.segments for i in range(s.start, s.end + 1))
    assert covered == [p.idx for p in ps]

    always_bad = FakeLLM()
    always_bad.complete = lambda system, user: "нет json"
    runner2 = llm_client.WindowRunner(always_bad, concurrency=1, retries=0, fallback=assemble.label_window_fallback, sleep=lambda _s: None)
    res2 = runner2.run(ws, lambda w, m: seg.label_window(w, m, texts))[0]
    assert res2.source == "fallback" and res2.error.startswith("contract")
    assert any(s.type == "heading" for s in res2.segments) and any(s.type == "body" for s in res2.segments)


def test_runner_deadline_and_missing_llm_fall_back():
    ps, ws = _windows_for(SAMPLE_TEXT)
    texts = {p.idx: p.text for p in ps}
    expired = llm_client.WindowRunner(FakeLLM(), concurrency=1, deadline=time.monotonic() - 1, fallback=assemble.label_window_fallback)
    results = expired.run(ws, lambda w, m: seg.label_window(w, m, texts))
    assert all(r.source == "fallback" and r.error == "deadline" for r in results.values())
    none = llm_client.WindowRunner(None, concurrency=2, fallback=assemble.label_window_fallback)
    assert all(r.source == "fallback" for r in none.run(ws, lambda w, m: seg.label_window(w, m, texts)).values())


def test_resolve_model_prefers_model_endpoints(monkeypatch):
    monkeypatch.setenv("MODEL_ENDPOINTS", json.dumps([
        {"id": "a", "base_url": "http://a/v1", "model_name": "m-a", "api_key": "none"},
        {"id": "b", "base_url": "http://b/v1", "model_name": "m-b", "api_key": "k", "extra": {"reasoning_effort": "none"}},
    ]))
    monkeypatch.setenv("CHUNKER_LLM_SEG_MODEL_ID", "b")
    cfg = llm_client.resolve_model()
    assert (cfg.base_url, cfg.model_name, cfg.api_key, cfg.extra) == ("http://b/v1", "m-b", "k", {"reasoning_effort": "none"})
    monkeypatch.setenv("CHUNKER_LLM_SEG_MODEL_ID", "zzz")
    with pytest.raises(ValueError, match="not in MODEL_ENDPOINTS"):
        llm_client.resolve_model()
    monkeypatch.setenv("CHUNKER_LLM_SEG_MODEL_ID", "")
    monkeypatch.setenv("LLM_URL_MODEL", "http://b/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "m-b")
    assert llm_client.resolve_model().extra == {"reasoning_effort": "none"}
    monkeypatch.delenv("MODEL_ENDPOINTS")
    monkeypatch.delenv("LLM_URL_MODEL")
    with pytest.raises(ValueError, match="not configured"):
        llm_client.resolve_model()


def test_resolve_model_api_key_env(monkeypatch):
    monkeypatch.setenv("MODEL_ENDPOINTS", json.dumps([
        {"id": "g", "base_url": "http://g/v1", "model_name": "gemma", "api_key_env": "LITELLM_API_KEY"},
    ]))
    monkeypatch.setenv("CHUNKER_LLM_SEG_MODEL_ID", "g")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-x")
    assert llm_client.resolve_model().api_key == "sk-x"
    monkeypatch.delenv("LITELLM_API_KEY")
    with pytest.raises(ValueError, match="not in MODEL_ENDPOINTS"):
        llm_client.resolve_model()


# ─── assemble + end to end ────────────────────────────────────────────


def test_segments_to_tree_and_chunks_end_to_end(tmp_path):
    f = tmp_path / "book.txt"
    f.write_text(SAMPLE_TEXT, encoding="utf-8")
    stats = {}
    chunks, titles = seg.segment_document_with_llm(
        str(f), document_name="book", kind="textbook", min_size=20, prose_min_chars=0, deadline=None, stats=stats, llm=FakeLLM()
    )
    assert "§ 12. Государство и его признаки" in titles and "Глава 3. Политика" in titles
    kinds = [c["fragment_data"]["block_type"] for c in chunks]
    contents = [c["fragment_data"]["content"] for c in chunks]
    assert "inset" in kinds and "caption" not in kinds  # подпись в одно предложение выброшена правилами
    inset = next(c for c in chunks if c["fragment_data"]["block_type"] == "inset")
    assert "Макиавелли" in inset["fragment_data"]["content"]
    assert inset["fragment_data"]["combined_text"].startswith("Глава 3. Политика > § 12. Государство и его признаки > Это интересно")
    assert not any("Выясните" in t or "Что такое государство" in t for t in contents)
    assert any("Функции государства" in t for t in contents)
    assert stats["llm_segmentation"]["windows"] >= 1 and stats["llm_segmentation"]["fallback"] == 0
    assert stats["dropped_blocks"]["exercise"]["count"] >= 2


def test_process_document_to_chunks_llm_method(tmp_path, monkeypatch):
    f = tmp_path / "book.txt"
    f.write_text(SAMPLE_TEXT, encoding="utf-8")
    monkeypatch.setenv("LLM_URL_MODEL", "http://fake/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "fake")
    monkeypatch.setattr(seg, "resolve_model", lambda model_id=None: llm_client.SegModelConfig("http://fake/v1", "fake"))
    monkeypatch.setattr(seg, "SegmentationLLM", lambda cfg, **kw: FakeLLM())
    from unittest.mock import patch

    stats = {}
    with patch.object(chunker_mod, "identify_document_type", return_value={"document_type": "учебник", "document_name": "Обществознание"}):
        chunks, info = chunker_mod.process_document_to_chunks(
            str(f), min_size=20, document_kind="textbook", stats=stats, chunking_method="llm"
        )
    assert chunks and info["document_name"] == "Обществознание"
    assert stats["chunking_method"] == "llm" and stats["chunking_engine"] == "llm_segmentation"
    assert all(c["fragment_data"]["source"] == "Обществознание" for c in chunks)
    assert "llm_segmentation" in stats and stats["llm_segmentation"]["model"] == "fake"


def test_llm_boundary_segmenter_matches_paragraph_heads():
    s = assemble.LLMBoundarySegmenter({"Функции государства делятся на внутренние и внешние.", ""})
    assert s.boundaries(["Государство — это…", "Функции государства делятся на внутренние и внешние. Ещё текст.", "Другое."]) == {1}


# ─── границы, упаковка и подзаголовки (тикет 71372790) ────────────────


def _para(i, text, page=None, runin=""):
    return Paragraph(i, text, page, None, runin)


def _tree_chunks(paragraphs, segments, stats=None, pack_limits=None):
    stats = {} if stats is None else stats
    tree, boundaries, topics, _titles = assemble.segments_to_tree(paragraphs, segments, stats)
    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=10, document_name="d", document_kind="textbook", prose_min_chars=0, stats=stats,
        segmenter=assemble.LLMBoundarySegmenter(boundaries, topics), pack_limits=pack_limits,
    )
    return tree, boundaries, chunks


P1 = "Первая мысль изложена здесь полностью и заканчивается точкой."
P2 = "Вторая мысль тоже изложена полностью и заканчивается точкой."
P3 = "Третья мысль завершает тему и тоже заканчивается точкой."


def test_continues_previous_suppresses_window_boundary():
    ps = [_para(0, "§ 1. Тема"), _para(1, P1), _para(2, P2), _para(3, P3)]
    head = Segment(0, 0, "heading", "§ 1. Тема", 2, "llm", 0)
    a = Segment(1, 2, "body", "Тема А", None, "llm", 0)
    b_new = Segment(3, 3, "body", "Тема Б", None, "llm", 1)
    _t, boundaries, _c = _tree_chunks(ps, [head, a, b_new])
    assert P3 in boundaries
    b_cont = Segment(3, 3, "body", "Тема А", None, "llm", 1, continues=True)
    _t, boundaries, _c = _tree_chunks(ps, [head, a, b_cont])
    assert not boundaries


def test_body_inset_body_boundary_depends_on_topic_title():
    ps = [_para(0, P1), _para(1, "Это интересно: факт из врезки, достаточно длинный, чтобы остаться врезкой в чанках."), _para(2, P2)]
    body1 = Segment(0, 0, "body", "Признаки государства", None, "llm", 0)
    inset = Segment(1, 1, "inset", "Это интересно", None, "llm", 0)
    same = Segment(2, 2, "body", "признаки государства", None, "llm", 0)
    other = Segment(2, 2, "body", "Функции государства", None, "llm", 0)
    _t, boundaries, chunks = _tree_chunks(ps, [body1, inset, same])
    assert not boundaries
    assert [c["fragment_data"]["block_type"] for c in chunks] == ["body", "inset"]
    assert P1 in chunks[0]["fragment_data"]["content"] and P2 in chunks[0]["fragment_data"]["content"]
    _t, boundaries, chunks = _tree_chunks(ps, [body1, inset, other])
    assert P2 in boundaries and boundaries[P2] == "Функции государства"
    assert [c["fragment_data"]["block_type"] for c in chunks] == ["body", "body", "inset"]
    assert chunks[1]["fragment_data"]["topic_title"] == "Функции государства"
    assert "Функции государства" not in chunks[1]["fragment_data"]["combined_text"]


def test_mid_sentence_never_boundary_and_fallback_adds_none():
    ps = [_para(0, "Абзац обрывается на середине"), _para(1, "предложения и продолжается здесь."), _para(2, P3)]
    a = Segment(0, 0, "body", "А", None, "llm", 0)
    b = Segment(1, 1, "body", "Б", None, "llm", 1)
    c = Segment(2, 2, "body", "В", None, "llm", 1)
    _t, boundaries, _c = _tree_chunks(ps, [a, b, c])
    assert "предложения и продолжается здесь." not in boundaries and P3 in boundaries
    fb = Segment(2, 2, "body", "", None, "fallback", 1)
    gap = Segment(1, 1, "body", "", None, "gapfill", 1)
    _t, boundaries, _c = _tree_chunks(ps, [a, gap, fb])
    assert not boundaries


def test_llm_pack_limits_keep_segment_whole_and_rules_unchanged():
    long_paras = [f"Абзац номер {i} подтемы. " + "Текст предложения. " * 25 for i in range(7)]  # ~500 симв. каждый
    ps = [_para(i, t) for i, t in enumerate(long_paras)]
    seg = Segment(0, 6, "body", "Одна подтема", None, "llm", 0)
    _t, _b, chunks = _tree_chunks(ps, [seg], pack_limits=(4000, 4000))
    assert len(chunks) == 1 and len(chunks[0]["fragment_data"]["content"]) > 3000
    # Без pack_limits действуют правила 1500/2000: тот же текст режется на части ≤ 2000.
    _t, _b, chunks = _tree_chunks(ps, [seg])
    assert len(chunks) >= 2 and all(len(c["fragment_data"]["content"]) <= chunker_mod.TEXTBOOK_MAX_CHARS for c in chunks)
    assert chunker_mod._max_chars() == chunker_mod.TEXTBOOK_MAX_CHARS  # пределы не утекли из вызова
    # Сегмент длиннее max делится на примерно равные части.
    huge = [_para(i, "Предложение подтемы номер один. " * 60) for i in range(5)]  # ~1900 симв. каждый, ~9500 всего
    _t, _b, chunks = _tree_chunks(huge, [Segment(0, 4, "body", "Подтема", None, "llm", 0)], pack_limits=(4000, 4000))
    assert 3 <= len(chunks) <= 4 and all(len(c["fragment_data"]["content"]) <= 4000 for c in chunks)


def test_short_inset_dropped_long_kept(monkeypatch):
    ps = [_para(0, P1), _para(1, "Подпись. Год."), _para(2, "Врезка с фактом: " + "ещё текст факта. " * 8)]
    stats = {}
    _t, _b, chunks = _tree_chunks(
        ps, [Segment(0, 0, "body", "А", None, "llm", 0), Segment(1, 1, "inset", "", None, "llm", 0), Segment(2, 2, "inset", "", None, "llm", 0)], stats
    )
    kinds = [c["fragment_data"]["block_type"] for c in chunks]
    assert kinds == ["body", "inset"]
    assert stats["dropped_blocks"]["inset_short"]["count"] == 1


def test_runin_paragraph_opens_level3_node_regardless_of_model():
    ps = [_para(0, "§ 1. Параграф"), _para(1, P1), _para(2, "4 Восстановление промышленности. Задачи были огромны.", runin="4 Восстановление промышленности"), _para(3, P3)]
    segs = [Segment(0, 0, "heading", "§ 1. Параграф", 2, "llm", 0), Segment(1, 3, "body", "Всё одной подтемой", None, "llm", 0)]
    tree, _b, chunks = _tree_chunks(ps, segs)
    section = tree["children"][0]
    assert section["content"] == P1
    assert [n["title"] for n in section["children"]] == ["4 Восстановление промышленности"]
    assert section["children"][0]["content"].startswith("4 Восстановление промышленности. Задачи")
    assert any(c["fragment_data"]["title"] == "4 Восстановление промышленности" for c in chunks)


def test_window_prompt_marks_page_changes():
    ps = [_para(0, "Первый абзац.", page=7), _para(1, "Второй абзац.", page=7), _para(2, "Третий абзац.", page=8)]
    w = win_mod.Window(0, [], ps)
    rendered = contract.render_window_prompt(w)
    assert "[2|стр. 8] Третий абзац." in rendered and "[1] Второй абзац." in rendered


MEDINSKY_SAMPLE = _REPO / "tests" / "data" / "medinskiy_layout_sample.pdf"


class HintLLM(FakeLLM):
    """Размечает по подсказкам вёрстки (как это делал бы аккуратный разметчик)."""

    _HINT_RE = re.compile(r'^\[(\d+)(?:\|([^\]]*))?\] (.*)$', re.M)

    def complete(self, system, user):
        self.calls += 1
        segs, body = [], None

        def close():
            nonlocal body
            if body:
                segs.append({"start": body[0], "end": body[1], "type": "body", "title": "Послевоенная Европа"})
                body = None

        for idx, hint, text in self._HINT_RE.findall(user.split("[АБЗАЦЫ ДЛЯ РАЗМЕТКИ", 1)[-1]):
            idx = int(idx)
            hint = hint or ""
            if "заголовок" in hint and "подзаголовка" not in hint:
                close(); segs.append({"start": idx, "end": idx, "type": "heading", "level": 2, "title": text[:80]})
            elif "задание" in hint:
                close(); segs.append({"start": idx, "end": idx, "type": "exercise"})
            elif "заливка" in hint or "мелкий" in hint:
                close(); segs.append({"start": idx, "end": idx, "type": "inset", "title": "Врезка"})
            elif "подпись" in hint:
                close(); segs.append({"start": idx, "end": idx, "type": "caption"})
            else:
                body = (body[0], idx) if body else (idx, idx)
        close()
        return json.dumps({"continues_previous": False, "segments": segs}, ensure_ascii=False)


@pytest.mark.skipif(not MEDINSKY_SAMPLE.is_file(), reason="fixture missing")
def test_llm_on_medinsky_sample_with_layout_hints():
    pytest.importorskip("pdfplumber")
    stats = {}
    chunks, _titles = seg.segment_document_with_llm(
        str(MEDINSKY_SAMPLE), document_name="med", kind="textbook", min_size=30, prose_min_chars=0,
        deadline=None, stats=stats, llm=HintLLM(),
    )
    assert stats["llm_segmentation_source"] == "layout"
    kinds = [c["fragment_data"]["block_type"] for c in chunks]
    contents = [c["fragment_data"]["content"] for c in chunks]
    assert "inset" in kinds
    assert any("Иосип Броз Тито" in t for t, k in zip(contents, kinds) if k == "inset")
    assert not any("Иосип Броз Тито" in t for t, k in zip(contents, kinds) if k == "body")
    assert not any("⬤" in t or "Узнайте" in t for t in contents)
    bodies = [c for c in chunks if c["fragment_data"]["block_type"] == "body"]
    assert bodies and all(c["fragment_data"]["page_start"] for c in bodies)
    # Единый сегмент модели не порезан правилами 1500/2000: тело образца — один чанк.
    assert len(bodies) == 1 and len(bodies[0]["fragment_data"]["content"]) > 2000
    assert bodies[0]["fragment_data"]["topic_title"] == "Послевоенная Европа"
