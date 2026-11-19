"""Tests for textbook/encyclopedia source-reference guard and prompt content."""

from __future__ import annotations

import sys
import types
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from import_paths import prefer_agent_api

prefer_agent_api()


def _install_import_stubs():
    broken_langgraph = False
    try:
        # Import for real: the previous check treated "not imported yet" as
        # "broken" (sys.modules.get returns None), so the stub replaced a
        # perfectly good langgraph on every run — which is why no test ever
        # exercised the compiled graph.
        import langgraph.graph  # noqa: F401

        broken_langgraph = False
    except Exception:
        broken_langgraph = True
    if broken_langgraph:
        for name in list(sys.modules):
            if name == "langgraph" or name.startswith("langgraph."):
                del sys.modules[name]
        langgraph = types.ModuleType("langgraph")
        langgraph.__spec__ = types.SimpleNamespace(name="langgraph")
        graph = types.ModuleType("langgraph.graph")
        graph.StateGraph = object
        graph.END = "__end__"
        graph.START = "__start__"
        checkpoint = types.ModuleType("langgraph.checkpoint")
        checkpoint_base = types.ModuleType("langgraph.checkpoint.base")
        checkpoint_base.BaseCheckpointSaver = object
        sys.modules["langgraph"] = langgraph
        sys.modules["langgraph.graph"] = graph
        sys.modules["langgraph.checkpoint"] = checkpoint
        sys.modules["langgraph.checkpoint.base"] = checkpoint_base


_install_import_stubs()

from agent.assistant_graph import GENAAssistant
from agent.nodes.generate_question.system_prompt import (
    KIND_ENCYCLOPEDIA,
    KIND_NORMATIVE,
    KIND_TEXTBOOK,
    SYSTEM_PROMPT_MULTI,
    SYSTEM_PROMPT_ONE,
    SYSTEM_PROMPT_OPEN,
)
from agent.nodes.llm_validator.question_guard import (
    QUESTION_CONTEXT_INDEX,
    find_source_reference,
    find_source_reference_in_question,
    should_apply_source_ref_guard,
)
from agent.nodes.llm_validator.validator import (
    EXPECTED_COUNTS,
    MULTIPLIER_INDICES,
    LLMValidator,
)

# c1_question subcriteria order: bravity, basis, clarity, not_binary, context
QUESTION_CLARITY_INDEX = 2


# ─── find_source_reference ──────────────────────────────────────────────

_BAD_TASKS = [
    "Какой город пал в результате боевых действий, описанных в тексте?",
    "Какие последствия имел военный конфликт в Ираке, описанный в тексте?",
    "Какую цель США на самом деле преследовали при военном вмешательстве в Сирии в 2014 году, согласно приведённому тексту?",
    "Какие действия иностранных государств упоминаются в тексте как нарушающие международное право?",
    "Какие регионы или страны указаны в тексте как ключевые драйверы мирового экономического роста в XXI веке?",
    "Что говорится по тексту о реформе армии?",
    "Какой принцип указан в данном тексте?",
    # paraphrases from the 5 Aug textbook run
    "Какой мировой порядок приходит на смену однополярному, согласно анализу современных тенденций в политике?",
    "Какой вывод можно сделать о целях США в Афганистане на основе приведённых фактов?",
    "Какой характер имела украинская политика в описанный период?",
    "Какие два фактора определяли двойственный характер украинской политики в описанный период?",
    "Какой двойной характер имела украинская политика в описанный период?",
    "Какие тенденции в мировой политике описаны согласно приведённому описанию?",
    "Как описано в учебнике, какие меры приняли власти?",
    "Какие реформы рассматриваются согласно анализу в главе 22 учебника?",
    "Что говорится в главе 22 о реформе армии?",
    "Согласно статье БРЭ, кто был первым президентом?",
    "Как описано в статье, какие меры приняли власти?",
    "Какой вывод следует из данного тома?",
    "Что изучается в курсе отечественной истории?",
    "Из текста следует, что реформа не удалась.",
    "Какой вывод можно сделать по данному тексту?",
    "В вышеприведённом фрагменте назван год реформы.",
    # «указаны как» is a source reference even without «в тексте» (expert review,
    # 20 Aug) — this line used to sit in _GOOD_TASKS.
    "Какие регионы или страны указаны как ключевые драйверы мирового экономического роста в XXI веке?",
    "Какие страны указаны как новые центры многополярного мира с отличным от Запада цивилизационным характером в начале XXI века?",
    "Какие теракты произошли в России в 2002–2004 годах, согласно изложенным фактам?",
    "Какую цель, по мнению автора, преследуют западные страны, заявляя о защите «мира, основанного на правилах», в начале 2020-х годов?",
    "Что автор считает главной причиной распада СССР?",
    "Какие авторы отмечают снижение рождаемости в Европе в 2010-х годах?",
    "Какие меры названы как наиболее действенные для борьбы с инфляцией?",
    # «курс» and «том» are still forbidden when they really mean the source
    "Какие темы рассматриваются в данном курсе истории?",
    # Найдено на живом прогоне БРЭ: короткие формы причастий не покрывались, а
    # для «в статье» нет универсального перехвата, как для «в тексте».
    "Какие два вида путассу описаны в статье Большой российской энциклопедии?",
    "Какие меры описаны в статье БРЭ?",
    "Какие виды перечислены в статье?",
    "Какие органы названы в статье?",
    "Какие меры описаны в тексте?",
    "Какой вывод следует из данного тома?",
]

