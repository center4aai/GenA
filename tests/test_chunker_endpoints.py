"""Unit tests for chunker/main.py endpoints."""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
if not _CHUNKER.is_dir():
    # Inside chunker image: code at /app, tests at /app/tests
    _CHUNKER = _REPO
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

import main as chunker_mod  # noqa: E402


@pytest.fixture
def client():
    return TestClient(chunker_mod.app)


SAMPLE_CHUNK = {
    "fragment_id": "doc_chunk_1",
    "hierarchy_context": {"breadcrumb_text": "Title"},
    "fragment_data": {
        "title": "Article 1",
        "content": "1. Legal provision text long enough to pass min_size checks easily.",
        "combined_text": "Title > Article 1\n\n1. Legal provision text long enough to pass min_size checks easily.",
        "text_length": 80,
    },
}

DOC_TYPE_INFO = {
    "document_type": "Кодекс",
    "document_name": "Test Code",
    "confidence": 0.9,
    "description": "Test document",
    "titles_count": 1,
    "key_indicators": [],
}


# ─── GET / and /health ────────────────────────────────────────────────


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "GenA Chunker Service"
    assert data["version"] == "2.0.0"
    assert "/chunk/" in data["endpoints"]


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "chunker"


# ─── POST /chunk/ ─────────────────────────────────────────────────────


def test_chunk_file_success(client):
    content = b"Sample document content for chunking test."
    with patch.object(
        chunker_mod, "process_document_to_chunks", return_value=([SAMPLE_CHUNK], DOC_TYPE_INFO)
    ) as proc:
        resp = client.post(
            "/chunk/",
            files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "test.txt"
    assert data["file_type"] == "txt"
    assert data["num_chunks"] == 1
    assert len(data["chunks"]) == 1
    assert data["document_type"]["document_type"] == "Кодекс"
    assert data["document_kind"] == "normative"
    assert proc.call_args.kwargs.get("document_kind") == "normative"


def test_chunk_file_textbook_kind_forwarded(client):
    content = b"Sample document content for chunking test."
    with patch.object(
        chunker_mod, "process_document_to_chunks", return_value=([SAMPLE_CHUNK], DOC_TYPE_INFO)
    ) as proc:
        resp = client.post(
            "/chunk/?document_kind=textbook",
            files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        )
    assert resp.status_code == 200
    assert resp.json()["document_kind"] == "textbook"
    assert proc.call_args.kwargs.get("document_kind") == "textbook"


def test_chunk_file_encyclopedia_kind_forwarded(client):
    content = b"Sample encyclopaedia content for chunking test."
    with patch.object(
        chunker_mod, "process_document_to_chunks", return_value=([SAMPLE_CHUNK], DOC_TYPE_INFO)
    ) as proc:
        resp = client.post(
            "/chunk/?document_kind=encyclopedia",
            files={"file": ("bre.pdf", io.BytesIO(content), "application/pdf")},
        )
    assert resp.status_code == 200
    assert resp.json()["document_kind"] == "encyclopedia"
    assert proc.call_args.kwargs.get("document_kind") == "encyclopedia"


PROSE_PARAGRAPH = (
    "Экономическое развитие страны в начале XXI века опиралось на несколько "
    "крупных инфраструктурных проектов, которые изменили транспортную карту "
    "регионов и связали удалённые территории с промышленными центрами. "
    "Одновременно менялась структура занятости: доля сферы услуг росла, а "
    "промышленность концентрировалась вокруг крупных агломераций."
)


def test_process_document_to_chunks_forwards_document_kind_to_outline():
    tree = {
        "level": 0,
        "title": "",
        "content": "",
        "children": [
            {
                "level": 1,
                "title": "§ 1. Тема",
                "content": PROSE_PARAGRAPH,
                "children": [],
            }
        ],
    }
    with patch.object(chunker_mod, "extract_outline_from_document", return_value=tree) as ext:
        with patch.object(
            chunker_mod,
            "identify_document_type",
            return_value=DOC_TYPE_INFO,
        ):
            chunks, info = chunker_mod.process_document_to_chunks(
                __file__, min_size=20, document_kind="textbook"
            )
    assert ext.call_args.kwargs.get("document_kind") == "textbook" or (
        len(ext.call_args.args) >= 2 and ext.call_args.args[1] == "textbook"
    )
    assert info["document_type"] == "Кодекс"
    assert chunks
    assert all("§ 1" in c["fragment_data"]["combined_text"] for c in chunks)


def test_create_llm_chunks_textbook_prefixes_breadcrumb_and_title():
    """Учебник: гейт и генератор видят только текст чанка, значит заголовок нужен в нём."""
    tree = {
        "title": "Глава 1",
        "level": 1,
        "content": "",
        "children": [
            {
                "title": "Параграф 1",
                "level": 2,
                "content": PROSE_PARAGRAPH,
                "children": [],
            }
        ],
    }
    textbook = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="book", document_kind="textbook"
    )
    assert textbook
    for chunk in textbook:
        combined = chunk["fragment_data"]["combined_text"]
        assert combined.startswith("Глава 1 > Параграф 1\n\n")
        assert chunk["fragment_data"]["content"] in combined
        assert chunk["fragment_data"]["title"] == "Параграф 1"
        assert chunk["hierarchy_context"]["breadcrumb_text"] == "Глава 1"


