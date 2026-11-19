from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple, Callable
from pathlib import Path
import json, re, statistics, logging

logger = logging.getLogger(__name__)

from markitdown import MarkItDown          # DOCX → Markdown  (keeps headings/lists etc.)
# Docs: https://github.com/microsoft/markitdown
from docx import Document                   # direct DOCX parsing
# Docs: https://python-docx.readthedocs.io/
from lxml import etree                      # to read w:outlineLvl, w:numPr/ilvl etc.

WNS = {"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

# ---------- 1) Markdown → дерево (универсально для любого входа) ----------

_heading_atx = re.compile(r'^(?P<h>#{1,6})\s+(?P<title>.+?)(\s+#+\s*)?$', re.UNICODE)
_setext = re.compile(r'^(?P<u>=+|-+)\s*$')
# Регулярное выражение для ссылок markdown: [текст](url)
_link_pattern = re.compile(r'\[([^\]]+)\]\(([^\)]+)\)')

# Markdown-обёртки выделений (**bold**, __bold__, *italic*, _italic_), которые
# mammoth/markdownify навешивают на заголовки статей/глав. При определении заголовков
# их нужно игнорировать в начале/конце строки.
_MD_EMPH_LEAD = r'[*_]{1,3}'
_MD_EMPH_TAIL = r'[*_]{1,3}'

def _strip_md_emphasis(s: str) -> str:
    """Снимает парные markdown-обёртки **/__/*/_ вокруг строки.
    Применяется только к строкам-заголовкам, а не к произвольному тексту.
    Внутренние ** не трогаем — режем только парные крайние."""
    if not s:
        return s
    s2 = s.strip()
    for marker in ('***', '___', '**', '__', '*', '_'):
        if s2.startswith(marker) and s2.endswith(marker) and len(s2) > 2 * len(marker):
            s2 = s2[len(marker):-len(marker)].strip()
            break
    # Иногда жирной приходит только начальная часть: "**Статья 14.** Заголовок"
    # — снимем ведущую парную обёртку, не требуя такой же на хвосте.
    m = re.match(r'^(\*{1,3}|_{1,3})(.+?)\1(.*)$', s2)
    if m:
        s2 = (m.group(2).strip() + ' ' + m.group(3).strip()).strip()
    return s2

_ARTICLE_LINE_HEADING_RE = re.compile(
    r'^(?:Статья|Article)\s+(\d+)([\.:])?(?:[ \t]+(.*))?$',
    re.IGNORECASE,
)
# Сноска после номера статьи: «Статья 67 (см.сноску 1)» — отдельный заголовок, не ссылка в тексте.
_ARTICLE_FOOTNOTE_SUFFIX_RE = re.compile(
    r'^\(см\.сноску\s+\d+\)$',
    re.IGNORECASE,
)

def _is_standalone_article_line(line: str) -> bool:
    """True if a whole line is a legal article heading, not an in-text reference.

    «Статья 14. Понятие преступления» → heading; «Статья 5 другого закона» → reference.
    «Статья 67 (см.сноску 1)» → heading (PDF/DOCX сноска, не склейка «Статья 671»).
    """
    s = _strip_md_emphasis(line.strip())
    m = _ARTICLE_LINE_HEADING_RE.match(s)
    if not m:
        return False
    punct, rest = m.group(2), m.group(3)
    if rest:
        rest_stripped = rest.strip()
        if _ARTICLE_FOOTNOTE_SUFFIX_RE.match(rest_stripped):
            return True
        return bool(rest[0].isupper() or rest[0].isdigit())
    return True

def _iter_markdown_blocks(md: str):
    """Итератор по блокам markdown, сохраняющий ссылки в формате [текст](url)"""
    lines = md.splitlines(); i=0; n=len(lines)
    while i<n:
        line = lines[i]
        m=_heading_atx.match(line)
        if m:
            # Сохраняем ссылки в заголовках
            title = m.group("title").strip()
            yield ("heading", len(m.group("h")), title); i+=1; continue
        if line.strip() and i+1<n:
            m2=_setext.match(lines[i+1])
            if m2:
                # Сохраняем ссылки в setext заголовках
                title = line.strip()
                yield ("heading", 1 if m2.group("u").startswith("=") else 2, title); i+=2; continue
        buf=[line]; i+=1
        while i<n and lines[i].strip()!="":
            if _heading_atx.match(lines[i]): break
            if i+1<n and lines[i].strip() and _setext.match(lines[i+1] or ""): break
            buf.append(lines[i]); i+=1
        # Сохраняем ссылки в параграфах
        para_text = "\n".join(buf).strip()
        yield ("para", para_text)
        while i<n and lines[i].strip()=="": i+=1

def parse_markdown_to_tree(md: str, legal_postprocess: bool = True) -> Dict[str,Any]:
    """node = {'level':int,'title':str,'content':str,'children':[node,...]}
    Сохраняет ссылки в формате [текст](url) в content и title.

    legal_postprocess=False skips article / numbered-item surgery used for
    normative (Garant/Consultant) docs — school textbooks must not be shredded
    by a stray «Статья N» quote or birth-death years like «1907) — …».
    """
    root={"level":0,"title":"","content":"","children":[]}
    stack=[root]; pending=[]
    def attach():
        if pending:
            cur=stack[-1]
            cur["content"] += (("\n\n" if cur["content"] else "") + "\n\n".join(pending).strip())
            pending.clear()
    for blk in _iter_markdown_blocks(md):
        if blk[0]=="heading":
            attach()
            _, level, title = blk
            while stack and stack[-1]["level"]>=level: stack.pop()
            node={"level":level,"title":title,"content":"","children":[]}
            stack[-1]["children"].append(node); stack.append(node)
        else:
            _, txt = blk
            if txt: pending.append(txt)
    attach()
    
    # Постобработка: объединяем узлы с одинаковым уровнем, если они являются последовательными параграфами
    tree = _merge_sequential_paragraphs(root)

    if legal_postprocess:
        # Постобработка: разбиваем content корневого узла на статьи, если они там есть
        tree = _split_content_into_articles(tree)

        # Постобработка: объединяем пункты под заголовками статей/разделов
        tree = _attach_items_to_headings(tree)

        # Постобработка: разбиваем подпункты в content на отдельные узлы
        tree = _split_sub_items_in_content(tree)

    # Постобработка: удаляем пустые промежуточные узлы
    tree = _remove_empty_intermediate_nodes(tree)
    
    return tree

def _is_roman_numeral(text: str) -> bool:
    """Проверяет, является ли текст римской цифрой"""
    if not text:
        return False
    text_upper = text.upper().strip()
    # Римские цифры: I, II, III, IV, V, VI, VII, VIII, IX, X, и т.д.
    roman_pattern = r'^[IVXLCDM]+$'
    return bool(re.match(roman_pattern, text_upper))

def _is_real_heading(title: str) -> bool:
    """Определяет, является ли title настоящим заголовком (РАЗДЕЛ, ГЛАВА, СТАТЬЯ и т.д.)"""
    if not title:
        return False
    
    title_upper = title.upper().strip()
    # Паттерны для настоящих заголовков
    heading_patterns = [
        r'^(РАЗДЕЛ|ГЛАВА|ЧАСТЬ|СТАТЬЯ|ПАРАГРАФ)\s+',
        r'^ГЛАВА\s+\d+',
        r'^РАЗДЕЛ\s+(ПЕРВЫЙ|ВТОРОЙ|ТРЕТИЙ|ЧЕТВЕРТЫЙ|ПЯТЫЙ|ШЕСТОЙ|СЕДЬМОЙ|ВОСЬМОЙ|ДЕВЯТЫЙ|ДЕСЯТЫЙ)',
        r'^КОНСТИТУЦИЯ',
        r'^ПРЕАМБУЛА',
        r'^[IVXLCDM]+\.',  # Римские цифры с точкой: I., II., III., IV., V.
    ]
    
    for pattern in heading_patterns:
        if re.match(pattern, title_upper):
            return True
    
    # Проверяем, начинается ли с римской цифры и точки
    parts = title_upper.split('.', 1)
    if len(parts) >= 2 and _is_roman_numeral(parts[0]):
        return True
    
    return False

def _is_likely_paragraph(node: Dict[str,Any]) -> bool:
    """Определяет, является ли узел скорее параграфом, чем заголовком"""
    title = node.get("title", "").strip()
    content = node.get("content", "").strip()
    has_children = bool(node.get("children"))
    
    # Если есть дочерние элементы - это заголовок
    if has_children:
        return False
    
    # Если нет title - это параграф
    if not title:
        return True
    
    # Если это настоящий заголовок - не параграф
    if _is_real_heading(title):
        return False
    
    # Если title очень длинный (>150 символов) - скорее всего параграф
    if len(title) > 150:
        return True
    
    # Если title заканчивается запятой или точкой с запятой - скорее всего часть предложения
    if title.endswith(",") or title.endswith(";"):
        return True
    
    # Если title начинается с маленькой буквы (не заглавной) - скорее всего часть предложения
    if title and title[0].islower():
        return True
    
    # Если есть content и title короткий - это заголовок
    if content and len(title) < 100:
        return False
    
    # Если нет content и title короткий - возможно заголовок, но если очень короткий и заканчивается запятой - параграф
    if not content and len(title) < 50 and (title.endswith(",") or title.endswith(";")):
        return True
    
    return False

def _merge_sequential_paragraphs(node: Dict[str,Any]) -> Dict[str,Any]:
    """Объединяет последовательные узлы одного уровня, если они являются параграфами"""
    if not node.get("children"):
        return node
    
    # Рекурсивно обрабатываем дочерние элементы
    processed_children = [_merge_sequential_paragraphs(ch) for ch in node.get("children", [])]
    
    merged_children = []
    i = 0
    while i < len(processed_children):
        current = processed_children[i].copy()
        
        # Если текущий узел - параграф, пытаемся объединить с последующими параграфами того же уровня
        if _is_likely_paragraph(current):
            # Собираем все последующие параграфы того же уровня
            paragraphs_to_merge = [current]
            j = i + 1
            while j < len(processed_children):
                next_node = processed_children[j]
                if (_is_likely_paragraph(next_node) and 
                    next_node.get("level", 0) == current.get("level", 0)):
                    paragraphs_to_merge.append(next_node)
                    j += 1
                else:
                    break
            
            # Объединяем все параграфы в один
            if len(paragraphs_to_merge) > 1:
                merged = {
                    "level": current.get("level", 1),
                    "title": "",
                    "content": "",
                    "children": []
                }
                
                # Собираем title и content из всех параграфов
                parts = []
                for para in paragraphs_to_merge:
                    para_title = para.get("title", "").strip()
                    para_content = para.get("content", "").strip()
                    
                    if para_title:
                        parts.append(para_title)
                    if para_content:
                        parts.append(para_content)
                
                # Первый непустой title становится title узла, остальное - content
                for para in paragraphs_to_merge:
                    para_title = para.get("title", "").strip()
                    if para_title and not merged["title"]:
                        # Проверяем, не является ли это частью предложения
                        if not _is_likely_paragraph(para) or len(para_title) < 50:
                            merged["title"] = para_title
                            break
                
                # Объединяем все части в content
                # Если title заканчивается запятой - объединяем через пробел, иначе через \n\n
                if parts:
                    # Проверяем, заканчивается ли первый title запятой
                    first_part = parts[0] if parts else ""
                    if first_part.endswith(",") or first_part.endswith(";"):
                        # Объединяем через пробел для продолжения предложения
                        merged["content"] = " ".join(parts).strip()
                    else:
                        merged["content"] = "\n\n".join(parts).strip()
                
                # Если title не установлен, но есть короткий первый title - используем его
                if not merged["title"] and paragraphs_to_merge[0].get("title"):
                    first_title = paragraphs_to_merge[0].get("title", "").strip()
                    # Если title заканчивается запятой - это часть предложения, не используем как title
                    if len(first_title) < 100 and not (first_title.endswith(",") or first_title.endswith(";")):
                        merged["title"] = first_title
                        # Убираем его из content, если он там есть
                        if merged["content"].startswith(first_title):
                            merged["content"] = merged["content"][len(first_title):].strip()
                
                merged_children.append(merged)
                i = j  # Пропускаем все объединенные узлы
                continue
        
        merged_children.append(current)
        i += 1
    
    node["children"] = merged_children
    return node

def _is_numbered_item(title: str) -> bool:
    """Определяет, является ли title пронумерованным пунктом (начинается с цифры и точки)"""
    if not title:
        return False
    
    title_stripped = title.strip()
    # Паттерн: начинается с цифры(ов), затем точка, затем пробел и текст
    # Примеры: "1. Текст", "1.1. Текст", "1) Текст"
    numbered_patterns = [
        r'^\d+[\.\)]\s+',  # "1. " или "1) "
        r'^\d+\.\d+[\.\)]\s+',  # "1.1. " или "1.1) "
        r'^[а-яА-Я]\.\s+',  # "а. " (буквенная нумерация)
    ]
    
    for pattern in numbered_patterns:
        if re.match(pattern, title_stripped):
            return True
    
    return False

def _is_sub_item(title: str) -> bool:
    """Определяет, является ли title подпунктом (начинается с цифры и скобки, например "1)")"""
    if not title:
        return False
    
    title_stripped = title.strip()
    # Подпункты обычно имеют формат "1)", "2)", "а)", "б)" и т.д.
    sub_item_patterns = [
        r'^\d+\)\s+',  # "1) "
        r'^[а-яА-Я]\)\s+',  # "а) " (буквенная нумерация)
    ]
    
    for pattern in sub_item_patterns:
        if re.match(pattern, title_stripped):
            return True
    
    return False

def _extract_sub_items_from_content(content: str) -> List[Dict[str,Any]]:
    """Извлекает подпункты вида "1)", "2)", "3)" из content, если они идут в одну строку через точку с запятой"""
    if not content:
        return []
    
    items = []
    
    # Ищем подпункты вида "1) текст; 2) текст; 3) текст;"
    # Паттерн: цифра + скобка + пробел + текст до следующего подпункта или конца строки
    # Учитываем, что текст может содержать точки с запятой внутри (но не перед следующим подпунктом)
    # Более точный паттерн: ищем "цифра) текст" до следующего "цифра)" или конца строки
    sub_item_pattern = re.compile(r'(\d+\)\s+[^;]*?)(?=\s*;\s*\d+\)|;?\s*$)', re.MULTILINE | re.DOTALL)
    
    matches = list(sub_item_pattern.finditer(content))
    
    # Если нашли несколько подпунктов
    if len(matches) > 1:
        # Проверяем, что они действительно в одной строке (нет \n между ними, или только один)
        first_match = matches[0]
        last_match = matches[-1]
        text_between = content[first_match.start():last_match.end()]
        
        # Если между первым и последним подпунктом нет переносов строк (или только один), значит они в одной строке
        if '\n' not in text_between or text_between.count('\n') <= 1:
            # Разбиваем на отдельные подпункты
            for match in matches:
                sub_item_text = match.group(1).strip()
                # Удаляем точку с запятой в конце, если есть
                if sub_item_text.endswith(';'):
                    sub_item_text = sub_item_text[:-1].strip()
                
                if sub_item_text:
                    items.append({
                        "level": 4,  # Будет установлен правильно позже
                        "title": sub_item_text,
                        "content": "",
                        "children": []
                    })
    elif len(matches) == 1:
        # Если нашли только один подпункт, но в content есть другие подпункты через точку с запятой
        # Проверяем, есть ли в content другие подпункты после точки с запятой
        first_match = matches[0]
        text_after = content[first_match.end():].strip()
        # Ищем другие подпункты после первого
        if text_after and re.search(r';\s*\d+\)', text_after):
            # Есть другие подпункты, извлекаем все через более простой метод
            # Разбиваем по паттерну "цифра) текст;"
            all_matches = re.finditer(r'(\d+\)\s+[^;]+?)(?=\s*;\s*\d+\)|;?\s*$)', content, re.MULTILINE | re.DOTALL)
            for match in all_matches:
                sub_item_text = match.group(1).strip()
                if sub_item_text.endswith(';'):
                    sub_item_text = sub_item_text[:-1].strip()
                if sub_item_text:
                    items.append({
                        "level": 4,
                        "title": sub_item_text,
                        "content": "",
                        "children": []
                    })
    
    return items

def _is_letter_sub_item(title: str) -> bool:
    """Определяет, является ли title буквенным подпунктом (начинается с буквы и скобки, например "а)")"""
    if not title:
        return False
    
    title_stripped = title.strip()
    # Буквенные подпункты: "а)", "б)", "в)" и т.д.
    letter_pattern = re.compile(r'^[а-яА-Я]\)\s+')
    return bool(letter_pattern.match(title_stripped))

def _extract_letter_sub_items_from_content(content: str) -> List[Dict[str,Any]]:
    """Извлекает буквенные подпункты (а), б), в)) из content текста"""
    if not content:
        return []
    
    items = []
    
    # Находим все позиции, где начинается новый буквенный подпункт
    # Паттерн: начало строки, \n\n, или \n, затем буква, скобка, пробел
    letter_sub_item_pattern = re.compile(r'(?:^|\n\n|\n)([а-яА-Я]\)\s+)', re.MULTILINE)
    
    # Находим все начала подпунктов
    start_positions = []
    for match in letter_sub_item_pattern.finditer(content):
        pos = match.start()
        match_text = match.group(0)
        # Пропускаем разделители (\n\n или \n)
        if match_text.startswith('\n\n'):
            pos = match.start() + 2
        elif match_text.startswith('\n'):
            pos = match.start() + 1
        start_positions.append(pos)
    
    # Если нашли начала подпунктов, разбиваем content по ним
    if start_positions:
        for i, start_pos in enumerate(start_positions):
            # Определяем конец текущего подпункта (начало следующего или конец content)
            end_pos = start_positions[i + 1] if i + 1 < len(start_positions) else len(content)
            
            # Извлекаем текст подпункта
            item_text = content[start_pos:end_pos].strip()
            if not item_text:
                continue
            
            # Разделяем на первую строку (title) и остальное (content)
            lines = item_text.split("\n")
            first_line = lines[0].strip() if lines else ""
            
            if _is_letter_sub_item(first_line):
                # Остальной текст - это content подпункта
                remaining_lines = lines[1:] if len(lines) > 1 else []
                item_content = "\n".join(remaining_lines).strip()
                
                items.append({
                    "level": 2,  # Будет установлен правильно позже
                    "title": first_line,
                    "content": item_content,
                    "children": []
                })
    
    return items

def _is_main_item(title: str) -> bool:
    """Определяет, является ли title основным пунктом (начинается с цифры и точки, например "1.")"""
    if not title:
        return False
    
    title_stripped = title.strip()
    # Основные пункты обычно имеют формат "1.", "2.", "а." и т.д.
    main_item_patterns = [
        r'^\d+\.\s+',  # "1. "
        r'^[а-яА-Я]\.\s+',  # "а. " (буквенная нумерация)
    ]
    
    for pattern in main_item_patterns:
        if re.match(pattern, title_stripped):
            return True
    
    return False

def _is_article_heading(title: str) -> bool:
    """Определяет, является ли title заголовком статьи/раздела/главы, под которым могут быть пункты"""
    if not title:
        return False
    
    title_stripped = title.strip()
    title_upper = title_stripped.upper()
    
    # Паттерны для заголовков, под которыми обычно идут пункты
    # Учитываем варианты с пробелом и без, с точкой и без
    article_patterns = [
        r'^(СТАТЬЯ|ARTICLE)\s*\d+',  # "Статья 1" или "Статья1"
        r'^(РАЗДЕЛ|SECTION)\s*\d+',
        r'^(ГЛАВА|CHAPTER)\s*\d+',
        r'^(ЧАСТЬ|PART)\s*\d+',
        r'^(ПАРАГРАФ|PARAGRAPH)\s*\d+',
        r'^СТАТЬЯ\s*\d+',
        r'^ГЛАВА\s*\d+',
        r'^РАЗДЕЛ\s*\d+',
        r'^[IVXLCDM]+\.',  # Римские цифры с точкой: "I.", "II.", "III.", "IV.", "V."
    ]
    
    for pattern in article_patterns:
        if re.match(pattern, title_upper):
            return True
    
    # Проверяем, начинается ли с римской цифры и точки
    parts = title_upper.split('.', 1)
    if len(parts) >= 2 and _is_roman_numeral(parts[0]):
        return True
    
    return False

def _extract_numbered_items_from_content_with_positions(content: str) -> Tuple[List[Dict[str,Any]], List[Tuple[int, int]]]:
    """Извлекает пронумерованные пункты из content текста и возвращает их позиции"""
    if not content:
        return [], []
    
    items = []
    positions = []
    
    # Улучшенный паттерн для поиска пунктов:
    # 1. Пункты могут начинаться с начала строки, после \n\n, после \n, или после пробела/точки с запятой
    # 2. Формат: цифра + скобка/точка + пробел + текст
    # 3. Пункт заканчивается перед следующим пунктом или в конце строки/текста
    
    # Находим все позиции, где начинается новый пронумерованный пункт
    # Паттерн: начало строки, \n\n, \n, пробел, или точка с запятой + пробел, затем цифра, скобка, пробел
    # Также учитываем случаи, когда пункты идут подряд через точку с запятой: "1) текст; 2) текст;"
    numbered_start_pattern = re.compile(r'(?:^|\n\n|\n|;\s*|\.\s+)(\d+\))\s+', re.MULTILINE)
    
    # Находим все начала пунктов
    start_positions = []
    for match in numbered_start_pattern.finditer(content):
        # Позиция начала пункта (после разделителя)
        pos = match.start()
        match_text = match.group(0)
        # Определяем, сколько символов пропустить
        if match_text.startswith('\n\n'):
            pos = match.start() + 2
        elif match_text.startswith('\n'):
            pos = match.start() + 1
        elif match_text.startswith('; '):
            pos = match.start() + 2
        elif match_text.startswith(';'):
            pos = match.start() + 1
        elif match_text.startswith('. '):
            pos = match.start() + 2
        # Если начинается с начала строки, pos уже правильный
        start_positions.append((pos, match.end()))
    
    # Если нашли начала пунктов, разбиваем content по ним
    if start_positions:
        for i, (start_pos, match_end) in enumerate(start_positions):
            # Определяем конец текущего пункта (начало следующего или конец content)
            if i + 1 < len(start_positions):
                next_start_pos, _ = start_positions[i + 1]
                end_pos = next_start_pos
            else:
                end_pos = len(content)
            
            # Извлекаем текст пункта
            item_text = content[start_pos:end_pos].strip()
            if not item_text:
                continue
            
            # Определяем конец текущего пункта
            # Пункт заканчивается перед следующим пунктом (который начинается с "цифра)")
            # Или перед точкой с запятой, если следующий пункт идет сразу после
            item_end_pos = len(item_text)
            
            # Ищем следующий пункт в тексте (паттерн: пробел или точка с запятой + пробел + цифра + скобка)
            next_item_match = re.search(r';\s*(\d+\))\s+', item_text)
            if next_item_match:
                # Найден следующий пункт, текущий заканчивается перед ним
                item_end_pos = next_item_match.start()
                item_text = item_text[:item_end_pos].strip()
            
            # Удаляем точку с запятой в конце, если есть
            if item_text.endswith(';'):
                item_text = item_text[:-1].strip()
            
            if not item_text:
                continue
            
            # Разделяем на первую строку (title) и остальное (content)
            # Пункт может быть на одной строке или на нескольких
            lines = item_text.split("\n")
            first_line = lines[0].strip() if lines else ""
            
            if not first_line:
                continue
            
            # Проверяем, является ли первая строка пронумерованным пунктом
            if _is_numbered_item(first_line):
                # Остальной текст - это content пункта
                remaining_lines = lines[1:] if len(lines) > 1 else []
                item_content = "\n".join(remaining_lines).strip()
                
                # Вычисляем реальные позиции в исходном content
                item_start = start_pos
                # Конец пункта - это начало следующего пункта или конец текста
                if next_item_match:
                    item_end = start_pos + item_end_pos + 1  # +1 чтобы включить точку с запятой
                else:
                    item_end = end_pos
                
                items.append({
                    "level": 2,  # Будет установлен правильно позже
                    "title": first_line,
                    "content": item_content,
                    "children": []
                })
                positions.append((item_start, item_end))
    
    return items, positions

def _extract_numbered_items_from_content(content: str) -> List[Dict[str,Any]]:
    """Извлекает пронумерованные пункты из content текста"""
    items, _ = _extract_numbered_items_from_content_with_positions(content)
    if items:
        return items
    
    # Fallback логика, если основная функция не нашла пункты
    items = []
    
    # Улучшенный паттерн для поиска пунктов:
    # 1. Пункты могут начинаться с начала строки, после \n\n, после \n, или после пробела/точки с запятой
    # 2. Формат: цифра + скобка/точка + пробел + текст
    # 3. Пункт заканчивается перед следующим пунктом или в конце строки/текста
    
    # Находим все позиции, где начинается новый пронумерованный пункт
    # Паттерн: начало строки, \n\n, \n, пробел, или точка с запятой + пробел, затем цифра, скобка, пробел
    # Также учитываем случаи, когда пункты идут подряд через точку с запятой: "1) текст; 2) текст;"
    numbered_start_pattern = re.compile(r'(?:^|\n\n|\n|;\s*|\.\s+)(\d+\))\s+', re.MULTILINE)
    
    # Находим все начала пунктов
    start_positions = []
    for match in numbered_start_pattern.finditer(content):
        # Позиция начала пункта (после разделителя)
        pos = match.start()
        match_text = match.group(0)
        # Определяем, сколько символов пропустить
        if match_text.startswith('\n\n'):
            pos = match.start() + 2
        elif match_text.startswith('\n'):
            pos = match.start() + 1
        elif match_text.startswith('; '):
            pos = match.start() + 2
        elif match_text.startswith('. '):
            pos = match.start() + 2
        # Если начинается с начала строки, pos уже правильный
        start_positions.append((pos, match.end()))
    
    # Если нашли начала пунктов, разбиваем content по ним
    if start_positions:
        for i, (start_pos, match_end) in enumerate(start_positions):
            # Определяем конец текущего пункта (начало следующего или конец content)
            if i + 1 < len(start_positions):
                next_start_pos, _ = start_positions[i + 1]
                end_pos = next_start_pos
            else:
                end_pos = len(content)
            
            # Извлекаем текст пункта
            item_text = content[start_pos:end_pos].strip()
            if not item_text:
                continue
            
            # Определяем конец текущего пункта
            # Пункт заканчивается перед следующим пунктом (который начинается с "цифра)")
            # Или перед точкой с запятой, если следующий пункт идет сразу после
            item_end_pos = len(item_text)
            
            # Ищем следующий пункт в тексте (паттерн: пробел или точка с запятой + пробел + цифра + скобка)
            next_item_match = re.search(r';\s*(\d+\))\s+', item_text)
            if next_item_match:
                # Найден следующий пункт, текущий заканчивается перед ним
                item_end_pos = next_item_match.start()
                item_text = item_text[:item_end_pos].strip()
            
            # Удаляем точку с запятой в конце, если есть
            if item_text.endswith(';'):
                item_text = item_text[:-1].strip()
            
            if not item_text:
                continue
            
            # Разделяем на первую строку (title) и остальное (content)
            # Пункт может быть на одной строке или на нескольких
            lines = item_text.split("\n")
            first_line = lines[0].strip() if lines else ""
            
            if not first_line:
                continue
            
            # Проверяем, является ли первая строка пронумерованным пунктом
            if _is_numbered_item(first_line):
                # Остальной текст - это content пункта
                remaining_lines = lines[1:] if len(lines) > 1 else []
                item_content = "\n".join(remaining_lines).strip()
                
                items.append({
                    "level": 2,  # Будет установлен правильно позже
                    "title": first_line,
                    "content": item_content,
                    "children": []
                })
    else:
        # Если не нашли через паттерн, пробуем разделить по точкам с запятой
        # (для случаев, когда пункты в одной строке: "1) текст; 2) текст;")
        if ';' in content and re.search(r'\d+\)\s+', content):
            # Ищем все пункты в формате "цифра) текст;"
            item_matches = list(re.finditer(r'(\d+\))\s+([^;]+?)(?=;\s*\d+\)|;?\s*$)', content))
            
            if item_matches:
                for match in item_matches:
                    item_num = match.group(1)
                    item_text = match.group(2).strip()
                    
                    # Удаляем точку с запятой в конце, если есть
                    if item_text.endswith(';'):
                        item_text = item_text[:-1].strip()
                    
                    if item_text:
                        item_title = f"{item_num} {item_text}"
                        
                        if _is_numbered_item(item_title):
                            items.append({
                                "level": 2,
                                "title": item_title,
                                "content": "",
                                "children": []
                            })
        
        # Если не нашли через точки с запятой, пробуем разделить по \n\n
        if not items:
            parts = content.split("\n\n")
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                
                lines = part.split("\n")
                first_line = lines[0].strip() if lines else ""
                
                if _is_numbered_item(first_line):
                    remaining_lines = lines[1:] if len(lines) > 1 else []
                    item_content = "\n".join(remaining_lines).strip()
                    
                    items.append({
                        "level": 2,
                        "title": first_line,
                        "content": item_content,
                        "children": []
                    })
    
    return items

def _split_content_into_articles(node: Dict[str,Any]) -> Dict[str,Any]:
    """Разбивает content узла на статьи, если они там есть (fallback после markdown)."""
    content = node.get("content", "").strip()
    if content:
        # «Статья 14.» / «Статья 14. Заголовок» — в т.ч. без отдельного markdown-заголовка
        article_pattern = re.compile(
            r'(?:^|\n\n|\n)((?:Статья|Article)\s+\d+[^\n]*)',
            re.MULTILINE | re.IGNORECASE,
        )
        matches = [
            m for m in article_pattern.finditer(content)
            if _is_standalone_article_line(m.group(1))
        ]
        if matches:
            extracted_articles = []
            remaining_content_parts = []
            child_level = node.get("level", 0) + 1

            first_match = matches[0]
            text_before = content[:first_match.start()].strip()
            if text_before:
                remaining_content_parts.append(text_before)

            for i, match in enumerate(matches):
                article_title = _strip_md_emphasis(match.group(1).strip())
                article_start = match.end()
                while article_start < len(content) and content[article_start] in ('\n', ' ', '\t'):
                    article_start += 1
                article_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                article_content = content[article_start:article_end].strip()
                extracted_articles.append({
                    "level": child_level,
                    "title": article_title,
                    "content": article_content,
                    "children": [],
                })

            node["content"] = "\n\n".join(remaining_content_parts) if remaining_content_parts else ""
            node["children"] = extracted_articles + node.get("children", [])

    if node.get("children"):
        node["children"] = [_split_content_into_articles(ch) for ch in node["children"]]
    return node

def _split_sub_items_in_content(node: Dict[str,Any]) -> Dict[str,Any]:
    """Разбивает подпункты вида "1)", "2)", "3)" в content на отдельные узлы, если они идут в одну строку"""
    # Рекурсивно обрабатываем дочерние элементы сначала
    if node.get("children"):
        node["children"] = [_split_sub_items_in_content(ch) for ch in node["children"]]
    
    # Проверяем content текущего узла на наличие подпунктов в одну строку
    content = node.get("content", "").strip()
    title = node.get("title", "").strip()
    
    # Если title начинается с подпункта вида "1)", проверяем, есть ли другие подпункты в content
    if _is_sub_item(title) and content:
        # Проверяем, есть ли в content другие подпункты
        # Объединяем title и content для проверки (без добавления "; ", так как они могут быть разделены по-разному)
        # Если content начинается с подпункта, объединяем через "; ", иначе просто конкатенируем
        if _is_sub_item(content.strip().split(';')[0] if ';' in content else content.strip()):
            combined_text = title + "; " + content
        else:
            combined_text = title + " " + content if title else content
        sub_items = _extract_sub_items_from_content(combined_text)
        
        if sub_items and len(sub_items) > 1:
            # Если нашли несколько подпунктов, разбиваем их все на отдельные узлы
            # Удаляем title, так как он будет частью подпунктов
            node["title"] = ""
            
            # Удаляем все подпункты из combined_text
            # Создаем паттерн для удаления всех подпунктов сразу
            cleaned_content = combined_text
            # Удаляем каждый подпункт по отдельности, начиная с конца
            for item in reversed(sub_items):
                sub_item_text = item["title"]
                # Удаляем подпункт с точкой с запятой или без, учитывая возможные пробелы
                # Используем более точный паттерн, который учитывает, что подпункт может быть в начале строки
                pattern = r'\s*' + re.escape(sub_item_text) + r'\s*;?\s*'
                cleaned_content = re.sub(pattern, '', cleaned_content, count=1)
            
            # Очищаем множественные пробелы, точки с запятой и переносы строк
            cleaned_content = re.sub(r'\s*;\s*', ' ', cleaned_content)
            cleaned_content = re.sub(r'\s+', ' ', cleaned_content).strip()
            
            # Удаляем точки с запятой в начале и конце
            cleaned_content = cleaned_content.strip(';').strip()
            
            # Если остался только один подпункт (который не был удален), удаляем его тоже
            if _is_sub_item(cleaned_content.strip()):
                cleaned_content = ""
            
            if not cleaned_content or cleaned_content.isspace():
                node["content"] = ""
            else:
                node["content"] = cleaned_content
            
            # Устанавливаем правильный уровень для подпунктов
            current_level = node.get("level", 0)
            for item in sub_items:
                item["level"] = current_level + 1
            
            # Добавляем подпункты в children
            if not node.get("children"):
                node["children"] = []
            node["children"] = sub_items + node["children"]
            return node
    
    # Если title не является подпунктом, проверяем только content
    if not content:
        return node
    
    # Ищем подпункты вида "1) текст; 2) текст; 3) текст;"
    sub_items = _extract_sub_items_from_content(content)
    
    if sub_items and len(sub_items) > 1:
        # Если нашли несколько подпунктов в одну строку, разбиваем их
        sub_item_texts = [item["title"] for item in sub_items]
        
        # Удаляем подпункты из content
        cleaned_content = content
        for sub_item_text in reversed(sub_item_texts):
            pattern = re.escape(sub_item_text) + r'\s*;?\s*'
            cleaned_content = re.sub(pattern, '', cleaned_content, count=1)
        
        cleaned_content = re.sub(r'\s+', ' ', cleaned_content).strip()
        if not cleaned_content or cleaned_content.isspace():
            node["content"] = ""
        else:
            node["content"] = cleaned_content
        
        # Устанавливаем правильный уровень для подпунктов
        current_level = node.get("level", 0)
        for item in sub_items:
            item["level"] = current_level + 1
        
        # Добавляем подпункты в children
        if not node.get("children"):
            node["children"] = []
        node["children"] = sub_items + node["children"]
    
    return node

def _remove_empty_intermediate_nodes(node: Dict[str,Any]) -> Dict[str,Any]:
    """Удаляет пустые промежуточные узлы (с пустым title и content, но с children)"""
    # Рекурсивно обрабатываем дочерние элементы сначала
    if node.get("children"):
        processed_children = []
        for child in node["children"]:
            processed_child = _remove_empty_intermediate_nodes(child)
            # Если дочерний узел пустой (нет title и content), но есть children, поднимаем children на уровень выше
            if (not processed_child.get("title", "").strip() and 
                not processed_child.get("content", "").strip() and 
                processed_child.get("children")):
                # Поднимаем children на уровень выше
                # Устанавливаем level children равным level родителя + 1
                parent_level = node.get("level", 0)
                for grandchild in processed_child.get("children", []):
                    # Устанавливаем level как parent_level + 1 (дети должны быть на один уровень ниже родителя)
                    grandchild["level"] = parent_level + 1
                processed_children.extend(processed_child.get("children", []))
            else:
                processed_children.append(processed_child)
        node["children"] = processed_children
    
    return node

def _attach_items_to_headings(node: Dict[str,Any]) -> Dict[str,Any]:
    """Прикрепляет пронумерованные пункты к заголовкам статей/разделов"""
    if not node.get("children"):
        return node
    
    # Рекурсивно обрабатываем дочерние элементы
    processed_children = [_attach_items_to_headings(ch) for ch in node.get("children", [])]
    
    result_children = []
    i = 0
    
    while i < len(processed_children):
        current = processed_children[i].copy()
        current_title = current.get("title", "").strip()
        
        # Проверяем, является ли текущий узел заголовком статьи/раздела
        if _is_article_heading(current_title) and not current.get("children"):
            # Собираем все последующие элементы
            items_to_attach = []
            content_parts = []
            current_level = current.get("level", 0)
            
            # Сначала проверяем content самого заголовка на наличие пронумерованных пунктов
            current_content = current.get("content", "").strip()
            if current_content:
                # Проверяем, есть ли в content пронумерованные пункты (не только в первой строке)
                # Ищем паттерн пронумерованного пункта в content
                has_numbered_items = bool(re.search(r'\n\n\d+[\.\)]\s+', current_content) or 
                                         re.match(r'^\d+[\.\)]\s+', current_content))
                
                if has_numbered_items:
                    # Извлекаем пункты из content заголовка
                    # Используем улучшенную функцию, которая также возвращает позиции
                    result = _extract_numbered_items_from_content_with_positions(current_content)
                    # Защита от неправильной распаковки
                    if isinstance(result, tuple) and len(result) == 2:
                        extracted_items, item_positions = result
                    else:
                        # Если функция вернула неожиданное количество значений, используем только первый элемент
                        extracted_items = result[0] if isinstance(result, tuple) and len(result) > 0 else []
                        item_positions = result[1] if isinstance(result, tuple) and len(result) > 1 else []
                    if extracted_items and item_positions:
                        # Удаляем все извлеченные пункты из content используя позиции
                        cleaned_content = current_content
                        
                        # Удаляем пункты в обратном порядке, чтобы не сбить позиции
                        for item, (start_pos, end_pos) in zip(reversed(extracted_items), reversed(item_positions)):
                            # Удаляем пункт из content
                            before = cleaned_content[:start_pos].rstrip()
                            after = cleaned_content[end_pos:].lstrip()
                            
                            # Объединяем части, убирая лишние пробелы
                            if before and after:
                                cleaned_content = before + " " + after
                            elif before:
                                cleaned_content = before
                            elif after:
                                cleaned_content = after
                            else:
                                cleaned_content = ""
                        
                        # Очищаем множественные пробелы и пустые строки
                        cleaned_content = re.sub(r'\s+', ' ', cleaned_content)
                        cleaned_content = re.sub(r'\n{3,}', '\n\n', cleaned_content)
                        cleaned_content = cleaned_content.strip()
                        
                        current["content"] = cleaned_content
                        
                        # Добавляем извлеченные пункты
                        for item in extracted_items:
                            item["level"] = current_level + 1
                            items_to_attach.append(item)
                    elif extracted_items:
                        # Если позиции не найдены, используем старую логику
                        # Удаляем пункты по тексту
                        cleaned_content = current_content
                        for item in reversed(extracted_items):
                            item_title = item.get("title", "").strip()
                            # Ищем и удаляем пункт
                            item_pos = cleaned_content.find(item_title)
                            if item_pos != -1:
                                end_pos = item_pos + len(item_title)
                                # Пропускаем точку с запятую и пробелы
                                while end_pos < len(cleaned_content) and cleaned_content[end_pos] in [';', ' ', '\n']:
                                    end_pos += 1
                                cleaned_content = cleaned_content[:item_pos].rstrip() + " " + cleaned_content[end_pos:].lstrip()
                        
                        cleaned_content = re.sub(r'\s+', ' ', cleaned_content).strip()
                        current["content"] = cleaned_content
                        
                        for item in extracted_items:
                            item["level"] = current_level + 1
                            items_to_attach.append(item)
            
            j = i + 1
            last_item_number = None  # Отслеживаем номер последнего пункта
            
            while j < len(processed_children):
                next_node = processed_children[j]
                next_title = next_node.get("title", "").strip()
                next_content = next_node.get("content", "").strip()
                next_level = next_node.get("level", 0)
                
                # ВАЖНО: Проверяем, является ли следующий узел заголовком статьи/раздела
                # Это должно быть ПЕРВОЙ проверкой, чтобы не пропустить новую статью
                # Проверяем на том же уровне или выше - это точно новая статья/раздел
                if _is_article_heading(next_title):
                    # Если это заголовок статьи на том же или более высоком уровне - прекращаем
                    if next_level <= current_level:
                        break
                    # Если это заголовок статьи на более низком уровне, но мы уже собрали пункты - тоже прекращаем
                    # (может быть подраздел внутри статьи)
                    if next_level > current_level and items_to_attach:
                        break
                
                # Если следующий узел - пронумерованный пункт в title
                if _is_numbered_item(next_title):
                    # Проверяем, не начинается ли пункт с "1." - это может означать новую статью
                    if next_title.strip().startswith("1.") and last_item_number is not None and last_item_number > 1:
                        # Если мы уже собрали пункты с номерами > 1, а следующий пункт начинается с "1.",
                        # это может быть начало новой статьи - прекращаем
                        break
                    
                    if next_level >= current_level and next_level <= current_level + 1:
                        # Извлекаем номер пункта для отслеживания
                        num_match = re.match(r'^(\d+)[\.\)]', next_title)
                        if num_match:
                            item_num = int(num_match.group(1))
                            # Если пункт начинается с "1." и мы уже собрали пункты, это новая статья
                            if item_num == 1 and last_item_number is not None and last_item_number > 1:
                                break
                            last_item_number = item_num
                        
                        item_copy = next_node.copy()
                        item_copy["level"] = current_level + 1
                        items_to_attach.append(item_copy)
                        j += 1
                        continue
                    else:
                        break
                
                # Если следующий узел имеет content с пронумерованными пунктами
                # Проверяем, есть ли в content пронумерованные пункты
                if next_content:
                    # Проверяем, начинается ли content с пронумерованного пункта
                    content_lines = next_content.split("\n")
                    first_content_line = content_lines[0].strip() if content_lines else ""
                    if _is_numbered_item(first_content_line):
                        # Извлекаем пункты из content
                        extracted_items = _extract_numbered_items_from_content(next_content)
                        if extracted_items:
                            # Проверяем первый пункт - если он начинается с "1." и мы уже собрали пункты, это новая статья
                            first_item_title = extracted_items[0].get("title", "").strip()
                            if first_item_title.startswith("1.") and last_item_number is not None and last_item_number > 1:
                                break
                            
                            for item in extracted_items:
                                item["level"] = current_level + 1
                                items_to_attach.append(item)
                                # Обновляем last_item_number
                                num_match = re.match(r'^(\d+)[\.\)]', item.get("title", ""))
                                if num_match:
                                    last_item_number = int(num_match.group(1))
                            j += 1
                            # Продолжаем собирать следующие пункты
                            continue
                
                # Если следующий узел не является пронумерованным пунктом
                # Проверяем различные случаи:
                # 1. Уровень меньше текущего - это возврат к более высокому уровню, прекращаем
                if next_level < current_level:
                    break
                # 2. Это обычный текст на том же уровне - добавляем в content статьи
                if next_level == current_level:
                    # Если это не пронумерованный пункт, добавляем в content
                    if not _is_numbered_item(next_title):
                        text_to_add = next_title if next_title else next_content
                        if text_to_add:
                            if current.get("content"):
                                current["content"] += "\n\n" + text_to_add
                            else:
                                current["content"] = text_to_add
                        j += 1
                        continue
                    else:
                        break
                # 3. Если уровень больше, но это не пункт - пропускаем (может быть подпункт)
                j += 1
            
            # Если нашли пункты для прикрепления
            if items_to_attach:
                # Обрабатываем вложенные пункты (подпункты) - прикрепляем подпункты (1), 2), 3)) к основным пунктам (1., 2., 3.)
                processed_items = []
                used_sub_items = set()  # Отслеживаем подпункты, которые уже были использованы
                
                for item_idx, item in enumerate(items_to_attach):
                    item_copy = item.copy()
                    item_title = item.get("title", "").strip()
                    item_level = item.get("level", 0)
                    
                    # Пропускаем подпункты, которые уже были прикреплены к предыдущим основным пунктам
                    if _is_sub_item(item_title) and id(item) in used_sub_items:
                        continue
                    
                    # Проверяем content пункта на наличие буквенных подпунктов (а), б), в))
                    item_content = item_copy.get("content", "").strip()
                    if item_content:
                        letter_sub_items = _extract_letter_sub_items_from_content(item_content)
                        if letter_sub_items:
                            # Разделяем content на текст до подпунктов и сами подпункты
                            first_sub_item_title = letter_sub_items[0].get("title", "").strip()
                            first_sub_item_pos = item_content.find(first_sub_item_title)
                            
                            if first_sub_item_pos > 0:
                                # Сохраняем текст до первого подпункта в content
                                text_before_sub_items = item_content[:first_sub_item_pos].strip()
                                item_copy["content"] = text_before_sub_items
                            else:
                                # Если подпункты начинаются с самого начала, очищаем content
                                item_copy["content"] = ""
                            
                            # Устанавливаем правильный уровень для буквенных подпунктов
                            for sub_item in letter_sub_items:
                                sub_item["level"] = item_level + 1
                            
                            # Добавляем буквенные подпункты как children
                            if not item_copy.get("children"):
                                item_copy["children"] = []
                            item_copy["children"].extend(letter_sub_items)
                    
                    # Проверяем, является ли это основным пунктом (1., 2., 3.)
                    if _is_main_item(item_title):
                        # Ищем подпункты после этого основного пункта
                        # Сначала ищем среди следующих пунктов в items_to_attach
                        sub_items = []
                        for k in range(item_idx + 1, len(items_to_attach)):
                            next_item = items_to_attach[k]
                            next_title = next_item.get("title", "").strip()
                            
                            # Если это подпункт (1), 2), 3))
                            if _is_sub_item(next_title):
                                sub_item_copy = next_item.copy()
                                sub_item_copy["level"] = item_level + 1
                                sub_items.append(sub_item_copy)
                                used_sub_items.add(id(next_item))
                            # Если это следующий основной пункт - прекращаем
                            elif _is_main_item(next_title):
                                break
                            # Если это не подпункт и не основной пункт - прекращаем
                            else:
                                break
                        
                        # Также ищем подпункты в следующих узлах processed_children, которые еще не были обработаны
                        for k in range(j, len(processed_children)):
                            next_node = processed_children[k]
                            next_title = next_node.get("title", "").strip()
                            next_level = next_node.get("level", 0)
                            
                            # Если это подпункт (1), 2), 3))
                            if _is_sub_item(next_title):
                                # Проверяем, что уровень подходит
                                if next_level >= item_level and next_level <= item_level + 1:
                                    sub_item_copy = next_node.copy()
                                    sub_item_copy["level"] = item_level + 1
                                    sub_items.append(sub_item_copy)
                                    continue
                                else:
                                    break
                            
                            # Если это следующий основной пункт - прекращаем
                            if _is_main_item(next_title):
                                break
                            
                            # Если это заголовок статьи - прекращаем
                            if _is_article_heading(next_title):
                                break
                            
                            # Если уровень меньше - это возврат к более высокому уровню
                            if next_level < item_level:
                                break
                            
                            # Если это не подпункт и не основной пункт, прекращаем поиск подпунктов
                            if not _is_numbered_item(next_title):
                                break
                        
                        # Если нашли подпункты, добавляем их как children
                        if sub_items:
                            item_copy["children"] = sub_items
                        else:
                            item_copy["children"] = []
                    else:
                        item_copy["children"] = []
                    
                    processed_items.append(item_copy)
                
                current["children"] = processed_items
                result_children.append(current)
                i = j  # Пропускаем все прикрепленные узлы
                continue
            # Если добавили content, но не было пунктов
            elif current.get("content"):
                result_children.append(current)
                i = j if j > i + 1 else i + 1
                continue
        
        result_children.append(current)
        i += 1
    
    node["children"] = result_children
    return node

# ---------- 2) Система очистки текста ----------

def load_cleaning_patterns(pattern_file: str = "возможные_паттерны_для_очистки.json") -> List[Dict[str, str]]:
    """Загружает паттерны для очистки из JSON файла"""
    import os
    import json
    
    # Пытаемся найти файл в текущей директории или рядом со скриптом
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pattern_path = os.path.join(script_dir, pattern_file)
    
    if not os.path.exists(pattern_path):
        # Если файл не найден, возвращаем пустой список
        print(f"[WARNING] Файл с паттернами очистки не найден: {pattern_path}")
        return []
    
    try:
        with open(pattern_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("patterns", [])
    except Exception as e:
        print(f"[WARNING] Ошибка при загрузке паттернов очистки: {e}")
        return []

def clean_text_with_patterns(text: str, patterns: List[Dict[str, str]] = None) -> str:
    """Очищает текст используя паттерны из JSON файла"""
    if patterns is None:
        patterns = load_cleaning_patterns()
    
    if not patterns:
        return text
    
    cleaned_text = text
    for pattern_info in patterns:
        try:
            regex_pattern = pattern_info.get("regex", "")
            if regex_pattern:
                # Компилируем паттерн
                compiled_pattern = re.compile(regex_pattern)
                # Удаляем совпадения
                cleaned_text = compiled_pattern.sub("", cleaned_text)
        except Exception as e:
            # Если паттерн некорректный, пропускаем его
            print(f"[WARNING] Ошибка при применении паттерна '{pattern_info.get('name', 'unknown')}': {e}")
            continue
    
    # Удаляем множественные пустые строки (более 2 подряд)
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
    # Удаляем пробелы в начале и конце
    cleaned_text = cleaned_text.strip()
    
    return cleaned_text

def clean_tree_with_patterns(node: Dict[str, Any], patterns: List[Dict[str, str]] = None):
    """Рекурсивно очищает content всех узлов дерева используя паттерны"""
    if patterns is None:
        patterns = load_cleaning_patterns()
    
    # Очищаем content текущего узла
    if "content" in node and node["content"]:
        node["content"] = clean_text_with_patterns(node["content"], patterns)
    
    # Рекурсивно обрабатываем children
    if "children" in node and node["children"]:
        for child in node["children"]:
            clean_tree_with_patterns(child, patterns)

# ---------- 3) Система обработчиков документов ----------

class DocumentProcessor(ABC):
    """Базовый класс для обработчиков документов"""
    
    def cached_markdown(self, path: str) -> str:
        cache = getattr(self, "_instance_markdown_cache", None)
        if cache is None:
            cache = {}
            self._instance_markdown_cache = cache
        key = str(Path(path).resolve())
        if key not in cache:
            cache[key] = self.convert_to_markdown(key)
        return cache[key]
    
    @abstractmethod
    def can_process(self, path: str) -> bool:
        """Проверяет, может ли обработчик обработать данный документ"""
        pass
    
    @abstractmethod
    def process(self, path: str) -> Dict[str,Any]:
        """Обрабатывает документ и возвращает дерево JSON"""
        pass
    
    _UNICODE_SUP_TRANS = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')

    def convert_to_markdown(self, path: str, apply_pdf_footnotes: bool = True) -> str:
        """Универсальный метод преобразования в markdown через markitdown.
        Для DOCX — вызывает mammoth напрямую, чтобы сохранить суперскрипт-сноски.
        Для PDF — после markitdown отдельно «вытаскивает» суперскрипт-сноски через
        pdfplumber (markitdown теряет инфу о размере шрифта, и сноска ¹ приклеивается
        к номеру: «Статья 67¹» → «Статья 671»).

        apply_pdf_footnotes=False ускоряет большие учебники (сноски правового
        формата там обычно не нужны).
        """
        if path.lower().endswith('.docx'):
            return self._docx_to_markdown_with_footnotes(path)
        md_obj = MarkItDown(enable_plugins=False)
        res = md_obj.convert(path)
        text = self._normalize_unicode_superscripts((res.text_content or "").strip())
        if path.lower().endswith('.pdf') and apply_pdf_footnotes:
            try:
                # Полный pdfplumber-проход по сноскам дорог на больших учебниках.
                # Для документов >80 стр. пропускаем — markitdown + unicode-sup уже хватает.
                import pdfplumber
                with pdfplumber.open(path) as _pdf:
                    n_pages = len(_pdf.pages)
                if n_pages > 80:
                    logger.info(
                        "Skipping PDF superscript footnote pass for large document "
                        f"({n_pages} pages)"
                    )
                else:
                    text = self._apply_pdf_superscript_footnotes(path, text)
            except Exception as e:
                # PDF-фолбэк не должен ломать общий пайплайн.
                logger.warning(f"PDF superscript post-processing failed: {e}")
        return text

    def _docx_to_markdown_with_footnotes(self, path: str) -> str:
        """DOCX → Markdown с сохранением суперскрипт-сносок как (см.сноску N)."""
        import mammoth
        from markitdown.converter_utils.docx.pre_process import pre_process_docx
        import markdownify as _md

        with open(path, 'rb') as f:
            processed = pre_process_docx(f)
            html = mammoth.convert_to_html(processed).value

        html = re.sub(r'<sup>(.*?)</sup>', r' (см.сноску \1)', html)

        md = _md.markdownify(html, heading_style='ATX')
        return self._normalize_unicode_superscripts(md.strip())

    @staticmethod
    def _normalize_unicode_superscripts(text: str) -> str:
        """Заменяет Unicode-суперскрипт-цифры (¹²³…) на (см.сноску N).

        Кроме единиц измерения: «км²», «м³», «см²» — там ² и ³ это степень, а не
        сноска. Раньше «площадь 17 125 191 км²» превращалась в «км (см.сноску 2)»,
        и вопрос уходил в генерацию с искажённой единицей.
        """

        def _replace(m: "re.Match") -> str:
            sup = m.group()
            prev = text[m.start() - 1] if m.start() else ""
            if sup in ("²", "³") and prev.isalpha():
                return sup
            return f' (см.сноску {sup.translate(DocumentProcessor._UNICODE_SUP_TRANS)})'

        return re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹]+', _replace, text)

    # --- PDF-сноски (суперскрипты, которые markitdown «склеивает» как обычные цифры) ---

    @staticmethod
    def _collect_pdf_footnote_replacements(path: str) -> list:
        """Через pdfplumber находит группы подряд идущих цифр-суперскриптов и для каждой
        возвращает пару (left_context, sup_digits). Где left_context — короткий «обычный»
        текст слева от сноски (например, "Статья 67"), а sup_digits — сами цифры ("1").
        Признак суперскрипта: размер шрифта заметно меньше «обычного» в этой строке
        (ratio < 0.85), И смещение baseline вверх (y0 выше). Доп. требование: символ — цифра.

        Возвращает список уникальных пар без сортировки.
        """
        import pdfplumber

        SUP_SIZE_RATIO = 0.85           # «маленький» шрифт = меньше 85% от строкового медианного
        SUP_BASELINE_LIFT_PT = 1.5      # baseline должна быть приподнята минимум на 1.5pt
        LEFT_CONTEXT_LEN = 30
        results: list = []
        seen = set()

        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                chars = page.chars or []
                if not chars:
                    continue
                # группируем chars в строки по близкому top
                rows: list[list[dict]] = []
                cur_row: list[dict] = []
                last_top: float | None = None
                for c in chars:
                    top = c.get("top")
                    if top is None:
                        continue
                    if last_top is None or abs(top - last_top) <= 3.0:
                        cur_row.append(c)
                    else:
                        if cur_row:
                            rows.append(cur_row)
                        cur_row = [c]
                    last_top = top
                if cur_row:
                    rows.append(cur_row)

                for row in rows:
                    sizes = [c.get("size") or 0 for c in row]
                    sizes_sorted = sorted(s for s in sizes if s > 0)
                    if not sizes_sorted:
                        continue
                    median_size = sizes_sorted[len(sizes_sorted) // 2]
                    median_y0 = sorted(c.get("y0", 0) for c in row)[len(row) // 2]
                    row_text = "".join(c.get("text", "") for c in row)

                    i = 0
                    n = len(row)
                    while i < n:
                        c = row[i]
                        text_i = c.get("text", "")
                        size_i = c.get("size") or 0
                        y0_i = c.get("y0") or 0
                        is_sup = (
                            text_i.isdigit()
                            and median_size > 0
                            and size_i / median_size < SUP_SIZE_RATIO
                            and (y0_i - median_y0) >= SUP_BASELINE_LIFT_PT
                        )
                        if not is_sup:
                            i += 1
                            continue
                        # собираем подряд идущие суперскрипт-цифры
                        sup_digits = ""
                        j = i
                        while j < n:
                            cj = row[j]
                            if (
                                (cj.get("text", "") or "").isdigit()
                                and (cj.get("size") or 0) / (median_size or 1) < SUP_SIZE_RATIO
                                and ((cj.get("y0") or 0) - median_y0) >= SUP_BASELINE_LIFT_PT
                            ):
                                sup_digits += cj.get("text", "")
                                j += 1
                            else:
                                break
                        # left_context — последние LEFT_CONTEXT_LEN не-суперскрипт-символов
                        # перед позицией i в этой строке (с обрезкой пробелов по краям).
                        left_context = row_text[:i][-LEFT_CONTEXT_LEN:].rstrip()
                        # отрезаем по последнему слову/токену слева до пробела:
                        # для замен надёжнее работать с устойчивым словом ("Статья 67"),
                        # а не с длинным куском.
                        m_tok = re.search(r'(\S+(?:\s\S+){0,2})\s*$', left_context)
                        if m_tok:
                            left_context = m_tok.group(1)
                        if left_context and sup_digits:
                            key = (left_context, sup_digits)
                            if key not in seen:
                                seen.add(key)
                                results.append(key)
                        i = j
        return results

    def _apply_pdf_superscript_footnotes(self, path: str, md: str) -> str:
        """Применяет к markdown-тексту найденные суперскрипты как (см.сноску N).
        Безопасно: меняем только точное вхождение «<left><digits>» (без соседних цифр
        слева или справа) — чтобы не задеть случайные совпадения с обычными числами."""
        replacements = self._collect_pdf_footnote_replacements(path)
        if not replacements:
            return md
        for left, digits in replacements:
            # «Статья 67» уже содержит цифры в left — нам нужно гарантировать, что
            # после digits НЕ идёт ещё одна цифра (иначе зацепим случайные числа).
            pattern = re.compile(
                r'(?<![0-9])' + re.escape(left) + re.escape(digits) + r'(?![0-9])'
            )
            md = pattern.sub(f"{left} (см.сноску {digits})", md)
        return md

    def apply_cleaning_patterns(self, tree: Dict[str, Any]) -> Dict[str, Any]:
        """Применяет паттерны очистки к дереву документа"""
        clean_tree_with_patterns(tree)
        return tree

# ---------- 3) Обработчик для документов Гаранта (ГАРАНТ) ----------

class GarantProcessor(DocumentProcessor):
    """Обработчик для документов системы ГАРАНТ"""
    
    def can_process(self, path: str) -> bool:
        """Определяет документы Гаранта по характерным признакам"""
        try:
            md = self.cached_markdown(path)
            has_garant_marker = bool(re.search(r'^\s*ГАРАНТ\s*:', md, re.MULTILINE | re.IGNORECASE))
            has_garant_links = "ivo.garant.ru" in md or "garant.ru" in md
            has_chapters = bool(re.search(r'Глава\s+\d+', md, re.IGNORECASE))
            has_articles = bool(re.search(r'Статья\s+\d+', md, re.IGNORECASE))
            has_legal_structure = has_chapters and has_articles
            return has_garant_marker or (has_garant_links and has_legal_structure)
        except Exception:
            return False
    
    def _clean_garant_text(self, text: str) -> str:
        """Удаляет служебные строки ГАРАНТ из текста"""
        lines = text.splitlines()
        cleaned_lines = []
        
        # Паттерны для удаления
        garant_pattern = re.compile(r'^\s*ГАРАНТ\s*:?\s*$', re.IGNORECASE)
        # Паттерн для "См. комментарии к статье X" (с возможными вариантами окончания)
        comment_pattern = re.compile(r'^\s*См\.\s+комментарии\s+к\s+статье\s+\d+.*?$', re.IGNORECASE)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            # Пропускаем строки "ГАРАНТ:" или "ГАРАНТ"
            if garant_pattern.match(line):
                i += 1
                # Пропускаем пустые строки после "ГАРАНТ:"
                while i < len(lines) and not lines[i].strip():
                    i += 1
                # Пропускаем строку "См. комментарии ..." если она идет после "ГАРАНТ:"
                if i < len(lines) and comment_pattern.match(lines[i]):
                    i += 1
                    # Пропускаем пустые строки после комментария
                    while i < len(lines) and not lines[i].strip():
                        i += 1
                continue
            
            # Пропускаем строки "См. комментарии ..." отдельно
            if comment_pattern.match(line):
                i += 1
                # Пропускаем пустые строки после комментария
                while i < len(lines) and not lines[i].strip():
                    i += 1
                continue
            
            cleaned_lines.append(line)
            i += 1
        
        return "\n".join(cleaned_lines)
    
    def _clean_tree_content(self, node: Dict[str,Any]):
        """Рекурсивно очищает контент узлов дерева от служебных строк ГАРАНТ"""
        if "content" in node and node["content"]:
            node["content"] = self._clean_garant_text(node["content"])
        for child in node.get("children", []):
            self._clean_tree_content(child)
    
    def process(self, path: str) -> Dict[str,Any]:
        """Обрабатывает документ Гаранта"""
        md = self.cached_markdown(path)
        
        # Очищаем от служебных строк ГАРАНТ перед обработкой
        md = self._clean_garant_text(md)
        
        # Дополнительная обработка для документов Гаранта:
        # - Улучшаем распознавание заголовков глав и статей
        # - Сохраняем ссылки в формате [текст](url)
        
        # Паттерны для глав и статей.
        # Учитываем, что mammoth может прислать заголовок жирным
        # (например, "**Статья 14.** Понятие преступления") — поэтому проверяем
        # после _strip_md_emphasis(), а в выходной заголовок пишем уже очищенную строку.
        chapter_pattern = re.compile(r'^(Глава\s+\d+[\.:]?\s*.+?)$', re.IGNORECASE)

        # Преобразуем в заголовки markdown
        lines = md.splitlines()
        processed_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = _strip_md_emphasis(line.strip())
            # Проверяем на главу
            if chapter_pattern.match(stripped):
                processed_lines.append(f"# {stripped}")
                i += 1
                continue
            # Проверяем на статью
            if _is_standalone_article_line(stripped):
                processed_lines.append(f"## {stripped}")
                i += 1
                continue
            processed_lines.append(line)
            i += 1
        
        md_processed = "\n".join(processed_lines)
        tree = parse_markdown_to_tree(md_processed)
        
        # Дополнительная очистка уже распарсенного дерева (на случай если что-то пропустили)
        self._clean_tree_content(tree)
        
        # Применяем паттерны очистки из JSON файла
        tree = self.apply_cleaning_patterns(tree)
        
        return tree

# ---------- 4) Обработчик для документов Консультанта (КонсультантПлюс) ----------

class ConsultantProcessor(DocumentProcessor):
    """Обработчик для документов системы КонсультантПлюс"""
    
    def can_process(self, path: str) -> bool:
        """Определяет документы Консультанта по характерным признакам"""
        try:
            md = self.cached_markdown(path)
            has_consultant_links = (
                "consultant.ru" in md
                or "КонсультантПлюс" in md
            )
            has_article_structure = bool(
                re.search(r'Статья\s+\d+', md, re.IGNORECASE)
            )
            return has_consultant_links and has_article_structure
        except Exception:
            return False
    
    def process(self, path: str) -> Dict[str,Any]:
        """Обрабатывает документ Консультанта"""
        md = self.cached_markdown(path)
        
        # Дополнительная обработка для документов Консультанта:
        # - Улучшаем распознавание разделов с римскими цифрами
        # - Распознавание подзаголовков
        # - Распознавание разделов и подразделов
        
        lines = md.splitlines()
        processed_lines = []
        i = 0
        
        # Паттерны для распознавания заголовков
        section_pattern = re.compile(r'^(Раздел\s+\d+[\.:]?\s*.+)$', re.IGNORECASE)
        subsection_pattern = re.compile(r'^(Подраздел\s+\d+[\.:]?\s*.+)$', re.IGNORECASE)
        # Глава с номером: "Глава 1", "Глава 2" и т.д. (может быть с точкой или без)
        chapter_pattern = re.compile(r'^(Глава\s+\d+[\.:]?\s*.+)$', re.IGNORECASE)
        # Римские цифры с точкой: I., II., III., IV., V.
        roman_section_pattern = re.compile(r'^([IVXLCDM]+)\.\s+(.+)$', re.IGNORECASE)
        # Подзаголовки (начинаются с заглавной буквы, короткие, без точки в конце)
        subtitle_pattern = re.compile(r'^([А-ЯЁ][А-Яа-яё\s]{5,80})$')
        
        while i < len(lines):
            raw_line = lines[i].strip()
            # mammoth/markdownify часто оборачивают заголовки статей/разделов в **bold** —
            # для всех проверок используем строку без markdown-обёртки, а в выходные
            # заголовки тоже пишем уже очищенную строку, чтобы parse_markdown_to_tree
            # корректно превратил её в title узла без ведущих "**".
            line = _strip_md_emphasis(raw_line)

            # Пропускаем пустые строки
            if not line:
                processed_lines.append("")
                i += 1
                continue
            
            # Пропускаем строки таблиц (начинаются с |)
            if line.startswith('|'):
                processed_lines.append(lines[i])
                i += 1
                continue
            
            # Проверяем на раздел с римской цифрой
            roman_match = roman_section_pattern.match(line)
            if roman_match:
                processed_lines.append(f"# {line}")
                i += 1
                continue
            
            # Проверяем на Главу (может быть на нескольких строках)
            if chapter_pattern.match(line):
                chapter_text = line
                # Собираем следующие строки, если они являются продолжением заголовка главы
                j = i + 1
                while j < len(lines):
                    next_line = _strip_md_emphasis(lines[j].strip())
                    # Если следующая строка пустая или начинается с "Статья" или "Глава" - прекращаем
                    if not next_line or _is_standalone_article_line(next_line) or chapter_pattern.match(next_line):
                        break
                    # Если следующая строка - таблица или другой заголовок - прекращаем
                    if next_line.startswith('|') or section_pattern.match(next_line) or subsection_pattern.match(next_line):
                        break
                    # Если следующая строка начинается с заглавной буквы и короткая - возможно продолжение
                    if len(next_line) < 100 and next_line.isupper():
                        chapter_text += " " + next_line
                        j += 1
                    else:
                        break
                processed_lines.append(f"# {chapter_text}")
                i = j
                continue
            
            # Проверяем на Статью
            if _is_standalone_article_line(line):
                processed_lines.append(f"## {line}")
                i += 1
                continue
            
            # Проверяем на обычный раздел
            if section_pattern.match(line):
                processed_lines.append(f"# {line}")
                i += 1
                continue
            
            # Проверяем на подраздел
            if subsection_pattern.match(line):
                processed_lines.append(f"## {line}")
                i += 1
                continue
            
            # Проверяем на подзаголовок (короткая строка, начинается с заглавной, без точки)
            # Но не пронумерованный пункт
            if (subtitle_pattern.match(line) and 
                not _is_numbered_item(line) and 
                len(line) < 100 and
                not line.endswith('.') and
                not line.endswith(',') and
                i + 1 < len(lines) and  # Есть следующая строка
                lines[i + 1].strip()):  # Следующая строка не пустая
                # Проверяем, что следующая строка не является заголовком
                next_line = _strip_md_emphasis(lines[i + 1].strip())
                if not (roman_section_pattern.match(next_line) or 
                       section_pattern.match(next_line) or
                       subsection_pattern.match(next_line) or
                       chapter_pattern.match(next_line) or
                       _is_standalone_article_line(next_line)):
                    processed_lines.append(f"## {line}")
                    i += 1
                    continue
            
            processed_lines.append(lines[i])
            i += 1
        
        md_processed = "\n".join(processed_lines)
        tree = parse_markdown_to_tree(md_processed)
        return self.apply_cleaning_patterns(tree)

# ---------- 5) Обработчик для DOCX со стилями заголовков ----------

class DocxWithHeadingStylesProcessor(DocumentProcessor):
    """Обработчик для DOCX со стилями заголовков (Heading 1, Heading 2, etc.)"""
    
    def can_process(self, path: str) -> bool:
        """Проверяет наличие стилей заголовков в DOCX"""
        if not path.lower().endswith('.docx'):
            return False
        try:
            doc = Document(path)
            # Проверяем наличие стилей заголовков
            heading_styles = set()
            for p in doc.paragraphs:
                style_name = p.style.name if p.style else ""
                if re.match(r'Heading\s+\d+', style_name, re.IGNORECASE):
                    heading_styles.add(style_name)
            return len(heading_styles) > 0
        except:
            return False
    
    def process(self, path: str) -> Dict[str,Any]:
        """Обрабатывает DOCX со стилями заголовков через markitdown"""
        md = self.convert_to_markdown(path)
        # markitdown должен правильно обработать стили заголовков
        tree = parse_markdown_to_tree(md)
        return self.apply_cleaning_patterns(tree)

# ---------- 6) Обработчик для DOCX со стилями, не наследуемыми от заголовков ----------

class DocxWithCustomStylesProcessor(DocumentProcessor):
    """Обработчик для DOCX с кастомными стилями, не наследуемыми от заголовков"""
    
    def can_process(self, path: str) -> bool:
        """Проверяет наличие кастомных стилей (не Heading)"""
        if not path.lower().endswith('.docx'):
            return False
        try:
            doc = Document(path)
            # Проверяем наличие outline levels или кастомных стилей
            has_outline = False
            has_custom_styles = False
            for p in doc.paragraphs:
                # Проверяем outline level
                el = p._element
                outline_vals = el.xpath(".//w:outlineLvl/@w:val")
                if outline_vals:
                    has_outline = True
                # Проверяем кастомные стили (не Heading и не Normal)
                style_name = (p.style.name or "").strip()
                if style_name and not re.match(r'Heading\s+\d+|Normal', style_name, re.IGNORECASE):
                    has_custom_styles = True
            return has_outline or has_custom_styles
        except:
            return False
    
    def process(self, path: str) -> Dict[str,Any]:
        """Обрабатывает DOCX с кастомными стилями через fallback метод"""
        # Используем fallback метод, который анализирует outline levels и типографику
        md = self._docx_fallback_to_markdown(path)
        tree = parse_markdown_to_tree(md)
        return self.apply_cleaning_patterns(tree)
    
    def _docx_fallback_to_markdown(self, path: str) -> str:
        """Превращает DOCX в псевдо-Markdown используя outline levels и типографику"""
        doc = Document(path)
        infos = self._gather_paragraph_info(doc)
        lines = []
        for is_h, lvl, text in self._score_and_level_candidates(infos):
            if is_h:
                lines.append("#"*lvl + " " + text)
            else:
                lines.append(text)
            lines.append("")  # пустая строка-разделитель
        return self._normalize_unicode_superscripts("\n".join(lines).strip())
    
    def _gather_paragraph_info(self, doc: Document) -> List[Any]:
        """Собирает информацию о параграфах"""
        from dataclasses import dataclass
        
        @dataclass
        class PInfo:
            text: str
            lvl_heading_style: Optional[int]
            lvl_outline: Optional[int]
            lvl_list: Optional[int]
            font_pt: Optional[float]
            bold_ratio: float
            upper_ratio: float
            is_short: bool
        
        infos: List[PInfo] = []
        for p in doc.paragraphs:
            text, runs = self._run_text_and_attrs(p)
            if not text:
                continue
            bold_chars = sum(len(t) for (t,b,_) in runs if b)
            total_chars = sum(len(t) for (t,_,_) in runs)
            bold_ratio = (bold_chars / total_chars) if total_chars else 0.0
            
            letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
            uppers = [c for c in letters if c.isupper()]
            upper_ratio = (len(uppers) / len(letters)) if letters else 0.0
            
            font_pts = [sz for (_,_,sz) in runs if sz is not None]
            font_pt = max(font_pts) if font_pts else None
            
            infos.append(PInfo(
                text=text,
                lvl_heading_style=self._heading_style_level(p),
                lvl_outline=self._outline_level(p),
                lvl_list=self._list_ilvl(p),
                font_pt=font_pt,
                bold_ratio=bold_ratio,
                upper_ratio=upper_ratio,
                is_short=(len(text) <= 120)
            ))
        return infos
    
    def _run_text_and_attrs(self, p) -> Tuple[str, List[Tuple[str, bool, Optional[float]]]]:
        txt = []
        runs = []
        for r in p.runs:
            t = r.text or ""
            if not t: continue
            if r.font.superscript:
                t = f" (см.сноску {t})"
            txt.append(t)
            sz = r.font.size.pt if r.font.size is not None else None
            runs.append((t, bool(r.bold), sz))
        return "".join(txt).strip(), runs
    
    def _outline_level(self, p) -> Optional[int]:
        el = p._element
        vals = el.xpath(".//w:outlineLvl/@w:val")
        if vals:
            try:
                return int(vals[0]) + 1
            except Exception:
                return None
        return None
    
    def _list_ilvl(self, p) -> Optional[int]:
        el = p._element
        vals = el.xpath(".//w:numPr/w:ilvl/@w:val")
        if vals:
            try:
                return int(vals[0])
            except Exception:
                return None
        return None
    
    def _heading_style_level(self, p) -> Optional[int]:
        try:
            name = (p.style.name or "").strip()
        except Exception:
            return None
        m = re.fullmatch(r"Heading\s+([1-9])", name, flags=re.IGNORECASE)
        return int(m.group(1)) if m else None
    
    def _rank_font_sizes(self, infos: List[Any]) -> Dict[float,int]:
        sizes = sorted({i.font_pt for i in infos if i.font_pt is not None}, reverse=True)
        if not sizes:
            return {}
        return {sz: lvl for lvl, sz in enumerate(sizes[:6], start=1)}
    
    def _calibrate_list_base(self, infos: List[Any]) -> int:
        lvls = [i.lvl_list for i in infos if i.lvl_list is not None]
        if not lvls:
            return 1
        return 1 - min(lvls)
    
    def _score_and_level_candidates(self, infos: List[Any]) -> List[Tuple[bool, int, str]]:
        font_rank = self._rank_font_sizes(infos)
        base = self._calibrate_list_base(infos)
        
        out: List[Tuple[bool,int,str]] = []
        for i in infos:
            cand_levels = []
            
            if i.lvl_outline is not None:
                cand_levels.append(("outline", i.lvl_outline, 1.0))
            if i.lvl_heading_style is not None:
                cand_levels.append(("hstyle", i.lvl_heading_style, 0.95))
            if i.lvl_list is not None:
                cand_levels.append(("list", max(1, min(6, i.lvl_list + base)), 0.8))
            
            if i.font_pt is not None and font_rank:
                if i.font_pt in font_rank:
                    cand_levels.append(("font", font_rank[i.font_pt], 0.7))
            if i.is_short and (i.bold_ratio >= 0.6 or i.upper_ratio >= 0.6):
                cand_levels.append(("typo", 3, 0.55))
            
            if cand_levels:
                best = max(cand_levels, key=lambda t: t[2])
                lvl = max(1, min(6, best[1]))
                out.append((True, lvl, i.text))
            else:
                out.append((False, 0, i.text))
        return out

# ---------- 7) Обработчик для ГОСТов ----------

class GOSTProcessor(DocumentProcessor):
    """Обработчик для документов ГОСТ (ГОСТы)"""
    
    def can_process(self, path: str) -> bool:
        """Определяет документы ГОСТ по характерным признакам"""
        try:
            md = self.convert_to_markdown(path)
            # Характерные признаки ГОСТов:
            # - наличие "ГОСТ" в начале документа
            # - наличие "МЕЖГОСУДАРСТВЕННЫЙ СТАНДАРТ" или "ГОСУДАРСТВЕННЫЙ СТАНДАРТ"
            # - структура с разделами типа "1. ОБЩИЕ ПОЛОЖЕНИЯ", "2. СОСТАВ И СОДЕРЖАНИЕ"
            has_gost = bool(re.search(r'^ГОСТ\s+\d+', md, re.MULTILINE))
            has_standard = "МЕЖГОСУДАРСТВЕННЫЙ СТАНДАРТ" in md or "ГОСУДАРСТВЕННЫЙ СТАНДАРТ" in md
            has_gost_structure = bool(re.search(r'^\d+\.\s+[А-ЯЁ\s]+$', md, re.MULTILINE))
            return has_gost or (has_standard and has_gost_structure)
        except:
            return False
    
    def process(self, path: str) -> Dict[str,Any]:
        """Обрабатывает документ ГОСТ"""
        md = self.convert_to_markdown(path)
        
        lines = md.splitlines()
        processed_lines = []
        i = 0
        
        # Паттерны для распознавания структуры ГОСТа
        # Разделы: "1. ОБЩИЕ ПОЛОЖЕНИЯ", "2. СОСТАВ И СОДЕРЖАНИЕ"
        section_pattern = re.compile(r'^(\d+)\.\s+([А-ЯЁ\s]+)$')
        # Подразделы: "1.1. Текст", "2.3. Текст", "2.4.1. Текст" (многоуровневые)
        subsection_pattern = re.compile(r'^(\d+)(?:\.(\d+))+(?:\.\s+(.+))?$')
        # Пункты: "1) текст", "2) текст"
        item_pattern = re.compile(r'^(\d+)\)\s+(.+)$')
        # Подпункты: "а) текст", "б) текст"
        subitem_pattern = re.compile(r'^([а-яА-Я])\)\s+(.+)$')
        
        while i < len(lines):
            line = lines[i].strip()
            
            # Пропускаем пустые строки
            if not line:
                processed_lines.append("")
                i += 1
                continue
            
            # Пропускаем строки таблиц
            if line.startswith('|'):
                processed_lines.append(lines[i])
                i += 1
                continue
            
            # Проверяем на раздел (например, "1. ОБЩИЕ ПОЛОЖЕНИЯ")
            section_match = section_pattern.match(line)
            if section_match:
                processed_lines.append(f"# {line}")
                i += 1
                continue
            
            # Проверяем на подраздел (например, "1.1. Текст", "2.4.1. Текст")
            # Сначала проверяем многоуровневые подразделы (2.4.1., 2.4.2. и т.д.)
            if re.match(r'^\d+\.\d+\.\d+\.', line):
                # Подраздел третьего уровня
                processed_lines.append(f"### {line}")
                i += 1
                continue
            elif re.match(r'^\d+\.\d+\.', line):
                # Подраздел второго уровня
                processed_lines.append(f"## {line}")
                i += 1
                continue
            
            # Проверяем на пункт (например, "1) текст")
            # Но сначала проверяем, нет ли в строке нескольких пунктов через точку с запятой
            if ';' in line and re.search(r'\d+\)\s+', line):
                # Если в строке несколько пунктов, разделяем их
                # Ищем все пункты в строке
                items_in_line = re.findall(r'(\d+\)\s+[^;]+(?:;|$))', line)
                if len(items_in_line) > 1:
                    # Разделяем на отдельные пункты
                    for item in items_in_line:
                        item_clean = item.strip()
                        if item_clean:
                            processed_lines.append(f"#### {item_clean}")
                    i += 1
                    continue
            
            item_match = item_pattern.match(line)
            if item_match:
                processed_lines.append(f"#### {line}")
                i += 1
                continue
            
            # Проверяем на подпункт (например, "а) текст")
            subitem_match = subitem_pattern.match(line)
            if subitem_match:
                processed_lines.append(f"##### {line}")
                i += 1
                continue
            
            # Обычный текст
            processed_lines.append(lines[i])
            i += 1
        
        md_processed = "\n".join(processed_lines)
        tree = parse_markdown_to_tree(md_processed)
        return self.apply_cleaning_patterns(tree)

# ---------- 8) Обработчик школьных учебников ----------

_TEXTBOOK_CHAPTER_RE = re.compile(
    r'^(?:Глава|ГЛАВА)\s+([IVXLCDM]+|\d+)\.?\s*(.*)$',
    re.IGNORECASE,
)
# Bare «§ 1» / «§ 2–3» (title on following lines) or single-line «§ 1. Title»
# Optional leading page number glued to §: «19§ 2–3. …»
_TEXTBOOK_SECTION_BARE_RE = re.compile(
    r'^(?:\d{1,3})?§\s*(\d+(?:\s*[–—\-]\s*\d+)?)\s*$',
)
_TEXTBOOK_SECTION_FULL_RE = re.compile(
    r'^(?:\d{1,3})?§\s*(\d+(?:\s*[–—\-]\s*\d+)?)\.\s+(.+)$',
)
_TEXTBOOK_ALLCAPS_SUBHEAD_RE = re.compile(
    r'^[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9\s\-–—«»""]{2,80}$',
)
_TEXTBOOK_KNOWN_SUBHEADS = frozenset({
    "КЛЮЧЕВЫЕ ПОНЯТИЯ",
    "ВСПОМНИТЕ",
    "ПРОВЕРЯЕМ СЕБЯ",
    "ЗАГАДКА ЧЕЛОВЕКА",
    "ПОДВЕДЁМ ИТОГИ",
    "ПОДВЕДЕМ ИТОГИ",
    "ВОПРОСЫ И ЗАДАНИЯ",
    "ДЛЯ ЛЮБОЗНАТЕЛЬНЫХ",
    "ЭТО ИНТЕРЕСНО",
    "РАБОТАЕМ С ИСТОЧНИКОМ",
    "ПРАКТИКУМ",
})
_TEXTBOOK_PAGE_NUM_RE = re.compile(r'^\d{1,3}$')
_TEXTBOOK_PRINT_MARK_RE = re.compile(r'^[0-9A-Fa-f]{4,8}$')
_TEXTBOOK_INDESIGN_RE = re.compile(r'\.indd\b', re.IGNORECASE)
_TEXTBOOK_UDK_RE = re.compile(r'^(УДК|ББК|ISBN)\b', re.IGNORECASE)
_TEXTBOOK_CID_BULLET_RE = re.compile(r'\(cid:\s*\d+\)', re.IGNORECASE)
# Soft hyphen at line break; tolerate whitespace before the hyphen (БРЭ / Medinsky).
_TEXTBOOK_HYPHEN_JOIN_RE = re.compile(
    r'([А-Яа-яЁёA-Za-z])[ \t]*-[ \t]*\n[ \t]*([а-яёa-z])',
)
# ALL-CAPS headword continuation across a soft hyphen (encyclopedia entries).
_CAPS_HYPHEN_JOIN_RE = re.compile(
    r'([А-ЯЁ])[ \t]*-[ \t]*\n[ \t]*([А-ЯЁ]{2,})',
)
# Строка, оборванная переносом, и строка-продолжение со строчной буквы.
_TEXTBOOK_TRAILING_HYPHEN_RE = re.compile(r'([А-Яа-яЁёA-Za-z])[ \t]*-[ \t]*$')
_TEXTBOOK_LOWER_START_RE = re.compile(r'^[а-яёa-z]')
# Stress marks decoded as digit 2/9 mid-word (ПУТОР2АКИ → ПУТОРАКИ).
# Two rules so "В 2АВСТРИИ" keeps the space (→ "В АВСТРИИ", not "ВАВСТРИИ").
_STRESS_DIGIT_RE = re.compile(r'([А-ЯЁа-яё])([29])(?=[А-ЯЁа-яё])')
# The stress mark is sometimes emitted with a space in front of it («МАШ 2ИНЫ»
# for МАШИ́НЫ). Dropping only the digit left «МАШ ИНЫ» — a headword torn in two.
# The space goes with the digit, unless what precedes it is a standalone short
# word: «В 2ИТАЛИИ», «ДИ 2КОЗИМО» are real word boundaries, not kerning.
_STRESS_DIGIT_SP_RE = re.compile(
    r'(?<![А-ЯЁа-яё])([А-ЯЁа-яё]{3,}) ([29])(?=[А-ЯЁа-яё])'
)
# Whatever the rule above deliberately skipped (a genuine word boundary such as
# «В 2ИТАЛИИ») still has to lose the digit — just not the space, or two words
# would be welded together.
_STRESS_DIGIT_SP_KEEP_RE = re.compile(r'([А-ЯЁа-яё] )([29])(?=[А-ЯЁа-яё])')


_TEXTBOOK_TABLE_MARKS_RE = re.compile(r'\s*\|\s*|\s*-{3,}\s*')


def _textbook_norm_ws(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or "").replace('\xa0', ' ')).strip()


def _textbook_heading_text(s: str) -> str:
    """Текст заголовка: markitdown может превратить строку заголовка в строку
    таблицы («§ 18. | | Хозяйственная | | система | --- |») — черты и отточия
    убираем. Только для заголовков: в теле такие строки — таблицы, их
    выбрасывает чанкер."""
    return _textbook_norm_ws(_TEXTBOOK_TABLE_MARKS_RE.sub(' ', s or ""))


def _textbook_norm_heading_key(s: str) -> str:
    """Normalize heading text for running-header dedup comparisons."""
    s = _textbook_norm_ws(s)
    s = re.sub(r'^(?:\d{1,3})?(§|Глава|ГЛАВА)\s*', lambda m: m.group(1).upper() + ' ', s, flags=re.I)
    s = s.replace('–', '-').replace('—', '-')
    return s.casefold()


def _textbook_is_allcaps_subhead(line: str) -> bool:
    s = _textbook_norm_ws(line)
    if not s or len(s) > 80:
        return False
    compact = s.upper().replace('Ё', 'Е')
    if compact in _TEXTBOOK_KNOWN_SUBHEADS:
        return True
    if not _TEXTBOOK_ALLCAPS_SUBHEAD_RE.match(s):
        return False
    # Skip list-like / prose-like lines promoted by PDF all-caps styling
    if any(ch in s for ch in ',;:·•') or s.endswith(('.', '!', '?')):
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 4:
        return False
    upper = sum(1 for c in letters if c.isupper())
    # Strict: essentially fully uppercase short heading
    return upper == len(letters) and 4 <= len(s) <= 60


def _textbook_is_junk_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _TEXTBOOK_PAGE_NUM_RE.match(s):
        return True
    if _TEXTBOOK_PRINT_MARK_RE.match(s):
        return True
    if _TEXTBOOK_INDESIGN_RE.search(s):
        return True
    if _TEXTBOOK_UDK_RE.match(s):
        return True
    low = s.casefold()
    if low.startswith('исключительные права'):
        return True
    if 'shutterstock' in low or 'fotodom' in low:
        return True
    if low.startswith('в оформлении обложки'):
        return True
    if re.match(r'^[оo]-?\d{1,3}$', low):  # ББК shelf code like О-28
        return True
    return False


def _textbook_strip_cover_letter_runs(lines: List[str]) -> List[str]:
    """Drop runs of single-character lines typical of vertical cover titles."""
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if len(s) == 1 and s.isalpha():
            j = i
            while j < n and len(lines[j].strip()) <= 1 and (
                not lines[j].strip() or lines[j].strip().isalpha()
            ):
                j += 1
            alpha_count = sum(1 for k in range(i, j) if len(lines[k].strip()) == 1)
            if alpha_count >= 4:
                i = j
                continue
        out.append(lines[i])
        i += 1
    return out


def _strip_stress_digits(md: str) -> str:
    """Remove OCR-style stress digits (2/9) embedded between Cyrillic letters."""
    md = _STRESS_DIGIT_RE.sub(r'\1', md)
    # Note the replacement drops the space as well — see _STRESS_DIGIT_SP_RE.
    md = _STRESS_DIGIT_SP_RE.sub(r'\1', md)
    md = _STRESS_DIGIT_SP_KEEP_RE.sub(r'\1', md)
    return md


def _textbook_join_hyphen_across_headers(text: str) -> str:
    """Склеить слово, разорванное переносом и вклинившимся колонтитулом.

    «правитель-» / «§ 2-3. Как устроено общество 19» / «ства)»: заголовок посреди
    слова — всегда колонтитул страницы, поэтому его выбрасываем. Иначе он рвёт
    и слово, и абзац, а обломок уезжает в отдельный чанк.
    """
    lines = text.splitlines()
    out: List[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        continuation = -1
        if _TEXTBOOK_TRAILING_HYPHEN_RE.search(line):
            skipped, j = 0, i + 1
            while j < n and skipped <= 2:
                nxt = lines[j].strip()
                if nxt:
                    if _TEXTBOOK_LOWER_START_RE.match(nxt):
                        continuation = j
                        break
                    skipped += 1
                j += 1
        if continuation >= 0:
            out.append(
                _TEXTBOOK_TRAILING_HYPHEN_RE.sub(r'\1', line) + lines[continuation].strip()
            )
            i = continuation + 1
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def _textbook_cleanup_text(md: str) -> str:
    """De-hyphenate, normalize spaces/bullets, drop junk lines."""
    # Join hyphenated line breaks before other processing
    md = _CAPS_HYPHEN_JOIN_RE.sub(r'\1\2', md)
    md = _TEXTBOOK_HYPHEN_JOIN_RE.sub(r'\1\2', md)
    md = _strip_stress_digits(md)
    md = md.replace('\xa0', ' ')
    md = _TEXTBOOK_CID_BULLET_RE.sub('•', md)
    lines = md.splitlines()
    lines = _textbook_strip_cover_letter_runs(lines)
    cleaned: List[str] = []
    for line in lines:
        raw = line
        s = line.strip()
        if _textbook_is_junk_line(s):
            continue
        # Collapse justification double spaces inside the line
        s = re.sub(r' {2,}', ' ', s)
        cleaned.append(s if s else "")
    # Collapse 3+ blank lines
    text = "\n".join(cleaned)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Повторная склейка: перенос через номер страницы или колонтитул становится
    # видимым только после удаления мусорных строк.
    text = _CAPS_HYPHEN_JOIN_RE.sub(r'\1\2', text)
    text = _TEXTBOOK_HYPHEN_JOIN_RE.sub(r'\1\2', text)
    text = _textbook_join_hyphen_across_headers(text)
    return text.strip()


# Колонтитул страницы учебника, украшенный номером страницы: «8 ГЛАВА I»,
# «ГЛАВА I 9», «7 § 1. Восстановление…», «§ 1. Восстановление… 7». Без номера
# это обычный заголовок главы/параграфа, который дедупится ниже.
_TEXTBOOK_RUNNING_HEADER_RES = (
    re.compile(r'^(?P<page>\d{1,3})\s+(?P<core>(?:Глава|ГЛАВА)\s+(?:[IVXLCDM]+|\d+)\.?)$'),
    re.compile(r'^(?P<core>(?:Глава|ГЛАВА)\s+(?:[IVXLCDM]+|\d+)\.?)\s+(?P<page>\d{1,3})$'),
    re.compile(r'^(?P<page>\d{1,3})\s+(?P<core>§\s*\d+(?:\s*[–—\-]\s*\d+)?\.\s+\S.{2,120}?)$'),
    re.compile(r'^(?P<core>§\s*\d+(?:\s*[–—\-]\s*\d+)?\.\s+\S.{2,120}?)\s+(?P<page>\d{1,3})$'),
)
# Повтор ключа на стольких страницах делает строку колонтитулом (а не, скажем,
# заголовком «Глава I. 1945», который встречается один раз).
_TEXTBOOK_RUNNING_HEADER_MIN_REPEATS = 2


def _textbook_running_header_core(line: str) -> Optional[str]:
    """«8 ГЛАВА I» → «ГЛАВА I»; не колонтитул → None."""
    s = line.strip()
    for rx in _TEXTBOOK_RUNNING_HEADER_RES:
        m = rx.match(s)
        if m:
            return m.group("core").strip()
    return None


def _textbook_running_header_keys(lines: List[str]) -> Dict[str, int]:
    """Сколько раз каждый колонтитул (без номера страницы) встречается в тексте."""
    counts: Dict[str, int] = {}
    for line in lines:
        core = _textbook_running_header_core(line)
        if core:
            key = _textbook_norm_heading_key(core)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _textbook_strip_running_header_page(line: str, header_keys: Dict[str, int]) -> str:
    """Убрать номер страницы из колонтитула, если тот повторяется по документу.

    Остаётся чистый «ГЛАВА I» / «§ 1. Название», который ниже превращается в
    заголовок и дедупится по номеру главы/параграфа. Иначе «8 ГЛАВА I» проходил
    как КАПС-подзаголовок, и каждая страница становилась узлом дерева.
    """
    core = _textbook_running_header_core(line)
    if not core:
        return line
    if header_keys.get(_textbook_norm_heading_key(core), 0) < _TEXTBOOK_RUNNING_HEADER_MIN_REPEATS:
        return line
    return core


def _textbook_rewrite_headings(md: str) -> str:
    """Inject ATX headings for chapters/§/all-caps subheads; drop running headers."""
    lines = md.splitlines()
    header_keys = _textbook_running_header_keys(lines)
    lines = [_textbook_strip_running_header_page(l, header_keys) for l in lines]
    out: List[str] = []
    i = 0
    n = len(lines)
    open_chapter_key = ""
    open_section_key = ""

    def _peek_nonempty(start: int) -> Tuple[int, str]:
        j = start
        while j < n and not lines[j].strip():
            j += 1
        if j >= n:
            return j, ""
        return j, lines[j].strip()

    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            out.append("")
            i += 1
            continue

        # Chapter: «Глава I. …» / «ГЛАВА I»
        ch = _TEXTBOOK_CHAPTER_RE.match(s)
        if ch:
            num, rest = ch.group(1), (ch.group(2) or "").strip()
            title = _textbook_heading_text(f"Глава {num}" + (f". {rest}" if rest else ""))
            # Absorb one wrapped ALL-CAPS continuation (e.g. «И ОКРУЖАЮЩИЙ МИР»)
            j, nxt = _peek_nonempty(i + 1)
            if (
                nxt
                and _textbook_is_allcaps_subhead(nxt)
                and nxt.upper().replace('Ё', 'Е') not in _TEXTBOOK_KNOWN_SUBHEADS
                and not _TEXTBOOK_CHAPTER_RE.match(nxt)
                and not _TEXTBOOK_SECTION_BARE_RE.match(nxt)
                and not _TEXTBOOK_SECTION_FULL_RE.match(nxt)
                and (not rest or len(rest) < 40)
            ):
                title = _textbook_heading_text(f"{title} {nxt}")
                i = j  # advanced past continuation; +1 after emit
            key = _textbook_norm_heading_key(title)
            open_ch_num = ""
            m_open_ch = re.match(r'глава\s+([ivxlcdm]+|\d+)', open_chapter_key, re.I)
            if m_open_ch:
                open_ch_num = m_open_ch.group(1).upper()
            this_num = num.upper() if not num.isdigit() else num
            if open_chapter_key and (key == open_chapter_key or open_ch_num == this_num):
                i += 1
                continue
            open_chapter_key = key
            open_section_key = ""
            out.append(f"# {title}")
            i += 1
            continue

        # Bare § opener: title on following line(s)
        bare = _TEXTBOOK_SECTION_BARE_RE.match(s)
        if bare:
            num = bare.group(1).replace('–', '-').replace('—', '-').replace(' ', '')
            title_parts: List[str] = []
            j = i + 1
            while j < n:
                nj, nxt = _peek_nonempty(j)
                if not nxt:
                    break
                if (
                    _TEXTBOOK_CHAPTER_RE.match(nxt)
                    or _TEXTBOOK_SECTION_BARE_RE.match(nxt)
                    or _TEXTBOOK_SECTION_FULL_RE.match(nxt)
                    or _textbook_is_allcaps_subhead(nxt)
                    or _textbook_is_junk_line(nxt)
                ):
                    break
                # Stop absorbing once we hit a long prose / epigraph
                if (
                    len(nxt) > 80
                    or (nxt.endswith('.') and len(nxt) > 40)
                    or nxt.startswith(('«', '"', '„', '('))
                    or '—' in nxt
                    or '–' in nxt
                ):
                    break
                title_parts.append(nxt)
                j = nj + 1
                if len(title_parts) >= 3:
                    break
            title = _textbook_heading_text(f"§ {num}" + (f". {' '.join(title_parts)}" if title_parts else ""))
            key = _textbook_norm_heading_key(title)
            open_num = ""
            m_open = re.match(r'§\s*(\d+(?:-\d+)?)', open_section_key)
            if m_open:
                open_num = m_open.group(1)
            if open_section_key and (key == open_section_key or open_num == num):
                # duplicate bare / same § number — skip
                i = j if title_parts else i + 1
                continue
            open_section_key = key
            out.append(f"## {title}")
            i = j if title_parts else i + 1
            continue

        # Full «§ N. Title» (often running header; keep first, drop repeats)
        full = _TEXTBOOK_SECTION_FULL_RE.match(s)
        if full:
            num = full.group(1).replace('–', '-').replace('—', '-').replace(' ', '')
            rest = _textbook_heading_text(full.group(2))
            title = f"§ {num}. {rest}"
            key = _textbook_norm_heading_key(title)
            # Match against open section (full or bare § N)
            open_num = ""
            m_open = re.match(r'§\s*(\d+(?:-\d+)?)', open_section_key)
            if m_open:
                open_num = m_open.group(1)
            if open_section_key and (
                key == open_section_key
                or (open_num and open_num == num)
            ):
                i += 1
                continue
            open_section_key = key
            out.append(f"## {title}")
            i += 1
            continue

        # All-caps subheads
        if _textbook_is_allcaps_subhead(s):
            out.append(f"### {_textbook_heading_text(s)}")
            i += 1
            continue

        out.append(_textbook_norm_ws(s) if s else "")
        i += 1

    text = "\n".join(out)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class TextbookProcessor(DocumentProcessor):
    """Обработчик школьных учебников (обществознание и т.п.).

    Selected when document_kind=textbook. Injects #/##/### headings for
    chapters, § sections and all-caps subheads; strips running headers and
    cover/copyright junk; skips legal article/numbered-item postprocessing.
    """

    def can_process(self, path: str) -> bool:
        # Only selected explicitly via DocumentProcessorManager(document_kind=textbook)
        return False

    def process(self, path: str) -> Dict[str, Any]:
        md = self.convert_to_markdown(path, apply_pdf_footnotes=False)
        md = _textbook_cleanup_text(md)
        md = _textbook_rewrite_headings(md)
        tree = parse_markdown_to_tree(md, legal_postprocess=False)
        return tree


# ---------- 8b) Обработчик энциклопедий (БРЭ и т.п.) ----------

def _pdf_to_text_pypdf(path: str) -> str:
    """Extract PDF text via pypdf (content-stream order).

    markitdown's table heuristic interleaves two-column encyclopedia pages
    and drops inter-word spaces; pypdf follows the content stream.
    """
    from pypdf import PdfReader

    return "\n\n".join((page.extract_text() or "") for page in PdfReader(path).pages)


# Glue a 1–2 letter ALL-CAPS token to the following word (ПУ СУНЛИН → ПУСУНЛИН).
_ENCYCLOPEDIA_LETTERSPACE_LEAD_RE = re.compile(
    r'(?:(?<=^)|(?<=\s))([А-ЯЁ]{1,2})\s+(?=[А-ЯЁ])',
)
# Glue a trailing 1–2 letter ALL-CAPS token to the previous word (РАСП ЕВ → РАСПЕВ).
_ENCYCLOPEDIA_LETTERSPACE_TAIL_RE = re.compile(
    r'(?<=[А-ЯЁ])\s+([А-ЯЁ]{1,2})(?=\s*$|[,(])',
)


def _encyclopedia_norm_headword(head: str) -> str:
    """Collapse pypdf letter-spacing in ALL-CAPS titles.

    ``ПУ СУНЛИН`` → ``ПУСУНЛИН``, ``ПУХОВЫЕ В ОЛОСЫ`` → ``ПУХОВЫЕ ВОЛОСЫ``.
    Multi-word titles with 3+ letter tokens (``ПУТОРАНСКИЙ ЗАПОВЕДНИК``) stay split.
    """
    s = _textbook_norm_ws(head)
    s = _ENCYCLOPEDIA_LETTERSPACE_LEAD_RE.sub(r'\1', s)
    s = _ENCYCLOPEDIA_LETTERSPACE_TAIL_RE.sub(r'\1', s)
    return s


# Entry headword: ALL-CAPS run followed by "," or "(" (ПУТОРАКИ (Diplomesodon), …)
_ENCYCLOPEDIA_HEADWORD_RE = re.compile(
    r'^([А-ЯЁ][А-ЯЁ0-9\s\-–—«»\.]{2,70}?)\s*([,(])(.*)$',
)
# Biographical entry: ALL-CAPS surname + given name(s) + dates in () or [].
# ПУТИН Владимир Владимирович (р. 7.10.1952, …)
# ПУШКИН Александр Сергеевич [26.5(6.6).1799, …]
_ENCYCLOPEDIA_BIO_HEADWORD_RE = re.compile(
    r'^([А-ЯЁ]{4,40}(?:\s+[А-ЯЁ]{3,40})*)\s+'
    r'([А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+){0,3}\s*[(\[].*)$',
)
# Editorial-board style initials: «А. А. АВДЕЕВ, …»
_ENCYCLOPEDIA_INITIALS_RE = re.compile(
    r'^(?:[А-ЯЁ]\.\s*){1,3}[А-ЯЁ]',
)
# Ambiguous short ALL-CAPS token (ИРМО, РКП) — only accept after sentence end.
_ENCYCLOPEDIA_PREV_TERM_RE = re.compile(r'[.;!?]\s*$')
# Trailing author signature like «Н. А. Щипанов.»
_ENCYCLOPEDIA_AUTHOR_SIG_RE = re.compile(
    r'^[А-ЯЁ]\.\s*(?:[А-ЯЁ]\.\s*)?[А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)?\.\s*$',
)


def _encyclopedia_is_headword_line(line: str) -> Optional[re.Match]:
    """Return match if *line* looks like an encyclopedia entry headword."""
    s = (line or "").strip()
    if not s:
        return None
    m = _ENCYCLOPEDIA_HEADWORD_RE.match(s)
    if m is None:
        m = _ENCYCLOPEDIA_BIO_HEADWORD_RE.match(s)
    if not m:
        return None
    head = m.group(1).strip()
    if _ENCYCLOPEDIA_INITIALS_RE.match(head):
        return None
    letters = [c for c in head if c.isalpha()]
    # Real БРЭ headwords are at least ~4 letters (ПУТЬ); reject ЦК/АН noise.
    if len(letters) < 4:
        return None
    if any(c.islower() for c in letters):
        return None
    # Reject headwords that are mostly digits/punctuation
    if len(letters) < len(head) * 0.5:
        return None
    return m


def _encyclopedia_headword_is_strong(match: re.Match) -> bool:
    """True for unambiguous entry titles (long enough to skip prev-terminator)."""
    head = match.group(1).strip()
    letters = [c for c in head if c.isalpha()]
    return len(letters) >= 5


def _encyclopedia_cleanup_text(md: str) -> str:
    """Reuse textbook cleanup, then drop author signature lines."""
    md = _textbook_cleanup_text(md)
    lines = md.splitlines()
    cleaned: List[str] = []
    for line in lines:
        s = line.strip()
        if s and _ENCYCLOPEDIA_AUTHOR_SIG_RE.match(s):
            continue
        cleaned.append(s if s else "")
    text = "\n".join(cleaned)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _encyclopedia_rewrite_headings(md: str) -> str:
    """Inject ``## HEADWORD`` for each alphabetical entry; drop front matter.

    Strong headwords (5+ letters) are accepted on shape alone — multi-column
    PDFs often put a non-terminator line before a real entry. Short 4-letter
    tokens still require a sentence terminator on the previous line to avoid
    promoting mid-text acronyms (ИРМО, РКП).
    """
    lines = md.splitlines()
    out: List[str] = []
    prev_nonempty = ""
    first_entry_seen = False

    for raw in lines:
        s = raw.strip()
        if not s:
            if first_entry_seen:
                out.append("")
            continue

        m = _encyclopedia_is_headword_line(s)
        accept = False
        if m is not None:
            strong = _encyclopedia_headword_is_strong(m)
            if not first_entry_seen:
                # Any valid headword (not initials) may start the body.
                accept = True
            elif strong:
                accept = True
            elif not prev_nonempty or _ENCYCLOPEDIA_PREV_TERM_RE.search(prev_nonempty):
                accept = True

        if accept and m is not None:
            head = _encyclopedia_norm_headword(m.group(1))
            if m.lastindex and m.lastindex >= 3:
                rest_prefix = m.group(2) + (m.group(3) or "")
            else:
                rest_prefix = m.group(2) or ""
            rest_prefix = rest_prefix.strip()
            out.append(f"## {head}")
            if rest_prefix:
                if rest_prefix.startswith('('):
                    content_start = rest_prefix
                elif rest_prefix.startswith(','):
                    content_start = rest_prefix.lstrip(', ').strip()
                else:
                    content_start = rest_prefix
                if content_start:
                    out.append(content_start)
            first_entry_seen = True
            prev_nonempty = s
            continue

        if not first_entry_seen:
            prev_nonempty = s
            continue

        out.append(_textbook_norm_ws(s))
        prev_nonempty = s

    text = "\n".join(out)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class EncyclopediaProcessor(DocumentProcessor):
    """Обработчик энциклопедий (БРЭ и т.п.).

    Selected when document_kind=encyclopedia. Splits on ALL-CAPS alphabetical
    entry headwords; strips stress-digit OCR artifacts and soft hyphens;
    skips legal article/numbered-item postprocessing.
    """

    def can_process(self, path: str) -> bool:
        return False

    def process(self, path: str) -> Dict[str, Any]:
        if path.lower().endswith('.pdf'):
            md = self._normalize_unicode_superscripts(_pdf_to_text_pypdf(path).strip())
        else:
            md = self.convert_to_markdown(path, apply_pdf_footnotes=False)
        md = _encyclopedia_cleanup_text(md)
        md = _encyclopedia_rewrite_headings(md)
        tree = parse_markdown_to_tree(md, legal_postprocess=False)
        return tree


# ---------- 9) Универсальный обработчик для прочих документов ----------

class UniversalProcessor(DocumentProcessor):
    """Универсальный обработчик для всех остальных документов"""
    
    def can_process(self, path: str) -> bool:
        """Всегда может обработать (fallback)"""
        return True
    
    def process(self, path: str) -> Dict[str,Any]:
        """Обрабатывает документ через markitdown"""
        md = self.convert_to_markdown(path)
        tree = parse_markdown_to_tree(md)
        return self.apply_cleaning_patterns(tree)

# ---------- 10) Менеджер обработчиков ----------

class DocumentProcessorManager:
    """Менеджер для выбора и использования обработчиков"""
    
    def __init__(self, document_kind: str = "normative"):
        kind = (document_kind or "normative").strip().lower()
        self.document_kind = (
            kind if kind in ("normative", "textbook", "encyclopedia") else "normative"
        )
        self.processors: List[DocumentProcessor] = [
            GarantProcessor(),
            ConsultantProcessor(),
            GOSTProcessor(),
            DocxWithHeadingStylesProcessor(),
            DocxWithCustomStylesProcessor(),
            UniversalProcessor()  # Всегда последний как fallback
        ]
        self._textbook = TextbookProcessor()
        self._encyclopedia = EncyclopediaProcessor()
    
    def process_document(self, path: str, document_kind: Optional[str] = None) -> Dict[str,Any]:
        """Обрабатывает документ используя подходящий обработчик"""
        kind = (document_kind or self.document_kind or "normative").strip().lower()
        if kind == "textbook":
            print(f"[INFO] Используется обработчик: TextbookProcessor")
            return self._textbook.process(path)
        if kind == "encyclopedia":
            print(f"[INFO] Используется обработчик: EncyclopediaProcessor")
            return self._encyclopedia.process(path)
        for processor in self.processors:
            if processor.can_process(path):
                print(f"[INFO] Используется обработчик: {processor.__class__.__name__}")
                return processor.process(path)
        # Fallback на универсальный
        return UniversalProcessor().process(path)

# ---------- 11) Утилиты сохранения/загрузки ----------

REQUIRED_KEYS = {"level","title","content","children"}

def _validate_tree(node: Dict[str,Any], path="root"):
    if not isinstance(node, dict): raise TypeError(f"{path}: node must be dict")
    missing = REQUIRED_KEYS - set(node.keys())
    if missing: raise ValueError(f"{path}: missing keys {missing}")
    if not isinstance(node["level"], int): raise TypeError(f"{path}.level must be int")
    if not isinstance(node["title"], str): raise TypeError(f"{path}.title must be str")
    if not isinstance(node["content"], str): raise TypeError(f"{path}.content must be str")
    if not isinstance(node["children"], list): raise TypeError(f"{path}.children must be list")
    for i,ch in enumerate(node["children"]): _validate_tree(ch, f"{path}.children[{i}]")

def save_tree_json(tree: Dict[str,Any], out_path: str, pretty=True):
    _validate_tree(tree)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        txt = json.dumps(tree, ensure_ascii=False, indent=2)
    else:
        txt = json.dumps(tree, ensure_ascii=False, separators=(",",":"))
    Path(out_path).write_text(txt, encoding="utf-8")

# ---------- 12) Публичный API ----------

__all__ = [
    "extract_outline_from_document",
    "save_tree_json",
    "DocumentProcessor",
    "DocumentProcessorManager",
    "GarantProcessor",
    "ConsultantProcessor",
    "GOSTProcessor",
    "DocxWithHeadingStylesProcessor",
    "DocxWithCustomStylesProcessor",
    "TextbookProcessor",
    "EncyclopediaProcessor",
    "UniversalProcessor",
    "parse_markdown_to_tree",
    "_textbook_cleanup_text",
    "_textbook_rewrite_headings",
    "_encyclopedia_cleanup_text",
    "_encyclopedia_rewrite_headings",
]

def extract_outline_from_document(path: str, document_kind: str = "normative") -> Dict[str,Any]:
    """Универсальная функция для извлечения структуры из документа"""
    manager = DocumentProcessorManager(document_kind=document_kind)
    return manager.process_document(path, document_kind=document_kind)

# ---------- 13) CLI-пример ----------

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Convert documents to JSON outline (sections hierarchy).")
    ap.add_argument("inputs", nargs="+", help="Paths to documents (DOCX, PDF, etc.)")
    ap.add_argument("-o","--out", default="out", help="Output folder (default: ./out)")
    ap.add_argument(
        "--document-kind",
        default="normative",
        choices=["normative", "textbook", "encyclopedia"],
        help="Pipeline flavour: normative (legal), textbook, or encyclopedia",
    )
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    manager = DocumentProcessorManager(document_kind=args.document_kind)
    
    for p in args.inputs:
        try:
            tree = manager.process_document(p, document_kind=args.document_kind)
            save_tree_json(tree, out_dir / (Path(p).stem + ".json"), pretty=True)
            print(f"[ok] {p} → {out_dir / (Path(p).stem + '.json')}")
        except Exception as e:
            print(f"[error] {p}: {e}")