_GOOD_TASKS = [
    "Какие меры были приняты в России в начале 2000-х годов для ограничения влияния региональных элит?",
    "Когда в России были отменены выборы глав местных администраций для ограничения влияния региональных элит?",
    "Какой город США пал в ходе Гражданской войны 1861–1865 годов?",
    "Какие последствия имел военный конфликт в Ираке в начале XXI века?",
    "Какую цель США на самом деле преследовали при военном вмешательстве в Сирии в 2014 году?",
    "Какая форма правления установлена в Российской Федерации согласно Конституции РФ?",
    "В тексте Конституции РФ какая денежная единица названа основной?",
    # legitimate «согласно / по данным» from the same textbook run
    "Какой основной целью была провозглашена военная интервенция в Ливии в 2011 году согласно резолюции Совета безопасности ООН?",
    "Какая страна стала третьей экономикой мира по данным Всемирного банка в конце 2022 года?",
    "Какой статус был предусмотрен для ДНР и ЛНР по Минским соглашениям 2015 года?",
    "Какова роль главы государства в разделении властей?",
    "Какие полномочия принадлежат главе государства?",
    "Как описано в тексте Конституции РФ разделение властей?",
    "Согласно статье 15 Конституции РФ, какие акты имеют высшую силу?",
    # rewritten counterparts of the new _BAD_TASKS above
    "Какие страны стали новыми центрами многополярного мира с отличным от Запада цивилизационным характером в начале XXI века?",
    "Какие теракты произошли в России в 2002–2004 годах?",
    "Какую цель преследовали западные страны, заявляя о защите «мира, основанного на правилах», в начале 2020-х годов?",
    # «как» is not adjacent to the participle — a real document is named
    "Какие акты указаны в Конституции РФ как имеющие высшую силу?",
    # «автор» followed by a named work is a legitimate subject
    "Какую цель преследовал автор «Капитала» при написании труда?",
    # Нумерованная статья — это ссылка на нормативный акт, а не на источник чанка
    "Какие акты указаны в статье 15 Конституции РФ как имеющие высшую силу?",
    "Что описано в статье 5 Уголовного кодекса РФ?",
    # «авторитетный» / «авторский» must not be read as «автор»
    "По мнению авторитетных экспертов, какой была доля экспорта России в 2020 году?",
    "Какой авторский стиль характерен для прозы Андрея Платонова?",
    # domain vocabulary: «курс» as an exchange rate / being informed,
    # «том» inside «томографический» — all ordinary factual wording
    "Какие изменения произошли в курсе рубля в 1998 году?",
    "Кто был в курсе заговора декабристов?",
    "Что показано на данном томографическом снимке?",
    "Какие данные томографии используются в диагностике заболеваний?",
]


