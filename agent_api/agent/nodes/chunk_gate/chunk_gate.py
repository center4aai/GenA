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
from llm_factory import create_chat_llm

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "gate_prompt.txt"


class ChunkGateInput(TypedDict):
    chunk: str
    question_type: str  # "open", "one", "multi"


class ChunkGateOutput(BaseModel):
    c1_chunk_informative: List[int] = Field(description="[0] или [1]")
    c1_reasoning: str = Field(default="")
    c1_confidence: float = Field(default=0.0)
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


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _loads_lenient(cleaned: str) -> Dict[str, Any]:
    """Parse an LLM JSON/dict response as robustly as possible.

    Strategy (least destructive first):
      1. Strict JSON — correct for well-formed double-quoted output and keeps
         apostrophes/quotes inside reasoning strings intact.
      2. Same, but on the outermost ``{...}`` block (drops any surrounding prose).
      3. ``ast.literal_eval`` — safely parses Python-dict-style output with single
         quotes without mangling quotes inside string values, and tolerates
         trailing commas.
      4. Last resort: naive single→double quote swap (legacy behaviour). This can
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
            obj = ast.literal_eval(candidate)
            if isinstance(obj, dict):
                return obj
        except (ValueError, SyntaxError):
            pass

    # Legacy fallback — may mangle quotes inside strings, so it runs last.
    return json.loads(candidates[-1].replace("'", '"'))


def _parse_gate_response(raw: str) -> Dict[str, Any]:
    """Parse LLM JSON response, tolerating single-quoted dicts and stray prose."""
    cleaned = _strip_markdown_fences(raw)

    data = _loads_lenient(cleaned)

    for key in ("c1_chunk_informative", "c2_chunk_reference_clarity", "c3_chunk_multi_suitability"):
        val = data.get(key)
        if isinstance(val, int):
            data[key] = [val]
        elif isinstance(val, list) and len(val) == 1:
            data[key] = [int(val[0])]
        else:
            data[key] = [0]

    for key in ("c1_confidence", "c2_confidence", "c3_confidence"):
        try:
            data[key] = float(data.get(key, 0.0))
        except (TypeError, ValueError):
            data[key] = 0.0

    for key in ("c1_reasoning", "c2_reasoning", "c3_reasoning"):
        data.setdefault(key, "")

    return data


def _decide(parsed: Dict[str, Any], question_type: str) -> tuple[bool, Optional[str]]:
    c1 = parsed.get("c1_chunk_informative", [0])[0]
    c2 = parsed.get("c2_chunk_reference_clarity", [0])[0]
    c3 = parsed.get("c3_chunk_multi_suitability", [0])[0]

    if c1 == 0:
        return False, "chunk_not_informative"
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
                max_tokens=512,
                timeout=60,
                **provider_kwargs,
            )
            self.system_prompt = _read_gate_prompt()

        def invoke(self, input_data: ChunkGateInput, config=None) -> ChunkGateOutput:
            chunk = input_data["chunk"]
            question_type = input_data["question_type"]

            user_text = f"Текстовый чанк для оценки:\n\n{chunk}"

            prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=user_text),
            ])

            chain = prompt | self.llm
            answer = chain.invoke({})

            raw_text = answer.content if hasattr(answer, "content") else str(answer)
            logger.info(f"Chunk gate raw response length: {len(raw_text)}")

            try:
                parsed = _parse_gate_response(raw_text)
            except (json.JSONDecodeError, KeyError, IndexError) as exc:
                logger.warning(f"Chunk gate JSON parse failed ({exc}), rejecting chunk conservatively")
                parsed = {
                    "c1_chunk_informative": [0],
                    "c1_reasoning": f"parse_error: {exc}",
                    "c1_confidence": 0.0,
                    "c2_chunk_reference_clarity": [0],
                    "c2_reasoning": "",
                    "c2_confidence": 0.0,
                    "c3_chunk_multi_suitability": [0],
                    "c3_reasoning": "",
                    "c3_confidence": 0.0,
                }

            passed, reason = _decide(parsed, question_type)

            logger.info(
                f"Chunk gate: passed={passed}, reason={reason}, "
                f"c1={parsed['c1_chunk_informative']}, c2={parsed['c2_chunk_reference_clarity']}, "
                f"c3={parsed['c3_chunk_multi_suitability']}"
            )
            logger.info(
                "Chunk gate details: %s",
                json.dumps(
                    {
                        "passed": passed,
                        "rejection_reason": reason,
                        "c1_reasoning": parsed.get("c1_reasoning", ""),
                        "c1_confidence": parsed.get("c1_confidence", 0.0),
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
                c2_chunk_reference_clarity=parsed["c2_chunk_reference_clarity"],
                c2_reasoning=parsed.get("c2_reasoning", ""),
                c2_confidence=parsed.get("c2_confidence", 0.0),
                c3_chunk_multi_suitability=parsed["c3_chunk_multi_suitability"],
                c3_reasoning=parsed.get("c3_reasoning", ""),
                c3_confidence=parsed.get("c3_confidence", 0.0),
                passed=passed,
                rejection_reason=reason,
            )

    return ChunkGateRunnable()
