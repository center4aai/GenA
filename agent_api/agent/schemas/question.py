from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field


class StructuredQuestionOutput(BaseModel):
    task: str = Field(description="Текст вопроса.")
    text: Optional[str] = Field(
        default=None, description="Дополнительный контекст вопроса."
    )
    option_1: Optional[str] = Field(default=None, description="Вариант ответа 1.")
    option_2: Optional[str] = Field(default=None, description="Вариант ответа 2.")
    option_3: Optional[str] = Field(default=None, description="Вариант ответа 3.")
    option_4: Optional[str] = Field(default=None, description="Вариант ответа 4.")
    option_5: Optional[str] = Field(default=None, description="Вариант ответа 5.")
    option_6: Optional[str] = Field(default=None, description="Вариант ответа 6.")
    option_7: Optional[str] = Field(default=None, description="Вариант ответа 7.")
    option_8: Optional[str] = Field(default=None, description="Вариант ответа 8.")
    option_9: Optional[str] = Field(default=None, description="Вариант ответа 9.")
    outputs: Union[int, str] = Field(
        description=(
            "Правильный ответ: для типов 'one' - номер варианта (1-9), "
            "'multi' - номера через запятую без пробелов, 'open' - текст ответа."
        )
    )
    source_text: Optional[str] = Field(
        default=None, description="Исходный текст, по которому был сгенерирован вопрос."
    )


_EMPTY_CONTEXT_VALUES = frozenset({None, "", "None", "null", "NULL"})


def format_question_context(text: Optional[str]) -> str:
    """Return question context for assessment prompts, treating sentinel empties as missing."""
    if text is None:
        return "Нет дополнительного контекста"
    stripped = str(text).strip()
    if stripped in _EMPTY_CONTEXT_VALUES:
        return "Нет дополнительного контекста"
    return stripped


def fence_user_content(text: str, label: str = "USER_CONTENT") -> str:
    """Wrap untrusted user text in explicit delimiters for prompt injection fencing."""
    body = text or ""
    opening = f"<<{label}>>"
    closing = f"<</{label}>>"
    # Neutralize embedded fence tags so user content cannot break out of the fence.
    body = body.replace(opening, f"< <{label}> >")
    body = body.replace(closing, f"< /{label}> >")
    return f"{opening}\n{body}\n{closing}"


_EMPTY_OPTION_VALUES = frozenset({None, "", "None", "null", "NULL"})


def serialize_question_options(question: Any) -> str:
    """Serialize option_* fields from a question dict or model for assessment prompts."""
    if hasattr(question, "model_dump"):
        data = question.model_dump()
    elif isinstance(question, dict):
        data = question
    else:
        return "Нет вариантов ответа"

    lines = []
    for i in range(1, 10):
        key = f"option_{i}"
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text in _EMPTY_OPTION_VALUES:
            continue
        lines.append(f"{key}: {text}")
    return "\n".join(lines) if lines else "Нет вариантов ответа"