def test_find_source_reference_catches_report_examples():
    for task in _BAD_TASKS:
        assert find_source_reference(task), f"expected match for: {task}"


def test_find_source_reference_clean_questions():
    for task in _GOOD_TASKS:
        assert find_source_reference(task) is None, f"unexpected match for: {task}"


def test_find_source_reference_normative_phrase_not_matched():
    assert (
        find_source_reference(
            "Какая форма правления установлена в тексте Конституции РФ?"
        )
        is None
    )


def test_guard_reads_only_the_question_wording():
    """It zeroes question_context, so it must not judge the answer or options.

    For an open question `outputs` is the reference answer, where «в описанный
    период» is legitimate prose — matching there failed the question and blamed
    wording it did not have.
    """
    clean_question = {
        "task": "Какие меры принимались в России в начале 2000-х годов?",
        "outputs": "В описанный период были приняты меры по укреплению вертикали власти.",
        "option_1": "Согласно приведённому тексту — реформа налогов",
    }
    assert find_source_reference_in_question(clean_question) is None

    dirty_question = {
        "task": "Какие меры описаны в тексте?",
        "outputs": "Реформа налогов.",
    }
    assert find_source_reference_in_question(dirty_question)


def test_should_apply_only_textbook_encyclopedia():
    assert should_apply_source_ref_guard("textbook") is True
    assert should_apply_source_ref_guard("encyclopedia") is True
    assert should_apply_source_ref_guard("normative") is False
    assert should_apply_source_ref_guard(None) is False
    assert should_apply_source_ref_guard("Textbook") is True


# ─── validator integration ──────────────────────────────────────────────

def _perfect_block_text(qtype: str, block_key: str) -> str:
    n = EXPECTED_COUNTS[qtype][block_key]
    return "\n".join(f"criterion_{i} 1 — ok" for i in range(n))


def _fake_llm_all_pass():
    """LLM that returns all-1 scores for every validation block."""
    llm = MagicMock()

    def _invoke(_messages=None, **_kwargs):
        # Content is parsed by block; we return enough 1s for the largest block.
        return MagicMock(content="\n".join(f"c{i} 1 — ok" for i in range(12)))

    chain = MagicMock()
    chain.invoke = MagicMock(side_effect=lambda _input: _invoke())

    # ChatPromptTemplate | llm produces a chain; we patch at evaluate level instead.
    return llm


def test_guard_zeros_question_context_and_fails_validation():
    """When LLM would pass, guard still forces question_context=0 and passed=False."""
    question = {
        "task": "Какой город пал в результате боевых действий, описанных в тексте?",
        "option_1": "Ричмонд",
        "option_2": "Нью-Йорк",
        "option_3": "Бостон",
        "option_4": "Чикаго",
        "outputs": 1,
    }

    validator = LLMValidator.__new__(LLMValidator)
    validator.thresholds = {"open": 14.0, "one": 18.0, "multi": 18.0}

    call_count = {"n": 0}

    def fake_invoke(_prompt_or_empty):
        call_count["n"] += 1
        # Return enough scores for any block (max expected is 9 for c2_options).
        return MagicMock(
            content="\n".join(f"sub_{i} 1 — ok" for i in range(12))
        )

    fake_chain = MagicMock()
    fake_chain.invoke = MagicMock(side_effect=fake_invoke)

    with patch(
        "agent.nodes.llm_validator.validator.ChatPromptTemplate.from_messages"
    ) as from_messages:
        prompt_mock = MagicMock()
        prompt_mock.__or__ = MagicMock(return_value=fake_chain)
        from_messages.return_value = prompt_mock
        validator.llm = MagicMock()

        result = validator.evaluate(
            qtype="one",
            source_text="Глава 5 > Гражданская война в США\n\nВ 1865 году пал Ричмонд.",
            question=question,
            document_kind="textbook",
        )

    assert result["by_block"]["c1_question"][QUESTION_CONTEXT_INDEX] == 0
    assert "запрещённая отсылка" in result["justifications"]["c1_question"][QUESTION_CONTEXT_INDEX]
    assert result["passed"] is False
    assert result["total"] == 0.0


