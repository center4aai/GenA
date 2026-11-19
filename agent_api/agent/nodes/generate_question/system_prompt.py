PROMPT_TEMPLATE_ONE = """
**Источник**: {source}

**Исходный текст**:
{original_text}

**JSON**:
"""

PROMPT_TEMPLATE_MULTI = """
**Источник**: {source}

**Исходный текст**:
{original_text}

**JSON**:  
"""

PROMPT_TEMPLATE_OPEN = """
**Источник**: {source}

**Исходный текст**:

{original_text}
**JSON**:  
"""

# Textbook / encyclopedia: do not label the chunk as a citable «Источник».
PROMPT_TEMPLATE_NO_SOURCE = """
**Контекст для якорей (страна, период, тема — бери из заголовка и текста чанка; чего в чанке нет — не добавляй; не цитируй как источник)**:
{source}

**Исходный текст**:
{original_text}

**JSON**:
"""

import os

def read_file(filename):
    """Читает содержимое файла и возвращает как строку"""
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        print(f"Ошибка: файл {filename} не найден")
        return None
    except Exception as e:
        print(f"Ошибка при чтении файла {filename}: {e}")
        return None

# Получаем путь к текущей директории
current_dir = os.path.dirname(__file__)

SYSTEM_PROMPT_ONE = read_file(os.path.join(current_dir, "ONE.txt"))
SYSTEM_PROMPT_MULTI = read_file(os.path.join(current_dir, "MULTI.txt"))
SYSTEM_PROMPT_OPEN = read_file(os.path.join(current_dir, "OPEN.txt"))
KIND_NORMATIVE = read_file(os.path.join(current_dir, "KIND_NORMATIVE.txt")) or ""
KIND_TEXTBOOK = read_file(os.path.join(current_dir, "KIND_TEXTBOOK.txt")) or ""
KIND_ENCYCLOPEDIA = read_file(os.path.join(current_dir, "KIND_ENCYCLOPEDIA.txt")) or ""

KIND_INSTRUCTIONS = {
    "normative": KIND_NORMATIVE,
    "textbook": KIND_TEXTBOOK,
    "encyclopedia": KIND_ENCYCLOPEDIA,
}

# Block-type hints for prose kinds (chunker fragment_data.block_type). Appended
# after the kind block by the generator AND the refiner (they must match).
BLOCK_INSTRUCTIONS = {
    "inset": (
        "### ТИП БЛОКА: врезка\n"
        "Чанк — врезка учебника («Это интересно», справка, биография, документ). "
        "Тема вопроса — предмет врезки; название рубрики («Это интересно», «Справка») — не тема "
        "и в вопрос не попадает."
    ),
    "caption": (
        "### ТИП БЛОКА: подпись к иллюстрации\n"
        "Чанк — подпись к иллюстрации, карте или таблице. Спрашивай ТОЛЬКО о фактах, которые "
        "сообщает текст подписи (кто изображён, годы жизни, событие, дата, автор), и никогда о том, "
        "что видно на изображении («что изображено», «какого цвета», «сколько человек», "
        "«где расположен»). Отвечающий изображения не видит."
    ),
    "glossary_entry": (
        "### ТИП БЛОКА: словарная статья\n"
        "Чанк — статья словаря терминов. Спрашивай значение термина или термин по его определению; "
        "формулировка должна быть понятна без самого словаря."
    ),
}


def block_instructions_for(block_type):
    """Text to append for a chunk's block type, or empty string."""
    return BLOCK_INSTRUCTIONS.get((block_type or "").strip().lower(), "")