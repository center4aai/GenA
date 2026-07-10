"""
Человекочитаемое отображение результатов LLM-валидации (критерии + счётчики).
Синхронизируйте MAX_REFINE_ATTEMPTS с agent_api/agent/assistant_graph.py MAX_REFINE_RETRIES.
"""
from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional

# Должно совпадать с MAX_REFINE_RETRIES в assistant_graph.py
MAX_REFINE_ATTEMPTS = 2

VALIDATION_BLOCK_LABELS: Dict[str, str] = {
    "c1_question": "Формулировка вопроса",
    "c2_outputs": "Формулировка ответа",
    "c2_options": "Варианты ответов",
    "c3_outputs": "Соответствие правильных ответов",
    "c4_logic": "Логическая согласованность",
    "c5_phrase": "Корректность формулировки",
}


def block_label(block_key: str) -> str:
    return VALIDATION_BLOCK_LABELS.get(block_key, block_key)


def parse_validation_details(raw: Any) -> Dict[str, Any]:
    """Приводит validation_details из БД/строки к dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            parsed = ast.literal_eval(s)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def parse_validation_justifications(raw: Any) -> Dict[str, List[str]]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {k: list(v) if isinstance(v, (list, tuple)) else [] for k, v in raw.items()}
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, dict):
                return {
                    k: list(v) if isinstance(v, (list, tuple)) else []
                    for k, v in parsed.items()
                }
        except Exception:
            pass
    return {}


def format_validation_breakdown_md(
    by_block: Dict[str, Any],
    justifications: Optional[Dict[str, List[str]]] = None,
) -> str:
    """
    Markdown: блоки с метками, счёт X/Y и краткие пояснения только для оценок 0.
    """
    justifications = justifications or {}
    lines: List[str] = []
    for block_key, scores in by_block.items():
        label = block_label(block_key)
        if not isinstance(scores, list):
            lines.append(f"- **{label}**: {scores}")
            continue
        try:
            total = sum(int(x) for x in scores)
        except (TypeError, ValueError):
            total = sum(scores) if scores else 0
        lines.append(f"- **{label}**: {total}/{len(scores)}")
        block_justs = justifications.get(block_key, [])
        for i, sc in enumerate(scores):
            if sc == 0 and i < len(block_justs):
                j = (block_justs[i] or "").strip()
                if j:
                    lines.append(f"  - ({i + 1}) {j}")
    return "\n".join(lines) if lines else "_Нет данных_"


def format_retry_line(retry_count: Any) -> str:
    """Текст про число попыток доработки после валидации."""
    try:
        n = int(retry_count)
    except (TypeError, ValueError):
        n = 0
    return f"**Refinement attempts:** {n}/{MAX_REFINE_ATTEMPTS}"