def test_guard_does_not_apply_for_normative():
    question = {
        "task": "Какой город пал в результате боевых действий, описанных в тексте?",
        "option_1": "A",
        "option_2": "B",
        "option_3": "C",
        "option_4": "D",
        "outputs": 1,
    }

    validator = LLMValidator.__new__(LLMValidator)
    validator.thresholds = {"open": 14.0, "one": 18.0, "multi": 18.0}

    fake_chain = MagicMock()
    fake_chain.invoke = MagicMock(
        side_effect=lambda _i: MagicMock(
            content="\n".join(f"sub_{i} 1 — ok" for i in range(12))
        )
    )

    with patch(
        "agent.nodes.llm_validator.validator.ChatPromptTemplate.from_messages"
    ) as from_messages:
        prompt_mock = MagicMock()
        prompt_mock.__or__ = MagicMock(return_value=fake_chain)
        from_messages.return_value = prompt_mock
        validator.llm = MagicMock()

        result = validator.evaluate(
            qtype="one",
            source_text="Статья 1. Текст.",
            question=question,
            document_kind="normative",
        )

    # Without guard, all-1 scores from fake LLM → question_context stays 1
    assert result["by_block"]["c1_question"][QUESTION_CONTEXT_INDEX] == 1
    assert result["passed"] is True


def test_guard_keeps_parse_complete_honest_on_truncated_output():
    """Guard must not pad short score vectors into looking fully parsed."""
    question = {
        "task": "Какие действия упоминаются в тексте как нарушающие право?",
        "option_1": "A",
        "option_2": "B",
        "option_3": "C",
        "option_4": "D",
        "outputs": 1,
    }

    validator = LLMValidator.__new__(LLMValidator)
    validator.thresholds = {"open": 14.0, "one": 18.0, "multi": 18.0}

    fake_chain = MagicMock()
    fake_chain.invoke = MagicMock(
        side_effect=lambda _i: MagicMock(content="sub_0 1 — ok\nsub_1 1 — ok")
    )

    with patch(
        "agent.nodes.llm_validator.validator.ChatPromptTemplate.from_messages"
    ) as from_messages:
        prompt_mock = MagicMock()
        prompt_mock.__or__ = MagicMock(return_value=fake_chain)
        from_messages.return_value = prompt_mock
        validator.llm = MagicMock()

        result = validator.evaluate(
            qtype="one",
            source_text="Глава 3 > Международное право",
            question=question,
            document_kind="textbook",
        )

    assert len(result["by_block"]["c1_question"]) < EXPECTED_COUNTS["one"]["c1_question"]
    assert result["parse_complete"] is False
    assert result["passed"] is False


def test_question_clarity_is_critical_multiplier():
    """A question unusable without the chunk must be rejectable on clarity alone."""
    for qtype in ("open", "one", "multi"):
        assert QUESTION_CLARITY_INDEX in MULTIPLIER_INDICES[qtype]["c1_question"]


