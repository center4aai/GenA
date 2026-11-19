"""
Chunk validation gate — фильтрует чанки, непригодные для генерации вопросов.

Оценивает чанк по трём бинарным критериям (LLM-вызов):
  c1_chunk_informative       — содержит ли чанк полезную информацию
  c2_chunk_reference_clarity — понятен ли чанк без внешних источников
  c3_chunk_multi_suitability — подходит ли для вопросов с множественным выбором

Логика маршрутизации:
  c1=[0] ИЛИ c2=[0]                         → чанк отклоняется целиком
  question_type=="multi" И c3=[0]            → чанк отклоняется для мульти
  иначе                                      → чанк проходит дальше
"""

import ast
import json
import re
import logging
from pathlib import Path
from typing import Dict, List, Optional, TypedDict, Any

from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from config import GATE_LLM_TIMEOUT
from llm_factory import TRANSIENT_LLM_ERRORS, create_chat_llm, transient_llm_error_kind
from agent.document_kinds import normalize_document_kind

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "gate_prompt.txt"
# Kind-specific addenda (textbook apparatus, encyclopedia front matter). Only
# these kinds get an addendum; normative keeps the base prompt byte-for-byte.
_KIND_PROMPT_FILES = {
    "textbook": Path(__file__).parent / "GATE_KIND_TEXTBOOK.txt",
    "encyclopedia": Path(__file__).parent / "GATE_KIND_ENCYCLOPEDIA.txt",
}
KIND_LABELS_RU = {"textbook": "учебник", "encyclopedia": "энциклопедия"}
# Mirrors chunker/blocks.py BLOCK_TYPE_LABELS_RU (kept types only).
BLOCK_TYPE_LABELS_RU = {
    "body": "основной текст",
    "list": "основной текст (перечень)",
    "inset": "врезка",
    "caption": "подпись к иллюстрации",
    "glossary_entry": "словарная статья",
}
# c1_category values the kind addenda may return; anything else → None.
_C1_CATEGORIES = frozenset({"content", "exercise", "front_matter", "caption_only"})
_CATEGORY_REASONS = {
    "exercise": "chunk_exercise_block",
    "front_matter": "chunk_front_matter",
}


# Обратная совместимость: раньше классификация жила здесь.
_TRANSIENT_LLM_ERRORS = TRANSIENT_LLM_ERRORS
__all__ = [
    "ChunkGateInput",
    "ChunkGateOutput",
    "GateParseError",
    "build_gate_messages",
    "create_chunk_gate_chain",
    "synthetic_gate_pass",
    "transient_llm_error_kind",
]

_PARSE_ERROR_SNIPPET_LEN = 300


class GateParseError(ValueError):
    """LLM ответил, но ответ не разобрать как вердикт gate.

    Это не отсев чанка (вердикта нет), а сбой gate: API отдаёт его как 500 с
    константным detail, worker ретраит/помечает задачу, чанк не теряется.
    """

    def __init__(self, cause: BaseException, raw_text: str):
        snippet = (raw_text or "")[:_PARSE_ERROR_SNIPPET_LEN]
        super().__init__(f"{type(cause).__name__}: {cause}; raw={snippet!r}")
        self.cause = cause
        self.raw_snippet = snippet


class _ChunkGateInputRequired(TypedDict):
    chunk: str
    question_type: str  # "open", "one", "multi"


class ChunkGateInput(_ChunkGateInputRequired, total=False):
    document_kind: str  # normative | textbook | encyclopedia (default normative)
    block_type: Optional[str]  # body | list | inset | caption | glossary_entry


def synthetic_gate_pass(rejection_reason: str) -> Dict[str, Any]:
    """Gate payload when the check is skipped (pre-validated, disabled, unconfigured)."""
    return {
        "passed": True,
        "rejection_reason": rejection_reason,
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }


class ChunkGateOutput(BaseModel):
    c1_chunk_informative: List[int] = Field(description="[0] или [1]")
    c1_reasoning: str = Field(default="")
    c1_confidence: float = Field(default=0.0)
    # Only for textbook/encyclopedia: why c1 is 0 (exercise, front_matter,
    # caption_only) or "content" when c1 is 1. None for normative.
    c1_category: Optional[str] = Field(default=None)
    c2_chunk_reference_clarity: List[int] = Field(description="[0] или [1]")
    c2_reasoning: str = Field(default="")
    c2_confidence: float = Field(default=0.0)
    c3_chunk_multi_suitability: List[int] = Field(description="[0] или [1]")
    c3_reasoning: str = Field(default="")
    c3_confidence: float = Field(default=0.0)
    passed: bool = Field(description="Прошёл ли чанк gate")
    rejection_reason: Optional[str] = Field(default=None)