def test_create_llm_chunks_prose_drops_layout_debris_keeps_short_prose():
    """Подпись к таблице выбрасывается, короткое законченное предложение — нет."""
    short_prose = "Крымский мост сдан в эксплуатацию в 2018 году и связал полуостров с материком."
    tree = {
        "title": "Глава 22",
        "level": 1,
        "content": "",
        "children": [
            {"title": "§ 116", "level": 2, "content": PROSE_PARAGRAPH, "children": []},
            {
                "title": "Таблица",
                "level": 2,
                "content": "ВВП России в 2000–2022 гг. (млрд руб. в текущих ценах, по данным Росстата)",
                "children": [],
            },
            {"title": "§ 117", "level": 2, "content": short_prose, "children": []},
        ],
    }
    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="book", document_kind="textbook"
    )
    combined = [c["fragment_data"]["combined_text"] for c in chunks]
    assert len(chunks) == 2
    assert not any("ВВП России" in c for c in combined)
    assert any(short_prose in c for c in combined)


def test_merge_short_prose_parts_glues_tail_into_neighbour():
    parts = ["А" * 1800 + ".", "Б" * 200 + ".", "В" * 120 + "."]
    merged = chunker_mod._merge_short_prose_parts(parts, 400)
    assert len(merged) == 2
    assert merged[1].startswith("Б")
    assert merged[1].endswith("В" * 120 + ".")
    assert chunker_mod._merge_short_prose_parts(parts, 0) == parts


def test_merge_short_prose_parts_respects_max_chars():
    parts = ["А" * (chunker_mod.TEXTBOOK_MAX_CHARS - 100) + ".", "Б" * 200 + "."]
    merged = chunker_mod._merge_short_prose_parts(parts, 400)
    assert merged == parts


def test_create_llm_chunks_prose_reflows_page_break_hyphenation():
    """Слово, разорванное переносом и разрывом страницы, склеивается обратно."""
    tree = {
        "title": "§ 5",
        "level": 1,
        "content": (
            "Западные страны стремились лишить Россию суверенитета и заставить при-\n\n"
            "знать себя полуколонией. " + PROSE_PARAGRAPH
        ),
        "children": [],
    }
    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="book", document_kind="textbook"
    )
    assert chunks
    combined = "\n".join(c["fragment_data"]["combined_text"] for c in chunks)
    assert "признать себя полуколонией" in combined
    assert "при-" not in combined


def test_create_llm_chunks_textbook_splits_long_paragraphs():
    long_para = ("Это предложение учебника. " * 80).strip()
    tree = {
        "title": "Раздел",
        "level": 1,
        "content": long_para,
        "children": [],
    }
    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="book", document_kind="textbook"
    )
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk["fragment_data"]["combined_text"]) <= chunker_mod.TEXTBOOK_MAX_CHARS + 50