def test_clarity_zero_fails_validation():
    """question_clarity=0 alone zeroes the total, even with everything else at 1."""
    question = {
        "task": "Какие меры были предприняты для улучшения экономической ситуации?",
        "option_1": "A",
        "option_2": "B",
        "option_3": "C",
        "option_4": "D",
        "outputs": 1,
    }

    validator = LLMValidator.__new__(LLMValidator)
    validator.thresholds = {"open": 14.0, "one": 18.0, "multi": 18.0}

    def scores_for(_call_index=[0]):
        # c1_question comes first: clarity (index 2) = 0, everything else = 1.
        _call_index[0] += 1
        if _call_index[0] == 1:
            return "a 1 — ok\nb 1 — ok\nc 0 — непонятен без исходного текста\nd 1 — ok\ne 1 — ok"
        return "\n".join(f"sub_{i} 1 — ok" for i in range(12))

    fake_chain = MagicMock()
    fake_chain.invoke = MagicMock(side_effect=lambda _i: MagicMock(content=scores_for()))

    with patch(
        "agent.nodes.llm_validator.validator.ChatPromptTemplate.from_messages"
    ) as from_messages:
        prompt_mock = MagicMock()
        prompt_mock.__or__ = MagicMock(return_value=fake_chain)
        from_messages.return_value = prompt_mock
        validator.llm = MagicMock()

        result = validator.evaluate(
            qtype="one",
            source_text="Глава 2 > Россия в начале 1990-х годов\n\nБыли предприняты реформы.",
            question=question,
            document_kind="textbook",
        )

    assert result["by_block"]["c1_question"][QUESTION_CLARITY_INDEX] == 0
    assert result["parse_complete"] is True
    assert result["total"] == 0.0
    assert result["passed"] is False


def test_should_retry_routes_to_refine_on_guard_fail():
    assert (
        GENAAssistant._should_retry(
            {
                "pipeline_mode": "full",
                "validation_result": {"passed": False, "total": 0.0},
                "retry_count": 0,
                "refine_available": True,
            }
        )
        == "refine"
    )
    assert (
        GENAAssistant._should_retry(
            {
                "pipeline_mode": "full",
                "validation_result": {"passed": False},
                "retry_count": None,
                "refine_available": True,
            }
        )
        == "refine"
    )


# ─── prompt content ─────────────────────────────────────────────────────

def test_kind_textbook_requires_context_anchors():
    assert KIND_TEXTBOOK is not None
    assert "якоря" in KIND_TEXTBOOK.lower() or "Контекст вопроса" in KIND_TEXTBOOK
    assert "описанный в тексте" in KIND_TEXTBOOK
    assert "начало 2000-х" in KIND_TEXTBOOK


def test_kind_blocks_ban_author_and_participle_references():
    """Expert review, 20 Aug: «по мнению автора» / «указаны как» / «согласно
    изложенным фактам» must be spelled out as forbidden, with worked examples."""
    for block in (KIND_TEXTBOOK, KIND_ENCYCLOPEDIA):
        assert block is not None
        assert "по мнению автора" in block
        assert "указаны как" in block
        assert "согласно изложенным фактам" in block
        # each phrase also appears in a «плохо → хорошо» pair
        assert "новыми центрами многополярного мира" in block
        assert "Какие теракты произошли в России в 2002–2004 годах?" in block


def test_base_prompts_no_longer_require_source_field():
    marker = 'информацию из поля "Источник"'
    marker2 = "информацию из поля «Источник»"
    for prompt in (SYSTEM_PROMPT_ONE, SYSTEM_PROMPT_MULTI, SYSTEM_PROMPT_OPEN):
        assert prompt is not None
        assert marker not in prompt
        assert marker2 not in prompt
        assert "описанный в тексте" in prompt
        assert "в тексте" in prompt


def test_base_prompts_state_self_sufficiency_positively():
    """The positive rule must exist for all question types, not only in KIND_* blocks."""
    for prompt in (SYSTEM_PROMPT_ONE, SYSTEM_PROMPT_MULTI, SYSTEM_PROMPT_OPEN):
        assert "ПОЛНОСТЬЮ САМОДОСТАТОЧНЫМ" in prompt
        assert "стране, организации, личности или периоде" in prompt
        assert "в России в начале 1990-х годов" in prompt
        assert "НЕ ЗАМЕНЯЕТ контекст" in prompt