def _read_gate_prompt() -> str:
    with _PROMPT_PATH.open("r", encoding="utf-8") as f:
        return f.read()


def _read_kind_addenda() -> Dict[str, str]:
    addenda: Dict[str, str] = {}
    for kind, path in _KIND_PROMPT_FILES.items():
        try:
            addenda[kind] = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Gate kind addendum for %s not loaded: %s", kind, exc)
    return addenda


# Копия chunker/text_utils.boundary_flags: gate получает только текст чанка, а
# менять контракт задач трёх сервисов ради двух булевых флагов не стоит.
_BOUNDARY_SENTENCE_END_RE = re.compile(r'[.!?…]\s*["»)\]]?$')
_BOUNDARY_LOWER_START_RE = re.compile(r'^[«"„(\[\s]*[а-яёa-z]')
_BOUNDARY_LIST_ITEM_RE = re.compile(r'^\s*(?:\(?\d{1,2}[.)]|\(?[а-яa-z][.)]|[•●▪■◆*–—-])\s+\S')
_CONTEXT_LINE_MAX = 300


def _chunk_body_text(chunk: str) -> str:
    """Текст чанка без контекстной строки «Глава … > § …» (первый абзац без точки)."""
    text = (chunk or "").strip()
    head, sep, rest = text.partition("\n\n")
    if sep and rest.strip() and len(head) <= _CONTEXT_LINE_MAX and not _BOUNDARY_SENTENCE_END_RE.search(head.rstrip()):
        return rest.strip()
    return text


def _boundary_flags(chunk: str) -> Dict[str, bool]:
    t = _chunk_body_text(chunk)
    if not t:
        return {"starts_mid_sentence": False, "ends_mid_sentence": False}
    starts = bool(_BOUNDARY_LOWER_START_RE.match(t)) and not _BOUNDARY_LIST_ITEM_RE.match(t)
    ends = not _BOUNDARY_SENTENCE_END_RE.search(t.rstrip()) and not t.endswith(":")
    return {"starts_mid_sentence": starts, "ends_mid_sentence": ends}


def boundary_check_line(chunk: str) -> str:
    """Строка «Автоматическая проверка границ: …» для заголовка, пустая если обрывов нет."""
    flags = _boundary_flags(chunk)
    parts = []
    if flags["starts_mid_sentence"]:
        parts.append("начало — обрыв предложения")
    if flags["ends_mid_sentence"]:
        parts.append("конец — обрыв предложения")
    return f"Автоматическая проверка границ: {'; '.join(parts)}\n" if parts else ""


def build_gate_messages(
    base_prompt: str,
    kind_addenda: Dict[str, str],
    chunk: str,
    document_kind: Optional[str] = None,
    block_type: Optional[str] = None,
) -> tuple[str, str]:
    """(system, human) for the gate call.

    For ``normative`` both strings are exactly what the gate always sent, so
    legal-document runs do not change. Textbook / encyclopedia get the kind
    addendum appended to the system prompt and a header naming the document
    kind and (if known) the block type in the human message.
    """
    kind = normalize_document_kind(document_kind)
    system = base_prompt
    addendum = (kind_addenda.get(kind) or "").strip()
    if addendum:
        system = f"{system.rstrip()}\n\n{addendum}\n"
    header = ""
    kind_label = KIND_LABELS_RU.get(kind)
    if kind_label:
        header = f"Тип документа: {kind_label}\n"
        block_label = BLOCK_TYPE_LABELS_RU.get((block_type or "").strip().lower())
        if block_label:
            header += f"Тип блока: {block_label}\n"
        # Детерминированный признак обрыва начала/конца — только для прозы
        # (учебник/энциклопедия) и только когда сработал.
        header += boundary_check_line(chunk)
        header += "\n"
    user_text = f"{header}Текстовый чанк для оценки:\n\n{chunk}"
    return system, user_text