def test_normative_rescues_prose_gaps_between_numbered_items():
    """Textbook-like prose before numbered end-of-chapter questions must not be dropped."""
    prose = (
        "В начале XXI века мировая политика претерпела существенные изменения. "
        "Государства столкнулись с новыми вызовами в области безопасности и экономики. "
        "Федеративные отношения в России также претерпели трансформацию. "
        "Выборы и партийная система стали важной частью политической жизни."
    )
    assert len(prose) >= chunker_mod.NORMATIVE_GAP_MIN_CHARS
    content = (
        f"{prose}\n\n"
        "1. Каковы основные тенденции мировой политики в XXI веке?\n"
        "2. Как изменились федеративные отношения в России?"
    )
    tree = {
        "title": "Глава",
        "level": 1,
        "content": content,
        "children": [],
    }
    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="chapter", document_kind="normative"
    )
    texts = [c["fragment_data"]["content"] for c in chunks]
    joined = "\n".join(texts)
    assert any("мировая политика" in t for t in texts), texts
    assert any(t.startswith("1.") for t in texts), texts
    assert any(t.startswith("2.") for t in texts), texts
    assert "мировая политика" in joined
    assert len(chunks) >= 3


def test_normative_short_gaps_between_items_are_ignored():
    """Real normative docs: short gaps (newlines/headers) must not spawn extra chunks."""
    content = (
        "1. Первый пункт нормативного документа с достаточным текстом для чанка.\n\n"
        "2. Второй пункт нормативного документа тоже достаточно длинный для чанка."
    )
    tree = {
        "title": "Статья 1",
        "level": 1,
        "content": content,
        "children": [],
    }
    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="law", document_kind="normative"
    )
    texts = [c["fragment_data"]["content"] for c in chunks]
    assert len(chunks) == 2
    assert texts[0].startswith("1.")
    assert texts[1].startswith("2.")


def test_chunk_file_no_filename(client):
    """FastAPI rejects empty multipart filename before the handler runs."""
    resp = client.post(
        "/chunk/",
        files={"file": ("", io.BytesIO(b"data"), "text/plain")},
    )
    assert resp.status_code == 422


