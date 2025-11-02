"""
LLM-валидатор вопросов по критериям качества.
Поддерживает три типа заданий: open, onech, multich.
Адаптирован для использования с Ollama через LangChain.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Literal
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from config import LLM_MODEL_NAME, LLM_URL_MODEL, LLM_API_KEY

def _read_text_file(path: Path) -> str:
    """Читает текстовый файл с промптом."""
    with path.open("r", encoding="utf-8") as f:
        return f.read()

def _find_prompt_file(file_name: str, search_roots: Optional[List[Path]] = None) -> Path:
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
    return (
        template.rstrip()
        + "\n\nИсходный текст: \n\n"
        + (source_text or "")
        + "\n\nЗадание: \n"
        + question_json
        + "\n"
    )

# --------------------------- Константы и конфиг ---------------------------

THRESHOLDS = {
    "open": 15,    # 15 -> 16 -> 15 -> 12 -> 15
    "one": 20,     
    "multi": 20,   
}

# Максимальные баллы по типам
MAX_POINTS = {
    "open": 17, # 17 -> 14-> 17
    "one": 22,
    "multi": 22,
}

# Файлы шаблонов промптов (1 критерий == 1 промпт)
PROMPT_FILES = {
    "open": {
        "c1_question": "prompts_evaluation_open/prompt_evaluation_open_criteria_1.txt",   # 5 подп.
        "c2_outputs":  "prompts_evaluation_open/prompt_evaluation_open_criteria_2.txt",   # 6 подп.
        # c3 отсутствует для open
        "c4_logic":    "prompts_evaluation_open/prompt_evaluation_open_criteria_4.txt",   # 4 подп.
        "c5_phrase":   "prompts_evaluation_open/prompt_evaluation_open_criteria_5.txt",   # 2 подп.
    },
    "multi": {
        "c1_question": "prompts_evaluation_multich/prompt_evaluation_multich_criteria_1.txt",  # 5
        "c2_options":  "prompts_evaluation_multich/prompt_evaluation_multich_criteria_2.txt",  # 9
        "c3_outputs":  "prompts_evaluation_multich/prompt_evaluation_multich_criteria_3.txt",  # 2
        "c4_logic":    "prompts_evaluation_multich/prompt_evaluation_multich_criteria_4.txt",  # 4
        "c5_phrase":   "prompts_evaluation_multich/prompt_evaluation_multich_criteria_5.txt",  # 2
    },
    "one": {
        "c1_question": "prompts_evaluation_onech/prompt_evaluation_onech_criteria_1.txt",     # 5
        "c2_options":  "prompts_evaluation_onech/prompt_evaluation_onech_criteria_2.txt",     # 9
        "c3_outputs":  "prompts_evaluation_onech/prompt_evaluation_onech_criteria_3.txt",     # 2
        "c4_logic": "prompts_evaluation_onech/prompt_evaluation_onech_criteria_4.txt",   # 3 # "c4_logic_base"
        "c5_phrase":     "prompts_evaluation_onech/prompt_evaluation_onech_criteria_5.txt",   # 2
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

Исходный текст:
{source_text}

Задание (JSON):
{question_json}
"""

# Сколько подпунктов ожидаем по каждому блоку
EXPECTED_COUNTS = {
    "open": {
        "c1_question": 5,
        "c2_outputs":  6,
        "c4_logic":    4,
        "c5_phrase":   2,
    },
    # "open": {
    #     "c1_question": 4,
    #     "c2_outputs":  5,
    #     "c4_logic":    3,
    #     "c5_phrase":   2,
    # },
    "multi": {
        "c1_question": 5,
        "c2_options":  9,
        "c3_outputs":  2,
        "c4_logic":    4,
        "c5_phrase":   2,
    },
    "one": {
        "c1_question":   5,
        "c2_options":    9,
        "c3_outputs":    2,
        "c4_logic":      4, # "c4_logic_base" "c4_logic_link"
        "c5_phrase":     2,
    },
}