def _normalize_category(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip().lower().replace("-", "_").replace(" ", "_")
    return v if v in _C1_CATEGORIES else None


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# A member that ends a line (closing quote/bracket, digit, literal) directly
# followed by a line that opens the next key — the comma between them is missing.
_MISSING_COMMA_RE = re.compile(r'([\]\}"0-9]|true|false|null)([ \t]*\n\s*)(")')
# Only before a closing bracket of a list or the document's final brace — a
# ", }" inside a reasoning string is left alone.
_TRAILING_COMMA_RE = re.compile(r",(\s*\])|,(\s*\}\s*$)")


def _repair_json(text: str) -> str:
    """Fix the two slips a deterministic model keeps making in gate JSON.

    Qwen at temperature 0 drops the comma after a long ``*_reasoning`` string
    (seen on 5–10 % of gate calls) and, less often, leaves a trailing one before
    ``}``; since the same chunk yields the same text on retry, the worker's
    retry cannot help. JSON strings may not contain raw newlines, so a newline
    between a value terminator and an opening quote can only be a member
    boundary — inserting a comma there never touches a valid document.
    """
    repaired = _MISSING_COMMA_RE.sub(r"\1,\2\3", text)
    return _TRAILING_COMMA_RE.sub(lambda m: m.group(1) or m.group(2), repaired)


def _loads_lenient(cleaned: str) -> Dict[str, Any]:
    """Parse an LLM JSON/dict response as robustly as possible.

    Strategy (least destructive first):
      1. Strict JSON — correct for well-formed double-quoted output and keeps
         apostrophes/quotes inside reasoning strings intact.
      2. Same, but on the outermost ``{...}`` block (drops any surrounding prose).
      3. Strict JSON after ``_repair_json`` — a missing comma between members
         or a trailing one before a closing bracket.
      4. ``ast.literal_eval`` — safely parses Python-dict-style output with single
         quotes without mangling quotes inside string values, and tolerates
         trailing commas.
      5. Last resort: naive single→double quote swap (legacy behaviour). This can
         corrupt quotes inside strings, so it only runs when everything else fails.
    """
    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match and match.group(0) != cleaned:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_repair_json(candidate))
        except json.JSONDecodeError:
            pass
        try:
            obj = ast.literal_eval(candidate)
            if isinstance(obj, dict):
                return obj
        except (ValueError, SyntaxError):
            pass

    # Legacy fallback — may mangle quotes inside strings, so it runs last.
    return json.loads(candidates[-1].replace("'", '"'))


def _normalize_gate_score(val: Any) -> List[int]:
    """Normalize LLM gate criterion to [0] or [1]."""
    if isinstance(val, bool):
        return [int(val)]
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            return [1 if int(val) == 1 else 0]
        except (TypeError, ValueError, OverflowError):
            return [0]
    if isinstance(val, str):
        s = val.strip()
        if s in ("1", "true", "True"):
            return [1]
        if s in ("0", "false", "False"):
            return [0]
        try:
            return [1 if int(float(s)) == 1 else 0]
        except (TypeError, ValueError):
            return [0]
    if isinstance(val, list) and val:
        try:
            return [1 if int(val[0]) == 1 else 0]
        except (TypeError, ValueError):
            pass
    return [0]


def _parse_gate_response(raw: str) -> Dict[str, Any]:
    """Parse LLM JSON response, tolerating single-quoted dicts and stray prose."""
    cleaned = _strip_markdown_fences(raw)

    data = _loads_lenient(cleaned)
    if not isinstance(data, dict):
        raise TypeError(f"Expected dict gate response, got {type(data).__name__}")

    for key in ("c1_chunk_informative", "c2_chunk_reference_clarity", "c3_chunk_multi_suitability"):
        if key not in data:
            raise KeyError(key)
        data[key] = _normalize_gate_score(data[key])

    for key in ("c1_confidence", "c2_confidence", "c3_confidence"):
        try:
            data[key] = float(data.get(key, 0.0))
        except (TypeError, ValueError):
            data[key] = 0.0

    for key in ("c1_reasoning", "c2_reasoning", "c3_reasoning"):
        data.setdefault(key, "")

    data["c1_category"] = _normalize_category(data.get("c1_category"))

    return data