def test_chunk_file_empty(client):
    resp = client.post(
        "/chunk/",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Empty file"


def test_chunk_file_oversized(client):
    with patch.object(chunker_mod, "MAX_UPLOAD_BYTES", 100):
        content = b"x" * 200
        resp = client.post(
            "/chunk/",
            files={"file": ("big.txt", io.BytesIO(content), "text/plain")},
        )
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"].lower()


def test_chunk_file_no_chunks_produced(client):
    empty_chunk = {
        "fragment_data": {"combined_text": "   "},
    }
    with patch.object(
        chunker_mod, "process_document_to_chunks", return_value=([empty_chunk], DOC_TYPE_INFO)
    ):
        resp = client.post(
            "/chunk/",
            files={"file": ("sparse.txt", io.BytesIO(b"text"), "text/plain")},
        )
    assert resp.status_code == 422
    assert "No chunks produced" in resp.json()["detail"]


def test_chunk_file_processing_error(client):
    with patch.object(
        chunker_mod,
        "process_document_to_chunks",
        side_effect=ValueError("Unsupported document format"),
    ):
        resp = client.post(
            "/chunk/",
            files={"file": ("bad.xyz", io.BytesIO(b"data"), "application/octet-stream")},
        )
    assert resp.status_code == 400
    assert "Unsupported document format" in resp.json()["detail"]


def test_chunk_file_internal_error(client):
    with patch.object(
        chunker_mod,
        "process_document_to_chunks",
        side_effect=RuntimeError("unexpected"),
    ):
        resp = client.post(
            "/chunk/",
            files={"file": ("fail.txt", io.BytesIO(b"data"), "text/plain")},
        )
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Internal server error"


def test_chunk_file_min_size_param(client):
    with patch.object(
        chunker_mod, "process_document_to_chunks", return_value=([SAMPLE_CHUNK], DOC_TYPE_INFO)
    ) as mock_proc:
        resp = client.post(
            "/chunk/?min_size=100",
            files={"file": ("test.txt", io.BytesIO(b"content"), "text/plain")},
        )
    assert resp.status_code == 200
    mock_proc.assert_called_once()
    assert mock_proc.call_args.kwargs.get("min_size") == 100 or mock_proc.call_args[1].get("min_size") == 100


# ─── POST /chunk-docx/ ──────────────────────────────────────────────


def test_chunk_docx_rejects_non_docx(client):
    resp = client.post(
        "/chunk-docx/",
        files={"file": ("test.pdf", io.BytesIO(b"pdf"), "application/pdf")},
    )
    assert resp.status_code == 400
    assert ".docx" in resp.json()["detail"]


def test_chunk_docx_success(client):
    with patch.object(
        chunker_mod, "process_document_to_chunks", return_value=([SAMPLE_CHUNK], DOC_TYPE_INFO)
    ):
        resp = client.post(
            "/chunk-docx/",
            files={
                "file": (
                    "report.docx",
                    io.BytesIO(b"PK fake docx content"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    assert resp.status_code == 200
    assert resp.json()["file_type"] == "docx"


# ─── Pure helpers (identify_document_type LLM mocked) ─────────────────


def test_identify_document_type_no_llm_configured():
    with patch.dict("os.environ", {}, clear=True):
        result = chunker_mod.identify_document_type(["Title 1", "Title 2"])
    assert result["document_type"] == "unknown"
    assert result["confidence"] == 0.0
    assert result["titles_count"] == 2


def test_identify_document_type_llm_mocked():
    mock_result = {
        "document_type": "Кодекс",
        "document_name": "Гражданский кодекс",
        "confidence": 0.95,
        "description": "Кодекс РФ",
        "key_indicators": ["кодекс"],
    }
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = mock_result

    env = {
        "LLM_PROVIDER": "openai",
        "LLM_MODEL_NAME": "test-model",
        "LLM_URL_MODEL": "http://localhost:8000/v1",
        "LLM_API_KEY": "test-key",
    }
    with patch.dict("os.environ", env, clear=False):
        with patch("langchain_core.prompts.ChatPromptTemplate") as mock_prompt_cls:
            mock_prompt = MagicMock()
            mock_prompt_cls.from_messages.return_value = mock_prompt
            mock_intermediate = MagicMock()
            mock_prompt.__or__ = MagicMock(return_value=mock_intermediate)
            mock_intermediate.__or__ = MagicMock(return_value=mock_chain)
            with patch("langchain_openai.ChatOpenAI"):
                with patch("langchain_core.output_parsers.JsonOutputParser"):
                    result = chunker_mod.identify_document_type(["Глава 1"])
    assert result["document_type"] == "Кодекс"
    assert result["titles_count"] == 1


# ─── Direct async endpoint call ───────────────────────────────────────


def test_chunk_file_direct_no_filename():
    upload = MagicMock()
    upload.filename = ""
    upload.read = MagicMock(return_value=b"data")

    async def run():
        return await chunker_mod.chunk_file(upload)

    result = asyncio.run(run())
    assert result.status_code == 400
    assert "Filename" in result.body.decode()


# ─── textbook: lists, glossary, exercises, apparatus, stats ───────────


def _textbook_chunks(tree, **kwargs):
    stats = {}
    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="book", document_kind="textbook", stats=stats, **kwargs
    )
    return chunks, stats


def test_textbook_list_stays_with_intro_and_is_not_split():
    intro = "К внутренним функциям государства относятся следующие направления деятельности:"
    items = [f"{i}. " + f"Направление номер {i} описывается достаточно подробно, " * 6 for i in range(1, 8)]
    content = "\n\n".join([PROSE_PARAGRAPH, intro] + items + [PROSE_PARAGRAPH])
    tree = {"title": "§ 6", "level": 1, "content": content, "children": []}
    chunks, _ = _textbook_chunks(tree)
    list_chunks = [c for c in chunks if c["fragment_data"]["block_type"] == "list"]
    assert len(list_chunks) == 1, [c["fragment_data"]["block_type"] for c in chunks]
    text = list_chunks[0]["fragment_data"]["content"]
    assert text.startswith(intro)
    assert "7. Направление номер 7" in text
    assert len(text) > chunker_mod.TEXTBOOK_MAX_CHARS  # список длиннее потолка, но цел
    assert len(text) <= chunker_mod.TEXTBOOK_LIST_HARD_CAP


def test_textbook_exercise_paragraphs_are_dropped_and_counted():
    task = "Выясните, какие государственные услуги вы можете получить с 14 лет. Какими услугами вы уже пользовались?"
    content = f"{PROSE_PARAGRAPH}\n\n{task}\n\n⬤ Узнайте, что такое движение неприсоединения.\n\n{PROSE_PARAGRAPH}"
    tree = {"title": "§ 9", "level": 1, "content": content, "children": []}
    chunks, stats = _textbook_chunks(tree)
    joined = "\n".join(c["fragment_data"]["content"] for c in chunks)
    assert "Выясните" not in joined
    assert "Узнайте" not in joined
    assert PROSE_PARAGRAPH[:40] in joined
    assert stats["dropped_blocks"]["exercise"]["count"] == 2
    assert stats["dropped_blocks"]["exercise"]["chars"] > 0


def test_textbook_apparatus_sections_are_dropped_by_heading():
    tree = {
        "title": "Глава 1",
        "level": 1,
        "content": "",
        "children": [
            {"title": "§ 1. Общество", "level": 2, "content": PROSE_PARAGRAPH, "children": []},
            {
                "title": "ВОПРОСЫ И ЗАДАНИЯ",
                "level": 3,
                "content": "1. Что такое общество?\n2. Назовите признаки государства.",
                "children": [],
            },
            {"title": "Содержание", "level": 3, "content": "§ 1. Общество . . . . 5\n§ 2. Государство . . . . 9\n§ 3. Право . . . . 14", "children": []},
            {"title": "Введение", "level": 2, "content": "Дорогие ребята! Этот учебник поможет вам разобраться в устройстве общества. " * 5, "children": []},
        ],
    }
    original_children = len(tree["children"])
    chunks, stats = _textbook_chunks(tree)
    titles = {c["fragment_data"]["title"] for c in chunks}
    assert titles == {"§ 1. Общество"}
    assert stats["dropped_blocks"]["exercise"]["count"] == 1
    assert stats["dropped_blocks"]["front_matter"]["count"] == 2
    # Дерево вызывающего не изменено.
    assert len(tree["children"]) == original_children


def test_textbook_glossary_one_entry_per_chunk():
    entries = [
        "Государство — политическая организация общества, обладающая суверенитетом и аппаратом власти.",
        "Гражданство — устойчивая правовая связь человека с государством, выражающаяся во взаимных правах.",
        "Суверенитет — верховенство и независимость государственной власти внутри страны и вовне.",
    ]
    tree = {
        "title": "Словарь терминов",
        "level": 1,
        "content": "\n\n".join(entries + ["Термин."]),
        "children": [],
    }
    chunks, stats = _textbook_chunks(tree)
    assert [c["fragment_data"]["block_type"] for c in chunks] == ["glossary_entry"] * 3
    assert [c["fragment_data"]["content"] for c in chunks] == entries
    assert all(c["fragment_data"]["combined_text"].startswith("Словарь терминов\n\n") for c in chunks)
    assert stats["chunks_by_block_type"]["glossary_entry"]["count"] == 3


def test_textbook_inset_node_becomes_single_chunk():
    inset = "Первые пользователи увидели портал «Госуслуги» в 2009 г. Сайт носил информационный характер. В 2010 г. появились личный кабинет и первые 20 услуг."
    tree = {
        "title": "§ 9. Цифровое государство",
        "level": 1,
        "content": PROSE_PARAGRAPH,
        "children": [
            {"title": "Интересно", "level": 2, "content": inset, "children": [], "block_type": "inset", "page_start": 85, "page_end": 85},
        ],
    }
    chunks, _ = _textbook_chunks(tree)
    insets = [c for c in chunks if c["fragment_data"]["block_type"] == "inset"]
    assert len(insets) == 1
    fd = insets[0]["fragment_data"]
    assert fd["content"] == inset
    assert fd["combined_text"].startswith("§ 9. Цифровое государство > Интересно\n\n")
    assert fd["page_start"] == 85 and fd["page_end"] == 85
    body = [c for c in chunks if c["fragment_data"]["block_type"] == "body"]
    assert body and body[0]["fragment_data"]["page_start"] is None


def test_textbook_long_paragraph_split_respects_abbreviations():
    sentence = "В 2022 г. утверждены Основы государственной политики по сохранению ценностей. "
    long_para = (sentence * 40).strip()
    tree = {"title": "Раздел", "level": 1, "content": long_para, "children": []}
    chunks, _ = _textbook_chunks(tree)
    assert len(chunks) >= 2
    for c in chunks:
        content = c["fragment_data"]["content"]
        assert len(content) <= chunker_mod.TEXTBOOK_MAX_CHARS
        assert not content.endswith("г.")
        assert not content.startswith("утверждены")


def test_chunk_file_returns_dropped_blocks_and_block_type_stats(client):
    def fake_process(path, min_size=50, document_kind="normative", prose_min_chars=None, stats=None, **_kw):
        if stats is not None:
            stats["dropped_blocks"] = {"exercise": {"count": 2, "chars": 150}}
            stats["chunks_by_block_type"] = {"body": {"count": 1, "chars": 80}}
        return [SAMPLE_CHUNK], DOC_TYPE_INFO

    with patch.object(chunker_mod, "process_document_to_chunks", side_effect=fake_process):
        resp = client.post(
            "/chunk/?document_kind=textbook",
            files={"file": ("book.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dropped_blocks"] == {"exercise": {"count": 2, "chars": 150}}
    assert data["chunks_by_block_type"] == {"body": {"count": 1, "chars": 80}}


def test_chunk_size_constants_come_from_env(monkeypatch):
    monkeypatch.setenv("CHUNKER_TEXTBOOK_TARGET_CHARS", "2500")
    monkeypatch.setenv("CHUNKER_TEXTBOOK_MAX_CHARS", "bad-value")
    assert chunker_mod._env_int("CHUNKER_TEXTBOOK_TARGET_CHARS", 1500) == 2500
    assert chunker_mod._env_int("CHUNKER_TEXTBOOK_MAX_CHARS", 2000) == 2000
    assert chunker_mod._env_int("CHUNKER_UNSET_VARIABLE", 7) == 7


def test_textbook_page_break_after_abbreviation_is_rejoined():
    """«…в 2022 г.» + разрыв страницы + «утверждены…» — один абзац, не два."""
    tree = {
        "title": "§ 4",
        "level": 1,
        "content": (
            "Сегодня в России традиционные ценности защищаются на государственном уровне. "
            "Указом Президента России в 2022 г.\n\n"
            "утверждены Основы государственной политики по сохранению ценностей. "
            + PROSE_PARAGRAPH
        ),
        "children": [],
    }
    chunks, _ = _textbook_chunks(tree)
    joined = "\n".join(c["fragment_data"]["content"] for c in chunks)
    assert "в 2022 г. утверждены Основы" in joined
    # A genuine sentence end followed by a lowercase paragraph is still kept apart
    # when the splitter sees a boundary («…в 2009 г.» + «Сайт…» starts uppercase).
    assert chunker_mod._normalize_prose_content("Портал появился в 2009 г.\n\nСайт носил информационный характер.") == (
        "Портал появился в 2009 г.\n\nСайт носил информационный характер."
    )


# ─── chunking_method: registry, parameter, response ───────────────────


def test_chunking_methods_endpoint_lists_registry(client, monkeypatch):
    monkeypatch.delenv("CHUNKER_EMBEDDING_URL", raising=False)
    resp = client.get("/chunking-methods/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default"] == "rules"
    by_id = {m["id"]: m for m in data["methods"]}
    assert list(by_id) == ["rules", "layout", "semantic", "llm"]
    assert by_id["rules"]["available"] is True
    assert by_id["rules"]["document_kinds"] == ["normative", "textbook", "encyclopedia"]
    assert by_id["semantic"]["available"] is False
    assert "CHUNKER_EMBEDDING_URL" in by_id["semantic"]["reason"]
    assert "normative" not in by_id["llm"]["document_kinds"]


def test_chunk_file_forwards_chunking_method_and_reports_engine(client):
    seen = {}

    def fake_process(path, min_size=50, document_kind="normative", prose_min_chars=None, stats=None, chunking_method="rules"):
        seen["method"] = chunking_method
        if stats is not None:
            stats.update({"chunking_method": chunking_method, "chunking_engine": "layout_pdf", "chunking_fallback": None})
        return [SAMPLE_CHUNK], DOC_TYPE_INFO

    with patch.object(chunker_mod, "process_document_to_chunks", side_effect=fake_process):
        resp = client.post(
            "/chunk/?document_kind=textbook&chunking_method=layout",
            files={"file": ("book.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        )
    assert resp.status_code == 200
    assert seen["method"] == "layout"
    data = resp.json()
    assert data["chunking_method"] == "layout"
    assert data["chunking_engine"] == "layout_pdf"
    assert data["chunking_fallback"] is None


def test_chunk_file_defaults_to_rules_and_rejects_unknown_method(client):
    seen = {}

    def fake_process(path, **kwargs):
        seen.update(kwargs)
        return [SAMPLE_CHUNK], DOC_TYPE_INFO

    with patch.object(chunker_mod, "process_document_to_chunks", side_effect=fake_process):
        ok = client.post("/chunk/", files={"file": ("doc.docx", io.BytesIO(b"PK"), "application/octet-stream")})
        bad = client.post(
            "/chunk/?chunking_method=magic",
            files={"file": ("doc.docx", io.BytesIO(b"PK"), "application/octet-stream")},
        )
    assert ok.status_code == 200
    assert seen["chunking_method"] == "rules"
    assert ok.json()["chunking_method"] == "rules"
    assert bad.status_code == 400
    assert "unknown chunking method" in bad.json()["detail"]


def test_check_chunking_method_rules_for_kind_and_configuration(monkeypatch):
    chunker_mod.check_chunking_method("rules", "normative")
    with pytest.raises(chunker_mod.ChunkingMethodUnavailable, match="not supported for document_kind 'normative'"):
        chunker_mod.check_chunking_method("layout", "normative")
    monkeypatch.delenv("CHUNKER_EMBEDDING_URL", raising=False)
    with pytest.raises(chunker_mod.ChunkingMethodUnavailable, match="not configured"):
        chunker_mod.check_chunking_method("semantic", "textbook")
    monkeypatch.setenv("CHUNKER_EMBEDDING_URL", "http://embeddings:8000/v1")
    chunker_mod.check_chunking_method("semantic", "textbook")


def test_process_document_to_chunks_unsupported_method_is_400_at_endpoint(client):
    resp = client.post(
        "/chunk/?document_kind=normative&chunking_method=llm",
        files={"file": ("law.docx", io.BytesIO(b"PK"), "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "chunking method 'llm' is not supported" in resp.json()["detail"]


def test_process_document_to_chunks_records_method_in_stats(tmp_path):
    doc = tmp_path / "book.docx"
    doc.write_bytes(b"PK")
    tree = {"title": "Глава 1", "level": 1, "content": PROSE_PARAGRAPH, "children": []}
    stats = {}
    with (
        patch.object(chunker_mod, "extract_outline_from_document", return_value=tree),
        patch.object(chunker_mod, "identify_document_type", return_value=DOC_TYPE_INFO),
    ):
        chunks, _info = chunker_mod.process_document_to_chunks(
            str(doc), min_size=20, document_kind="textbook", stats=stats, chunking_method="rules"
        )
    assert chunks
    assert stats["chunking_method"] == "rules"
    assert stats["chunking_engine"] == "hierarchical_outline"
    assert stats["chunking_fallback"] is None


def test_semantic_segmenter_boundaries_force_chunk_breaks():
    """A segmenter returning boundary indices splits the packing there."""
    paras = [(f"Абзац номер {i}. " + "Слово " * 30).strip() for i in range(6)]
    tree = {"title": "§ 1", "level": 1, "content": "\n\n".join(paras), "children": []}

    class _Seg:
        seen = None

        def boundaries(self, items):
            _Seg.seen = list(items)
            return {3}

    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="book", document_kind="textbook", prose_min_chars=0, segmenter=_Seg()
    )
    assert _Seg.seen == paras
    contents = [c["fragment_data"]["content"] for c in chunks]
    assert len(contents) == 2
    assert contents[0].startswith("Абзац номер 0.") and "Абзац номер 2." in contents[0]
    assert contents[1].startswith("Абзац номер 3.")


# ─── chunking_stats (диагностика llm/layout в ответе) ─────────────────


def test_chunk_file_returns_chunking_stats_for_llm(client):
    def fake_process(path, min_size=50, document_kind="normative", prose_min_chars=None, stats=None, **_kw):
        if stats is not None:
            stats["chunking_method"] = "llm"
            stats["chunking_engine"] = "llm_segmentation"
            stats["llm_segmentation_source"] = "layout"
            stats["llm_segmentation"] = {
                "windows": 3, "llm_ok": 2, "fallback": 1, "salvaged_truncated": 0, "gapfill_segments": 1,
                "suspicious_windows": [], "errors": {2: "deadline"}, "llm_calls": 3, "llm_seconds": 12.5,
            }
            stats["layout"] = {"pages": 3, "blocks": {"body": 4}}
        return [SAMPLE_CHUNK], DOC_TYPE_INFO

    with patch.object(chunker_mod, "process_document_to_chunks", side_effect=fake_process):
        resp = client.post(
            "/chunk/?document_kind=textbook&chunking_method=llm",
            files={"file": ("book.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        )
    assert resp.status_code == 200
    cs = resp.json()["chunking_stats"]
    assert cs["paragraph_source"] == "layout"
    assert cs["layout"] == {"pages": 3, "blocks": {"body": 4}}
    seg = cs["llm_segmentation"]
    assert seg["windows"] == 3 and seg["fallback"] == 1
    assert "errors" not in seg
    assert seg["errors_count"] == 1
    assert seg["errors_sample"] == ["2: deadline"]


def test_chunk_file_chunking_stats_empty_for_rules(client):
    def fake_process(path, **_kw):
        return [SAMPLE_CHUNK], DOC_TYPE_INFO

    with patch.object(chunker_mod, "process_document_to_chunks", side_effect=fake_process):
        resp = client.post("/chunk/", files={"file": ("law.docx", io.BytesIO(b"PK"), "application/octet-stream")})
    assert resp.status_code == 200
    assert resp.json()["chunking_stats"] == {}


def test_record_llm_fallback_marks_rule_labelled_windows():
    stats = {"llm_segmentation": {"windows": 10, "fallback": 3, "errors": {1: "deadline", 4: "busy: 503", 7: "deadline"}}}
    chunker_mod._record_llm_fallback(stats)
    assert stats["chunking_fallback"] == "llm: 3/10 windows by rules (deadline: 2, busy: 1)"

    clean = {"llm_segmentation": {"windows": 10, "fallback": 0, "errors": {}}}
    chunker_mod._record_llm_fallback(clean)
    assert "chunking_fallback" not in clean

    # Уже записанную причину (например, layout недоступен) не перезаписываем.
    kept = {"chunking_fallback": "layout unavailable", "llm_segmentation": {"windows": 2, "fallback": 2, "errors": {}}}
    chunker_mod._record_llm_fallback(kept)
    assert kept["chunking_fallback"] == "layout unavailable"


def test_prose_chunks_carry_boundary_flags_normative_do_not():
    tree = {"title": "§ 1. Тема", "level": 2, "content": "обрывок начала. " + "Целое предложение. " * 30 + "хвост без точки", "children": []}
    chunks = chunker_mod.create_llm_chunks(tree, min_size=20, document_name="x", document_kind="textbook", prose_min_chars=0)
    assert chunks
    assert chunks[0]["fragment_data"]["boundary_flags"]["starts_mid_sentence"] is True
    assert chunks[-1]["fragment_data"]["boundary_flags"]["ends_mid_sentence"] is True
    law = {"title": "Статья 1", "level": 2, "content": "1. Первый пункт статьи. 2. Второй пункт статьи.", "children": []}
    law_chunks = chunker_mod.create_llm_chunks(law, min_size=5, document_name="law", document_kind="normative")
    assert law_chunks and all("boundary_flags" not in c["fragment_data"] for c in law_chunks)