def _extract_binary_vector(text: str, expected_count: int) -> List[int]:
    """Извлекает вектор 0/1 из ответа модели.
    Основная стратегия — читать построчно, брать завершающую 0/1.
    собрать все одиночные 0/1 в тексте.
    Недостающие значения добиваются нулями.
    """
    vals: List[int] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.search(r"([01])\s*$", s)
        if m:
            vals.append(int(m.group(1)))
            if len(vals) >= expected_count:
                return vals[:expected_count]

    # Альтернативная стратегия: найти все одиночные 0/1
    for m in re.findall(r"(?<!\d)([01])(?!\d)", text):
        vals.append(int(m))
        if len(vals) >= expected_count:
            return vals[:expected_count]

    # Добиваем нулями если не хватает
    if len(vals) < expected_count:
        vals.extend([0] * (expected_count - len(vals)))
    return vals[:expected_count]

@dataclass
class BlockResult:
    key: str
    scores: List[int]
    raw: str

    @property
    def total(self) -> int:
        return sum(self.scores)

class LLMValidator:
    """Основной класс валидатора."""

    def __init__(
        self,
        thresholds: Optional[Dict[str, int]] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        # Пороговые значения
        self.thresholds = thresholds or THRESHOLDS.copy()
        # LLM
        self.llm = ChatOpenAI(
            model=model or LLM_MODEL_NAME,
            temperature=0.0,
            openai_api_base=base_url or LLM_URL_MODEL,
            openai_api_key=api_key or LLM_API_KEY,
            max_retries=3,
            stream=False,  # Отключаем стриминг для получения полного ответа сразу
            timeout=30,    # Увеличиваем timeout для больших промптов
            max_tokens=512 # Ограничиваем длину ответа для ускорения
        )

    def evaluate(
        self,
        qtype: str,
        source_text: Optional[str],
        question: Dict,
    ) -> Dict:
        """Проверяет один вопрос и возвращает детальный отчёт + суммарные баллы."""
        qtype = qtype.lower().strip()
        if qtype not in ("open", "one", "multi"):
            raise ValueError("qtype должен быть одним из: 'open', 'one', 'multi'")

        question_json = json.dumps(question, ensure_ascii=False, indent=2)
        blocks: List[BlockResult] = []
        raw: Dict[str, str] = {}

        if qtype == "open":
            order = ["c1_question", "c2_outputs", "c4_logic", "c5_phrase"]
        elif qtype == "multi":
            order = ["c1_question", "c2_options", "c3_outputs", "c4_logic", "c5_phrase"]
        else:  # one
            order = ["c1_question", "c2_options", "c3_outputs", "c4_logic", "c5_phrase"] # "c4_logic_base", "c4_logic_link"

        # Обрабатываем критерии последовательно для стабильности
        for key in order:
            expected = EXPECTED_COUNTS[qtype][key]
            
            # Получаем промпт
            if qtype == "one" and key == "c4_logic_link": # в текущей версии кода это условие НЕ будет никогда выполнено; код удалять не буду
                # Для one/c4_logic_link используем встроенный промпт
                prompt_text = EXTRA_ONECH_LOGIC_LINK_PROMPT.format(
                    source_text=source_text or "",
                    question_json=question_json,
                )
            else:
                # Используем файлы промптов
                file_key = key
                fname = PROMPT_FILES[qtype][file_key]
                path = _find_prompt_file(fname, [Path(__file__).parent])
                template = _read_text_file(path)
                prompt_text = _build_prompt(template, source_text or "", question_json)

            # Создаем промпт и вызываем LLM
            prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content="Ты — эксперт по оценке качества заданий."),
                HumanMessage(content=prompt_text)
            ])
            
            chain = prompt | self.llm
            answer = chain.invoke({})
            
            # Извлекаем ответ
            if hasattr(answer, 'content'):
                answer_text = answer.content
            else:
                answer_text = str(answer)
                
            vec = _extract_binary_vector(answer_text, expected)

            blocks.append(BlockResult(key=key, scores=vec, raw=answer_text))
            raw[key] = answer_text

        # Подсчитываем общий балл
        total = sum(b.total for b in blocks)
        max_total = MAX_POINTS[qtype]
        passed = bool(total >= self.thresholds[qtype])

        # Сбор ответа
        by_block: Dict[str, List[int]] = {b.key: b.scores for b in blocks}

        return {
            "type": qtype,
            "by_block": by_block,
            "raw": raw,
            "total": total,
            "max_total": max_total,
            "threshold": self.thresholds[qtype],
            "passed": passed,
        }
