"""
LLM-валидатор вопросов по критериям качества.
Поддерживает три типа заданий: open, onech, multich.
Адаптирован для использования с Ollama через LangChain.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Literal
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from llm_factory import create_chat_llm

from agent.document_kinds import normalize_document_kind

from .question_guard import (
    QUESTION_CONTEXT_INDEX,
    find_source_reference_in_question,
    should_apply_source_ref_guard,
)


def _read_text_file(path: Path) -> str:
    """Читает текстовый файл с промптом."""
    with path.open("r", encoding="utf-8") as f:
        return f.read()


def _find_prompt_file(
    file_name: str, search_roots: Optional[List[Path]] = None
) -> Path:
    """Находит файл промпта в различных директориях."""
    if search_roots is None:
        search_roots = []

    candidates = list(search_roots)
    candidates.append(Path(__file__).parent.resolve())
    candidates.append(Path.cwd().resolve())

    for root in candidates:
        p = (root / file_name).resolve()
        if p.exists():
            return p

    raise FileNotFoundError(f"Не найден файл промпта: {file_name}")


def _build_prompt(template: str, source_text: str, question_json: str) -> str:
    """Строит промпт из шаблона и данных."""
    from agent.schemas.question import fence_user_content

    fenced_source = fence_user_content(source_text or "", "SOURCE_TEXT")
    fenced_question = fence_user_content(question_json, "QUESTION_JSON")
    return (
        template.rstrip()
        + "\n\nИсходный текст: \n\n"
        + fenced_source
        + "\n\nЗадание: \n"
        + fenced_question
        + "\n"
    )


# --------------------------- Константы и конфиг ---------------------------

THRESHOLDS = {
    "open": 14.0,
    "one": 18.0,
    "multi": 18.0,
}

MAX_POINTS = {
    "open": 16.0,    # c1: 4.5 + c2: 5.5 + c4: 4 + c5: 2 = 16.0
    "one": 20.5,     # c1: 4.5 + c2: 8.0 + c3: 2 + c4: 4 + c5: 2 = 20.5
    "multi": 20.5,
}

# Веса подкритериев внутри каждого блока (порядок совпадает с промптами)
WEIGHTS = {
    "open": {
        "c1_question": [0.5, 1.0, 1.0, 1.0, 1.0],        # bravity=0.5
        "c2_outputs":  [0.5, 1.0, 1.0, 1.0, 1.0, 1.0],   # bravity=0.5
        "c4_logic":    [1.0, 1.0, 1.0, 1.0],
        "c5_phrase":   [1.0, 1.0],
    },
    "multi": {
        "c1_question": [0.5, 1.0, 1.0, 1.0, 1.0],
        "c2_options":  [1.0, 0.5, 0.5, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],  # right_count=0.5, bravity=0.5
        "c3_outputs":  [1.0, 1.0],
        "c4_logic":    [1.0, 1.0, 1.0, 1.0],
        "c5_phrase":   [1.0, 1.0],
    },
    "one": {
        "c1_question": [0.5, 1.0, 1.0, 1.0, 1.0],
        "c2_options":  [1.0, 0.5, 0.5, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "c3_outputs":  [1.0, 1.0],
        "c4_logic":    [1.0, 1.0, 1.0, 1.0],
        "c5_phrase":   [1.0, 1.0],
    },
}

# Индексы критических подкритериев-мультипликаторов.
# Если любой из них = 0, итоговый балл обнуляется.
MULTIPLIER_INDICES = {
    "open": {
        "c1_question": [1, 2, 4],   # question_basis, question_clarity, question_context
        "c2_outputs":  [1, 2],   # outputs_question_duplication, outputs_basis
    },
    "multi": {
        "c1_question": [1, 2, 4],   # question_basis, question_clarity, question_context
        "c2_options":  [3, 4, 7],  # options_question_duplication, options_duplication, options_right_basis
        "c3_outputs":  [0, 1],   # outputs_include, outputs_exclude
    },
    "one": {
        "c1_question": [1, 2, 4],
        "c2_options":  [3, 4, 7],
        "c3_outputs":  [0, 1],
    },
}

# Файлы шаблонов промптов (1 критерий == 1 промпт)
PROMPT_FILES = {
    "open": {
        "c1_question": "prompts_evaluation_open/prompt_evaluation_open_criteria_1.txt",  # 5 подп.
        "c2_outputs": "prompts_evaluation_open/prompt_evaluation_open_criteria_2.txt",  # 6 подп.
        # c3 отсутствует для open
        "c4_logic": "prompts_evaluation_open/prompt_evaluation_open_criteria_4.txt",  # 4 подп.
        "c5_phrase": "prompts_evaluation_open/prompt_evaluation_open_criteria_5.txt",  # 2 подп.
    },
    "multi": {
        "c1_question": "prompts_evaluation_multich/prompt_evaluation_multich_criteria_1.txt",  # 5
        "c2_options": "prompts_evaluation_multich/prompt_evaluation_multich_criteria_2.txt",  # 9
        "c3_outputs": "prompts_evaluation_multich/prompt_evaluation_multich_criteria_3.txt",  # 2
        "c4_logic": "prompts_evaluation_multich/prompt_evaluation_multich_criteria_4.txt",  # 4
        "c5_phrase": "prompts_evaluation_multich/prompt_evaluation_multich_criteria_5.txt",  # 2
    },
    "one": {
        "c1_question": "prompts_evaluation_onech/prompt_evaluation_onech_criteria_1.txt",  # 5
        "c2_options": "prompts_evaluation_onech/prompt_evaluation_onech_criteria_2.txt",  # 9
        "c3_outputs": "prompts_evaluation_onech/prompt_evaluation_onech_criteria_3.txt",  # 2
        "c4_logic": "prompts_evaluation_onech/prompt_evaluation_onech_criteria_4.txt",  # 3 # "c4_logic_base"
        "c5_phrase": "prompts_evaluation_onech/prompt_evaluation_onech_criteria_5.txt",  # 2
        # "c4_logic_link" добавим отдельно (1 подп.)
    },
}

# Встроенный промпт только для one/c4_logic_link (так как для него нет отдельного файла) - # в текущей версии кода этот промпт НЕ будет никогда использоваться; код удалять не буду
EXTRA_ONECH_LOGIC_LINK_PROMPT = """Ты — эксперт по оценке качества заданий.
Твоя задача — ДОПОЛНИТЕЛЬНО проверить ещё один подпункт для блока "Логическая согласованность".
А именно:
4. Логическая связанность вопроса и ответов — все варианты ответов должны быть логически связаны с вопросом и соответствовать его смыслу.

