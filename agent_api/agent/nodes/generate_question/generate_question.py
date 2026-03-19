from typing import Literal, Dict, Optional, TypedDict, List, Union
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
import logging
from agent.nodes.generate_question.system_prompt import (
    PROMPT_TEMPLATE_ONE,
    PROMPT_TEMPLATE_MULTI,
    PROMPT_TEMPLATE_OPEN,
    SYSTEM_PROMPT_ONE,
    SYSTEM_PROMPT_MULTI,
    SYSTEM_PROMPT_OPEN,
)
from config import LLM_MODEL_NAME, LLM_URL_MODEL, LLM_API_KEY
from langchain_core.output_parsers import PydanticOutputParser

try:
    from langdetect import detect, LangDetectException
except ImportError:
    detect = None
    LangDetectException = Exception

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


QuestionType = Literal["one", "multi", "open"]


class GenerateQuestionInput(TypedDict):
    input_text: str
    question_type: QuestionType
    source: Optional[str]
    language: Optional[str]  # ru, be, tg


class StructuredQuestionOutput(BaseModel):
    task: str = Field(description="Текст вопроса.")
    text: Optional[str] = Field(
        default=None, description="Дополнительный контекст вопроса."
    )
    option_1: Optional[str] = Field(default=None, description="Вариант ответа 1.")
    option_2: Optional[str] = Field(default=None, description="Вариант ответа 2.")
    option_3: Optional[str] = Field(default=None, description="Вариант ответа 3.")
    option_4: Optional[str] = Field(default=None, description="Вариант ответа 4.")
    option_5: Optional[str] = Field(default="None", description="Вариант ответа 5.")
    option_6: Optional[str] = Field(default="None", description="Вариант ответа 6.")
    option_7: Optional[str] = Field(default="None", description="Вариант ответа 7.")
    option_8: Optional[str] = Field(default="None", description="Вариант ответа 8.")
    option_9: Optional[str] = Field(default="None", description="Вариант ответа 9.")
    outputs: Union[int, str] = Field(
        description="Правильный ответ: для типов 'one' - номер варианта (1-9), 'multi' - номера через запятую без пробелов, 'open' - текст ответа."
    )
    source_text: Optional[str] = Field(
        default=None, description="Исходный текст, по которому был сгенерирован вопрос."
    )


def detect_language(text: str) -> str:
    """
    Определяет язык текста автоматически.

    Args:
        text: Текст для определения языка

    Returns:
        Код языка: 'ru', 'be', 'tg' или 'ru' по умолчанию
    """
    # Сначала проверяем наличие специфических символов таджикского языка
    # Таджикский использует кириллицу с дополнительными символами: ҷ, ҳ, қ, ғ, ӯ, ӣ, ҳ
    tajik_chars = ["ҷ", "ҳ", "қ", "ғ", "ӯ", "ӣ", "Ҷ", "Ҳ", "Қ", "Ғ", "Ӯ", "Ӣ"]
    tajik_words = [
        "тоҷик",
        "Тоҷикистон",
        "ҷумҳур",
        "Ҷумҳур",
        "забони",
        "тоҷикӣ",
        "давлат",
        "конститутсия",
    ]

    # Проверяем наличие таджикских символов или слов
    has_tajik_chars = any(char in text for char in tajik_chars)
    has_tajik_words = any(word.lower() in text.lower() for word in tajik_words)

    if has_tajik_chars or has_tajik_words:
        logger.info(
            f"Обнаружены таджикские символы или слова (символы: {has_tajik_chars}, слова: {has_tajik_words}), определяем язык как таджикский"
        )
        return "tg"

    # Проверяем белорусский язык (специфические символы: ў, і, ё)
    belarusian_chars = ["ў", "Ў"]
    if any(char in text for char in belarusian_chars):
        logger.info("Обнаружены белорусские символы, определяем язык как белорусский")
        return "be"

    if detect is None:
        logger.warning(
            "langdetect не установлен, используется русский язык по умолчанию"
        )
        return "ru"

    try:
        detected_lang = detect(text)
        logger.info(f"langdetect определил язык: {detected_lang}")

        # Маппинг кодов языков (включая персидский, который langdetect может вернуть для таджикского)
        lang_mapping = {
            "ru": "ru",  # русский
            "be": "be",  # белорусский
            "tg": "tg",  # таджикский
            "fa": "tg",  # персидский (langdetect может определять таджикский как персидский)
        }

        # Если язык поддерживается, возвращаем его
        if detected_lang in lang_mapping:
            mapped_lang = lang_mapping[detected_lang]
            logger.info(f"Язык определен как: {mapped_lang}")
            return mapped_lang

        # Если язык не поддерживается, используем русский по умолчанию
        logger.warning(f"Язык {detected_lang} не поддерживается, используется русский")
        return "ru"
    except LangDetectException as e:
        logger.warning(
            f"Ошибка определения языка: {e}, используется русский по умолчанию"
        )
        return "ru"
    except Exception as e:
        logger.error(
            f"Неожиданная ошибка при определении языка: {e}, используется русский по умолчанию"
        )
        return "ru"


