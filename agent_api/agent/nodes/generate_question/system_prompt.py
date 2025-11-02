PROMPT_TEMPLATE_ONE = """
**Исходный текст**:
{original_text}

**JSON**:
"""

PROMPT_TEMPLATE_MULTI = """
**Исходный текст**:
{original_text}

**JSON**:  
"""

PROMPT_TEMPLATE_OPEN = """
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