def _gate_output_from_parsed(
    parsed: Dict[str, Any],
    question_type: str,
) -> ChunkGateOutput:
    passed, reason = _decide(parsed, question_type)

    logger.info(
        "Chunk gate: passed=%s, reason=%s, c1=%s, c2=%s, c3=%s",
        passed,
        reason,
        parsed["c1_chunk_informative"],
        parsed["c2_chunk_reference_clarity"],
        parsed["c3_chunk_multi_suitability"],
    )
    logger.info(
        "Chunk gate details: %s",
        json.dumps(
            {
                "passed": passed,
                "rejection_reason": reason,
                "c1_reasoning": parsed.get("c1_reasoning", ""),
                "c1_confidence": parsed.get("c1_confidence", 0.0),
                "c1_category": parsed.get("c1_category"),
                "c2_reasoning": parsed.get("c2_reasoning", ""),
                "c2_confidence": parsed.get("c2_confidence", 0.0),
                "c3_reasoning": parsed.get("c3_reasoning", ""),
                "c3_confidence": parsed.get("c3_confidence", 0.0),
            },
            ensure_ascii=False,
        ),
    )

    return ChunkGateOutput(
        c1_chunk_informative=parsed["c1_chunk_informative"],
        c1_reasoning=parsed.get("c1_reasoning", ""),
        c1_confidence=parsed.get("c1_confidence", 0.0),
        c1_category=parsed.get("c1_category"),
        c2_chunk_reference_clarity=parsed["c2_chunk_reference_clarity"],
        c2_reasoning=parsed.get("c2_reasoning", ""),
        c2_confidence=parsed.get("c2_confidence", 0.0),
        c3_chunk_multi_suitability=parsed["c3_chunk_multi_suitability"],
        c3_reasoning=parsed.get("c3_reasoning", ""),
        c3_confidence=parsed.get("c3_confidence", 0.0),
        passed=passed,
        rejection_reason=reason,
    )


def _decide(parsed: Dict[str, Any], question_type: str) -> tuple[bool, Optional[str]]:
    c1 = parsed.get("c1_chunk_informative", [0])[0]
    c2 = parsed.get("c2_chunk_reference_clarity", [0])[0]
    c3 = parsed.get("c3_chunk_multi_suitability", [0])[0]

    if c1 == 0:
        # Kind addenda classify why: exercises and front matter get their own
        # reason so the statistics show what the textbook apparatus costs.
        reason = _CATEGORY_REASONS.get(parsed.get("c1_category") or "")
        return False, reason or "chunk_not_informative"
    if c2 == 0:
        return False, "chunk_not_self_contained"
    if question_type == "multi" and c3 == 0:
        return False, "chunk_not_suitable_for_multi"
    return True, None


def create_chunk_gate_chain(
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    provider: str = "openai",
    **provider_kwargs,
) -> Runnable:

    class ChunkGateRunnable(Runnable[ChunkGateInput, ChunkGateOutput]):
        def __init__(self):
            self.llm = create_chat_llm(
                provider=provider,
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                temperature=0.0,
                max_tokens=1024,
                timeout=GATE_LLM_TIMEOUT,
                # Ретраит очередь (worker); SDK-ретраи здесь лишь множили
                # нагрузку на и так занятый LLM-хост (до 12 запросов на чанк).
                max_retries=0,
                **provider_kwargs,
            )
            self.system_prompt = _read_gate_prompt()
            self.kind_addenda = _read_kind_addenda()

        def invoke(self, input_data: ChunkGateInput, config=None) -> ChunkGateOutput:
            chunk = input_data["chunk"]
            question_type = input_data["question_type"]

            system_text, user_text = build_gate_messages(
                self.system_prompt,
                self.kind_addenda,
                chunk,
                input_data.get("document_kind"),
                input_data.get("block_type"),
            )

            prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=system_text),
                HumanMessage(content=user_text),
            ])

            chain = prompt | self.llm
            # Любая ошибка LLM (транзиентная или 400/404) — не вердикт по чанку:
            # пробрасываем, API-слой выберет статус, очередь ретраит.
            answer = chain.invoke({})

            raw_text = answer.content if hasattr(answer, "content") else str(answer)
            logger.info(f"Chunk gate raw response length: {len(raw_text)}")

            try:
                parsed = _parse_gate_response(raw_text)
            except (json.JSONDecodeError, KeyError, IndexError, ValueError, TypeError) as exc:
                logger.warning(f"Chunk gate JSON parse failed ({exc})")
                raise GateParseError(exc, raw_text) from exc

            return _gate_output_from_parsed(parsed, question_type)

    return ChunkGateRunnable()