def get_language_instruction(language: str, question_type: str = "one") -> str:
    """
    Возвращает инструкцию по использованию языка для промпта.

    Args:
        language: Код языка ('ru', 'be', 'tg')
        question_type: Тип вопроса ('one', 'multi', 'open')

    Returns:
        Инструкция на соответствующем языке
    """
    # Инструкции для типов one и multi
    instructions_one_multi = {
        "ru": "**ИСПОЛЬЗУЙТЕ ТОЛЬКО РУССКИЙ ЯЗЫК** для создания вопроса и вариантов ответов.",
        "be": "**ВЫКАРЫСТОЙЦЕ ТОЛЬКІ БЕЛАРУСКУЮ МОВУ** для стварэння пытання і варыянтаў адказаў.",
        "tg": "**ТАНҲО ЗАБОНИ ТОҶИКӢРО ИСТИФОДА БАРЕД! НЕ ИСПОЛЬЗУЙТЕ РУССКИЙ ЯЗЫК!** Барои эҷоди савол ва вариантҳои ҷавоб танҳо забони тоҷикӣ истифода шавад. Мисол: 'Кадом забони давлатӣ дар Тоҷикистон аст?' на забони тоҷикӣ, на русӣ!",
    }

    # Инструкции для типа open
    instructions_open = {
        "ru": "ИСПОЛЬЗУЙТЕ ИСКЛЮЧИТЕЛЬНО РУССКИЙ ЯЗЫК для формулировки вопроса и ответа.",
        "be": "ВЫКАРЫСТОЙЦЕ ВЫКЛЮЧНА БЕЛАРУСКУЮ МОВУ для фармулёўкі пытання і адказу.",
        "tg": "**ТАНҲО ЗАБОНИ ТОҶИКӢРО ИСТИФОДА БАРЕД! НЕ ИСПОЛЬЗУЙТЕ РУССКИЙ ЯЗЫК!** Барои формулировкаи савол ва ҷавоб танҳо забони тоҷикӣ истифода шавад.",
    }

    if question_type == "open":
        return instructions_open.get(language, instructions_open["ru"])
    else:
        return instructions_one_multi.get(language, instructions_one_multi["ru"])