def test_language_instruction_anchor_still_present():
    """generate_question.py rewrites item 1 by regex; the anchor must stay intact."""
    for prompt in (SYSTEM_PROMPT_ONE, SYSTEM_PROMPT_MULTI):
        assert (
            "1. **ИСПОЛЬЗУЙТЕ ТОЛЬКО РУССКИЙ ЯЗЫК** для создания вопроса и вариантов ответов."
            in prompt
        )
    assert (
        "1. ИСПОЛЬЗУЙТЕ ИСКЛЮЧИТЕЛЬНО РУССКИЙ ЯЗЫК для формулировки вопроса и ответа."
        in SYSTEM_PROMPT_OPEN
    )


def test_kind_normative_contains_source_field_rule():
    assert KIND_NORMATIVE is not None
    assert "Источник" in KIND_NORMATIVE
    assert "ОБЯЗАТЕЛЬНА" in KIND_NORMATIVE or "ОБЯЗАТЕЛЬНО" in KIND_NORMATIVE


# ─── textbook grounding rules (exercises, external facts, captions) ────


def test_kind_prose_blocks_forbid_solving_exercises_and_external_facts():
    assert "Выясните" in KIND_TEXTBOOK
    assert "Госуслуги" in KIND_TEXTBOOK
    assert "Никаких фактов извне" in KIND_TEXTBOOK
    assert "Подписи к иллюстрациям" in KIND_TEXTBOOK
    assert "Якорь берётся ТОЛЬКО из чанка" in KIND_TEXTBOOK
    assert "Никаких фактов извне" in KIND_ENCYCLOPEDIA
    assert "Якорь берётся ТОЛЬКО из статьи" in KIND_ENCYCLOPEDIA
    # Normative rules untouched by the prose additions.
    assert "Выясните" not in KIND_NORMATIVE


def test_block_instructions_only_for_known_prose_block_types():
    from agent.nodes.generate_question.system_prompt import block_instructions_for

    assert "подпись к иллюстрации" in block_instructions_for("caption")
    assert "врезка" in block_instructions_for("inset")
    assert "словар" in block_instructions_for("glossary_entry")
    assert block_instructions_for("body") == ""
    assert block_instructions_for(None) == ""


def _evaluate_capturing_prompts(document_kind: str):
    """Run LLMValidator.evaluate with a fake LLM and return the human prompts it built."""
    question = {
        "task": "В каком году был создан портал «Госуслуги»?",
        "option_1": "A",
        "option_2": "B",
        "option_3": "C",
        "option_4": "D",
        "outputs": 1,
    }
    validator = LLMValidator.__new__(LLMValidator)
    validator.thresholds = {"open": 14.0, "one": 18.0, "multi": 18.0}
    fake_chain = MagicMock()
    fake_chain.invoke = MagicMock(
        side_effect=lambda _i: MagicMock(content="\n".join(f"sub_{i} 1 — ok" for i in range(12)))
    )
    prompts = []
    with patch(
        "agent.nodes.llm_validator.validator.ChatPromptTemplate.from_messages"
    ) as from_messages:
        def _capture(messages):
            prompts.append("\n".join(getattr(m, "content", str(m)) for m in messages))
            prompt_mock = MagicMock()
            prompt_mock.__or__ = MagicMock(return_value=fake_chain)
            return prompt_mock

        from_messages.side_effect = _capture
        validator.llm = MagicMock()
        validator.evaluate(
            qtype="one",
            source_text="Глава 3 > § 9. Цифровое государство\n\nПортал «Госуслуги» появился в 2009 г.",
            question=question,
            document_kind=document_kind,
        )
    return prompts


def test_validator_adds_exercise_and_external_fact_rules_for_prose_only():
    textbook_prompts = _evaluate_capturing_prompts("textbook")
    joined = "\n".join(textbook_prompts)
    assert "выполнении учебного задания" in joined
    assert "options_right_basis (для one и multi)" in joined
    # The outputs note is attached to the options block, not to every criterion.
    assert sum("options_right_basis (для one и multi)" in p for p in textbook_prompts) == 1

    normative_prompts = _evaluate_capturing_prompts("normative")
    joined_norm = "\n".join(normative_prompts)
    assert "выполнении учебного задания" not in joined_norm
    assert "options_right_basis (для one и multi)" not in joined_norm