Формат ответа:
Логическая связанность вопроса и ответов 0/1

Входные данные:
"""

# Сколько подпунктов ожидаем по каждому блоку
EXPECTED_COUNTS = {
    "open": {
        "c1_question": 5,
        "c2_outputs": 6,
        "c4_logic": 4,
        "c5_phrase": 2,
    },
    # "open": {
    #     "c1_question": 4,
    #     "c2_outputs":  5,
    #     "c4_logic":    3,
    #     "c5_phrase":   2,
    # },
    "multi": {
        "c1_question": 5,
        "c2_options": 9,
        "c3_outputs": 2,
        "c4_logic": 4,
        "c5_phrase": 2,
    },
    "one": {
        "c1_question": 5,
        "c2_options": 9,
        "c3_outputs": 2,
        "c4_logic": 4,  # "c4_logic_base" "c4_logic_link"
        "c5_phrase": 2,
    },
}


def _extract_binary_vector(text: str, expected_count: int) -> List[int]:
    """Извлекает вектор 0/1 из ответа модели (обратная совместимость)."""
    scores, _ = _extract_scores_and_justifications(text, expected_count)
    return scores


_SCORE_LINE = re.compile(
    r"(?:^|\s)([01])(?:/1)?\s*(?:[-–—]\s*(.*))?$"
)
# Preferred shape: the criterion name, then its score — `question_bravity 1 — …`.
# Every criterion name in the prompts is snake_case (question_basis,
# options_right_count, …), so requiring an underscore keeps prose lines such as
# «Оценка по блоку 1» or «Критерий 1» from being read as scores. Those used to
# shift the whole vector or truncate it, silently zeroing the wrong criterion —
# or letting a zeroed critical one through.
_NAMED_SCORE_LINE = re.compile(
    r"^[\s*\-–—•>]*"
    r"\{?\s*(?P<name>[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)\s*\}?"
    r"[\s:.)\-–—]*"
    r"(?P<score>[01])(?:\s*/\s*1)?"
    r"\s*(?:[-–—]\s*(?P<just>.*))?$"
)
# Format spec line: `{question_bravity} {0/1} — {обоснование}`.
# A real answer never wraps its justification in braces.
_TEMPLATE_JUSTIFICATION = re.compile(r"\{\s*обоснование\s*\}", re.I)


def _extract_scores_and_justifications(
    text: str, expected_count: int
) -> Tuple[List[int], List[str]]:
    """Извлекает вектор 0/1 И обоснования из ответа модели.

    Ожидаемый формат строк:
        {criterion_name} 1 — обоснование
        {criterion_name} 0 — обоснование проблемы
        {criterion_name} 1/1
        {criterion_name} {1/1} — обоснование
    Если обоснования нет — возвращается пустая строка.
    Строка спецификации формата с плейсхолдером ``{обоснование}`` пропускается.
    """
    named = _scan(text, expected_count, _NAMED_SCORE_LINE, "score", "just")
    if len(named[0]) == expected_count:
        return named

    # Fall back to the positional scan for models that answer with bare scores.
    return _scan(text, expected_count, _SCORE_LINE, 1, 2)


def _scan(
    text: str,
    expected_count: int,
    pattern: "re.Pattern[str]",
    score_group,
    just_group,
) -> Tuple[List[int], List[str]]:
    scores: List[int] = []
    justifications: List[str] = []

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if _TEMPLATE_JUSTIFICATION.search(s):
            continue
        s = re.sub(r"\{([01])(?:/1)?\}", r" \1", s)
        m = pattern.search(s)
        if m:
            scores.append(int(m.group(score_group)))
            justifications.append((m.group(just_group) or "").strip())
            if len(scores) >= expected_count:
                break

    return scores[:expected_count], justifications[:expected_count]


@dataclass
class BlockResult:
    key: str
    scores: List[int]
    justifications: List[str]
    raw: str

    @property
    def total(self) -> int:
        return sum(self.scores)


class LLMValidator:
    """Основной класс валидатора."""

    def __init__(
        self,
        thresholds: Optional[Dict[str, float]] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: str = "openai",
        **provider_kwargs,
    ) -> None:
        self.thresholds = thresholds or THRESHOLDS.copy()
        self.llm = create_chat_llm(
            provider=provider,
            model_name=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0.0,
            max_tokens=512,
            **provider_kwargs,
        )

    def evaluate(
        self,
        qtype: str,
        source_text: Optional[str],
        question: Dict,
        document_kind: str = "normative",
    ) -> Dict:
        """Проверяет один вопрос и возвращает детальный отчёт + суммарные баллы."""
        qtype = qtype.lower().strip()
        if qtype not in ("open", "one", "multi"):
            raise ValueError("qtype должен быть одним из: 'open', 'one', 'multi'")

        kind = normalize_document_kind(document_kind)
        if kind not in ("normative", "textbook", "encyclopedia"):
            kind = "normative"

        effective_source = source_text or question.get("source_text") or ""
        clean_question = {
            k: v for k, v in question.items() if k != "source_text" and v is not None
        }
        question_json = json.dumps(clean_question, ensure_ascii=False, indent=2)
        blocks: List[BlockResult] = []
        raw: Dict[str, str] = {}

        if qtype == "open":
            order = ["c1_question", "c2_outputs", "c4_logic", "c5_phrase"]
        elif qtype == "multi":
            order = ["c1_question", "c2_options", "c3_outputs", "c4_logic", "c5_phrase"]
        else:  # one
            order = [
                "c1_question",
                "c2_options",
                "c3_outputs",
                "c4_logic",
                "c5_phrase",
            ]  # "c4_logic_base", "c4_logic_link"

        # Notes must NOT end with bare 0/1 — the score parser would treat them as criteria.
        textbook_context_note = (
            "\n\nДОПОЛНЕНИЕ ДЛЯ УЧЕБНИКА / КНИГИ:\n"
            "Документ — учебник или учебное пособие. Отсылка к источнику в вопросе "
            "НЕ требуется и НЕ должна присутствовать.\n"
            "Ставь question_context равным единице, если вопрос понятен сам по себе "
            "без исходного текста чанка и без упоминания главы/параграфа/страницы.\n"
            "Ставь question_context равным нулю, если:\n"
            "- вопрос ссылается на структуру книги («в параграфе 3…», «в главе…», "
            "«в главе 22 учебника»);\n"
            "- вопрос отсылает к самому чанку вместо контекста («в тексте», "
            "«согласно тексту», «согласно описанию», «согласно анализу», "
            "«описанный в тексте», «упомянутый в тексте», «указанный в тексте», "
            "«по тексту», «как описано в учебнике», «на основе приведённых фактов», "
            "«согласно изложенным фактам», «согласно приведённым данным», "
            "«в описанный период» и аналоги);\n"
            "- вопрос ссылается на позицию автора источника («по мнению автора», "
            "«как считает автор», «автор утверждает») вместо самого факта;\n"
            "- вопрос использует причастие с «как» вместо содержательного глагола "
            "(«указаны как», «названы как», «перечислены как»);\n"
            "- в Исходном тексте есть страна, период или субъект, без которых вопрос "
            "неоднозначен, а в формулировке вопроса их нет.\n"
            "Ставь question_basis равным нулю, если:\n"
            "- вопрос построен на выполнении учебного задания из Исходного текста "
            "(«Выясните…», «Узнайте…», «Вспомните…», «Составьте…», блок «Вопросы и задания», "
            "«Проверяем себя», «Практикум»): спрашивается то, что текст поручает найти, "
            "а не то, что текст сообщает;\n"
            "- в вопросе есть дата, год, число, имя, название организации или документа, "
            "которых нет в Исходном тексте (включая его первую строку-заголовок), даже если факт верен;\n"
            "- Исходный текст — подпись к иллюстрации, а вопрос — о том, что изображено, "
            "а не о фактах подписи.\n"
        )
        encyclopedia_context_note = (
            "\n\nДОПОЛНЕНИЕ ДЛЯ ЭНЦИКЛОПЕДИИ:\n"
            "Документ — энциклопедия (статьи по алфавиту). Отсылка к источнику в вопросе "
            "НЕ требуется и НЕ должна присутствовать.\n"
            "Ставь question_context равным единице, если вопрос понятен сам по себе "
            "без исходного текста чанка и без упоминания тома/страницы/заголовка "
            "соседней статьи.\n"
            "Ставь question_context равным нулю, если:\n"
            "- вопрос ссылается на структуру энциклопедии («согласно статье БРЭ…»);\n"
            "- вопрос отсылает к самому чанку вместо контекста («в тексте», "
            "«согласно тексту», «согласно описанию», «согласно анализу», "
            "«описанный в тексте», «упомянутый в тексте», «указанный в тексте», "
            "«по тексту», «как описано в статье», «на основе приведённых фактов», "
            "«согласно изложенным фактам», «согласно приведённым данным», "
            "«в описанный период» и аналоги);\n"
            "- вопрос ссылается на позицию автора источника («по мнению автора», "
            "«как считает автор», «автор утверждает») вместо самого факта;\n"
            "- вопрос использует причастие с «как» вместо содержательного глагола "
            "(«указаны как», «названы как», «перечислены как»);\n"
            "- в Исходном тексте есть страна, период или субъект, без которых вопрос "
            "неоднозначен, а в формулировке вопроса их нет.\n"
            "Ставь question_basis равным нулю, если:\n"
            "- вопрос построен на выполнении учебного задания из Исходного текста "
            "(«Выясните…», «Узнайте…», «Вспомните…», «Составьте…», блок «Вопросы и задания», "
            "«Проверяем себя», «Практикум»): спрашивается то, что текст поручает найти, "
            "а не то, что текст сообщает;\n"
            "- в вопросе есть дата, год, число, имя, название организации или документа, "
            "которых нет в Исходном тексте (включая его первую строку-заголовок), даже если факт верен;\n"
            "- Исходный текст — подпись к иллюстрации, а вопрос — о том, что изображено, "
            "а не о фактах подписи.\n"
        )

        # Ответы для прозы: выполненное задание или факт извне — не «основан на тексте».
        prose_outputs_note = (
            "\n\nДОПОЛНЕНИЕ ДЛЯ УЧЕБНИКА / ЭНЦИКЛОПЕДИИ:\n"
            "Ставь outputs_basis (для open) / options_right_basis (для one и multi) "
            "равным нулю, если правильный ответ не выводится из утверждений Исходного текста: "
            "в частности, если это ответ на задание учебника («Выясните…», «Узнайте…»), "
            "взятый из общих знаний, или если в ответе есть даты, имена или числа, "
            "которых в тексте нет.\n"
        )

        # Обрабатываем критерии последовательно для стабильности
        for key in order:
            expected = EXPECTED_COUNTS[qtype][key]

            # Получаем промпт
            if (
                qtype == "one" and key == "c4_logic_link"
            ):  # в текущей версии кода это условие НЕ будет никогда выполнено; код удалять не буду
                prompt_text = _build_prompt(
                    EXTRA_ONECH_LOGIC_LINK_PROMPT, effective_source, question_json
                )
            else:
                # Используем файлы промптов
                file_key = key
                fname = PROMPT_FILES[qtype][file_key]
                path = _find_prompt_file(fname, [Path(__file__).parent])
                template = _read_text_file(path)
                prompt_text = _build_prompt(template, effective_source, question_json)

            if kind == "textbook" and key == "c1_question":
                prompt_text = f"{prompt_text}{textbook_context_note}"
            elif kind == "encyclopedia" and key == "c1_question":
                prompt_text = f"{prompt_text}{encyclopedia_context_note}"
            elif kind in ("textbook", "encyclopedia") and key in ("c2_outputs", "c2_options"):
                prompt_text = f"{prompt_text}{prose_outputs_note}"

            # Создаем промпт и вызываем LLM
            prompt = ChatPromptTemplate.from_messages(
                [
                    SystemMessage(content="Ты — эксперт по оценке качества заданий."),
                    HumanMessage(content=prompt_text),
                ]
            )

            chain = prompt | self.llm
            answer = chain.invoke({})

            # Извлекаем ответ
            if hasattr(answer, "content"):
                answer_text = answer.content
            else:
                answer_text = str(answer)

            vec, justs = _extract_scores_and_justifications(answer_text, expected)
            if len(vec) < expected:
                logger.warning(
                    "validator: block %s parsed %d/%d scores, raw head=%r",
                    key,
                    len(vec),
                    expected,
                    answer_text[:200],
                )

            blocks.append(BlockResult(key=key, scores=vec, justifications=justs, raw=answer_text))
            raw[key] = answer_text

        # Deterministic guard: forbidden «в тексте» references for textbook/encyclopedia.
        if should_apply_source_ref_guard(kind):
            matched = find_source_reference_in_question(
                question if isinstance(question, dict) else {"task": str(question or "")}
            )
            if matched:
                for b in blocks:
                    if b.key != "c1_question":
                        continue
                    # Do not pad short vectors: a missing multiplier index already
                    # zeroes the total and keeps parse_complete honest.
                    if len(b.scores) > QUESTION_CONTEXT_INDEX:
                        b.scores[QUESTION_CONTEXT_INDEX] = 0
                        while len(b.justifications) <= QUESTION_CONTEXT_INDEX:
                            b.justifications.append("")
                        b.justifications[QUESTION_CONTEXT_INDEX] = (
                            f"запрещённая отсылка к исходному тексту: «{matched}»"
                        )
                    break

        # Взвешенная сумма + критические мультипликаторы
        parse_complete = all(
            len(b.scores) >= EXPECTED_COUNTS[qtype][b.key] for b in blocks
        )
        weighted_sum = 0.0
        multiplier_product = 1
        block_weighted: Dict[str, float] = {}
        block_max: Dict[str, float] = {}
        for b in blocks:
            expected = EXPECTED_COUNTS[qtype][b.key]
            w = WEIGHTS[qtype].get(b.key, [1.0] * expected)
            block_max[b.key] = sum(w)
            block_complete = len(b.scores) >= expected
            if block_complete:
                block_weighted[b.key] = sum(s * wt for s, wt in zip(b.scores, w))
                weighted_sum += block_weighted[b.key]
            else:
                block_weighted[b.key] = 0.0
            for idx in MULTIPLIER_INDICES.get(qtype, {}).get(b.key, []):
                if idx >= len(b.scores):
                    parse_complete = False
                    multiplier_product = 0
                    break
                multiplier_product *= b.scores[idx]
            if not parse_complete and multiplier_product == 0:
                break

        total = weighted_sum * multiplier_product if parse_complete else 0.0
        max_total = MAX_POINTS[qtype]
        passed = bool(parse_complete and total >= self.thresholds[qtype])

        by_block: Dict[str, List[int]] = {b.key: b.scores for b in blocks}
        justifications: Dict[str, List[str]] = {b.key: b.justifications for b in blocks}

        return {
            "type": qtype,
            "by_block": by_block,
            "block_weighted": block_weighted,
            "block_max": block_max,
            "justifications": justifications,
            "raw": raw,
            "total": total,
            "max_total": max_total,
            "threshold": self.thresholds[qtype],
            "passed": passed,
            "parse_complete": parse_complete,
        }