def create_generate_question_chain(
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
) -> (
    Runnable[GenerateQuestionInput, StructuredQuestionOutput]
):
    PROMPT_MAPPING = {
        "one": {"system": SYSTEM_PROMPT_ONE, "template": PROMPT_TEMPLATE_ONE},
        "multi": {"system": SYSTEM_PROMPT_MULTI, "template": PROMPT_TEMPLATE_MULTI},
        "open": {"system": SYSTEM_PROMPT_OPEN, "template": PROMPT_TEMPLATE_OPEN},
    }

    llm = ChatOpenAI(
        model=model_name or LLM_MODEL_NAME,
        temperature=0.0,
        openai_api_base=base_url or LLM_URL_MODEL,
        openai_api_key=api_key or LLM_API_KEY,
        max_retries=3,
        streaming=False,
        timeout=30,
        max_tokens=1024,
        model_kwargs={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    )

    parser = PydanticOutputParser(pydantic_object=StructuredQuestionOutput)

    class GenerateQuestionRunnable(
        Runnable[GenerateQuestionInput, StructuredQuestionOutput]
    ):
        def invoke(self, input_data: GenerateQuestionInput) -> StructuredQuestionOutput:
            try:
                question_type = input_data["question_type"]
                logger.info(f"Processing question type: {question_type}")

                # Определение языка
                language = input_data.get("language")
                if not language:
                    language = detect_language(input_data["input_text"])
                    logger.info(f"Язык определен автоматически: {language}")
                else:
                    logger.info(f"Используется явно указанный язык: {language}")

                # Дополнительная проверка для таджикского - если в тексте есть таджикские символы, принудительно устанавливаем язык
                if language != "tg":
                    tajik_chars_check = [
                        "ҷ",
                        "ҳ",
                        "қ",
                        "ғ",
                        "ӯ",
                        "ӣ",
                        "Ҷ",
                        "Ҳ",
                        "Қ",
                        "Ғ",
                        "Ӯ",
                        "Ӣ",
                    ]
                    if any(
                        char in input_data["input_text"] for char in tajik_chars_check
                    ):
                        logger.warning(
                            f"Обнаружены таджикские символы, но язык определен как {language}. Принудительно устанавливаем tg."
                        )
                        language = "tg"

                prompts = PROMPT_MAPPING.get(question_type)
                if not prompts:
                    raise ValueError(f"Unsupported question type: {question_type}")

                # Получение инструкции по языку с учетом типа вопроса
                language_instruction = get_language_instruction(language, question_type)
                logger.info(
                    f"Используется язык: {language}, инструкция: {language_instruction[:50]}..."
                )

                # Замена инструкции по языку в системном промпте
                system_prompt_base = prompts["system"]
                original_prompt = system_prompt_base

                # Используем regex для более надежной замены
                import re

                # Заменяем старую инструкцию на новую
                # Для типов one и multi
                if "**ИСПОЛЬЗУЙТЕ ТОЛЬКО РУССКИЙ ЯЗЫК**" in system_prompt_base:
                    # Используем regex для более гибкой замены
                    pattern = r"1\.\s*\*\*ИСПОЛЬЗУЙТЕ ТОЛЬКО РУССКИЙ ЯЗЫК\*\*\s+для создания вопроса и вариантов ответов\."
                    replacement = f"1. {language_instruction}"
                    system_prompt_base = re.sub(
                        pattern, replacement, system_prompt_base, count=1
                    )
                    logger.info(
                        f"Заменена инструкция для типов one/multi. Язык: {language}"
                    )
                # Для типа open
                elif "ИСПОЛЬЗУЙТЕ ИСКЛЮЧИТЕЛЬНО РУССКИЙ ЯЗЫК" in system_prompt_base:
                    pattern = r"1\.\s+ИСПОЛЬЗУЙТЕ ИСКЛЮЧИТЕЛЬНО РУССКИЙ ЯЗЫК\s+для формулировки вопроса и ответа\."
                    replacement = f"1. {language_instruction}"
                    system_prompt_base = re.sub(
                        pattern, replacement, system_prompt_base, count=1
                    )
                    logger.info(f"Заменена инструкция для типа open. Язык: {language}")
                else:
                    logger.warning(
                        "Не найдена инструкция по языку для замены в промпте"
                    )

                # Проверяем, что замена произошла
                if system_prompt_base == original_prompt and language != "ru":
                    logger.error(
                        f"ВНИМАНИЕ: Замена инструкции не произошла для языка {language}!"
                    )
                    # Пытаемся найти и заменить более гибко - ищем любую строку с "РУССКИЙ ЯЗЫК"
                    pattern = r"1\.\s+.*?РУССКИЙ ЯЗЫК.*?\.\s*\n"
                    if re.search(pattern, system_prompt_base):
                        system_prompt_base = re.sub(
                            pattern,
                            f"1. {language_instruction}\n",
                            system_prompt_base,
                            count=1,
                        )
                        logger.info(
                            f"Выполнена замена через regex (гибкий паттерн) для языка {language}"
                        )

                # Добавляем явную инструкцию по языку в начало промпта для таджикского и белорусского
                if language != "ru":
                    language_warning = f"\n\n⚠️ ВАЖНО: {language_instruction}\n⚠️ ВСЕ ТЕКСТЫ (вопрос, варианты ответов, объяснения) ДОЛЖНЫ БЫТЬ НА ЗАБОНИ ТОҶИКӢ (таджикском языке) ЕСЛИ language=tg, НА БЕЛАРУСКАЙ МОВЕ (белорусском языке) ЕСЛИ language=be.\n\n"
                    if language == "tg":
                        language_warning = f"\n\n⚠️ ВАЖНО: {language_instruction}\n⚠️ ВСЕ ТЕКСТЫ (вопрос, варианты ответов) ДОЛЖНЫ БЫТЬ НА ЗАБОНИ ТОҶИКӢ (таджикском языке). НЕ ИСПОЛЬЗУЙТЕ РУССКИЙ ЯЗЫК!\n\n"
                    elif language == "be":
                        language_warning = f"\n\n⚠️ ВАЖНО: {language_instruction}\n⚠️ ВСЕ ТЕКСТЫ (вопрос, варианты ответов) ДОЛЖНЫ БЫТЬ НА БЕЛАРУСКАЙ МОВЕ (белорусском языке). НЕ ИСПОЛЬЗУЙТЕ РУССКИЙ ЯЗЫК!\n\n"

                    # Вставляем предупреждение после первой строки или в начало
                    if system_prompt_base.startswith("ВЫ —"):
                        # Находим конец первой строки
                        first_line_end = system_prompt_base.find(
                            "\n", system_prompt_base.find(".")
                        )
                        if first_line_end > 0:
                            system_prompt_base = (
                                system_prompt_base[: first_line_end + 1]
                                + language_warning
                                + system_prompt_base[first_line_end + 1 :]
                            )
                        else:
                            system_prompt_base = language_warning + system_prompt_base
                    else:
                        system_prompt_base = language_warning + system_prompt_base
                    logger.info(
                        f"Добавлено явное предупреждение о языке в начало промпта для языка {language}"
                    )

                # Добавляем инструкцию по языку в формат инструкций парсера для таджикского и белорусского
                format_instructions = parser.get_format_instructions()
                if language != "ru":
                    if language == "tg":
                        format_instructions = (
                            f"⚠️ КРИТИЧЕСКИ ВАЖНО: ВСЕ ПОЛЯ (task, option_1, option_2, и т.д.) ДОЛЖНЫ БЫТЬ НА ЗАБОНИ ТОҶИКӢ! НЕ ИСПОЛЬЗУЙТЕ РУССКИЙ ЯЗЫК!\n\n"
                            + format_instructions
                        )
                    elif language == "be":
                        format_instructions = (
                            f"⚠️ КРИТИЧЕСКИ ВАЖНО: ВСЕ ПОЛЯ (task, option_1, option_2, и т.д.) ДОЛЖНЫ БЫТЬ НА БЕЛАРУСКАЙ МОВЕ! НЕ ИСПОЛЬЗУЙТЕ РУССКИЙ ЯЗЫК!\n\n"
                            + format_instructions
                        )

                system_prompt = f"{system_prompt_base}\n\n{format_instructions}"
                source = input_data.get("source", "")

                # Определяем текст "Не указан" в зависимости от языка
                source_not_specified = {
                    "ru": "Не указан",
                    "be": "Не паказаны",
                    "tg": "Муайян нашудааст",
                }.get(language, "Не указан")

                human_prompt = prompts["template"].format(
                    original_text=input_data["input_text"],
                    source=source if source else source_not_specified,
                )

                prompt = ChatPromptTemplate.from_messages(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=human_prompt),
                    ]
                )

                chain = prompt | llm | parser
                pyd_out = chain.invoke({})
                out: Dict = pyd_out.model_dump()
                out["source_text"] = input_data["input_text"]
                logger.info("Successfully generated question")
                return out
            except Exception as e:
                logger.error(f"Error generating question: {str(e)}")
                raise

    return GenerateQuestionRunnable()
