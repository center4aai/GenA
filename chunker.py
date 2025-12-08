"""
Модуль для создания чанков из документов для передачи в LLM.

Процесс обработки:
1. Документ (DOCX, PDF и т.д.) → docx2json_outline.py → JSON дерево структуры
2. JSON дерево → chunker.py → чанки для LLM

Использование:
    # Полный цикл обработки документа
    chunks = process_document_to_chunks("document.docx", min_size=50)
    
    # Или работа с готовым JSON файлом
    chunks = create_llm_chunks("document_outline.json", min_size=50)
    
    # Или работа напрямую с данными дерева
    tree = extract_outline_from_document("document.docx")
    chunks = create_llm_chunks(tree, min_size=50, document_name="document")

CLI:
    # Обработка документа (автоматически через docx2json_outline)
    python chunker.py document.docx -o output_dir --save-intermediate
    
    # Обработка готового JSON файла
    python chunker.py document_outline.json --json-mode
"""

from typing import List, Dict, Any, Union
from pathlib import Path
import json
import os
import sys

# Убеждаемся, что можем импортировать docx2json_outline из той же директории
_current_dir = Path(__file__).parent.absolute()
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))

try:
    from docx2json_outline import extract_outline_from_document
except ImportError:
    # Альтернативный способ импорта, если обычный не работает
    import importlib.util
    docx2json_path = _current_dir / "docx2json_outline.py"
    if docx2json_path.exists():
        spec = importlib.util.spec_from_file_location(
            "docx2json_outline", 
            docx2json_path
        )
        if spec and spec.loader:
            docx2json_outline = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(docx2json_outline)
            extract_outline_from_document = docx2json_outline.extract_outline_from_document
        else:
            raise ImportError(
                f"Не удалось загрузить модуль docx2json_outline из {docx2json_path}. "
                "Убедитесь, что файл docx2json_outline.py находится в той же директории."
            )
    else:
        raise ImportError(
            f"Файл docx2json_outline.py не найден в {_current_dir}. "
            "Убедитесь, что оба файла находятся в одной директории."
        )


def create_llm_chunks(
    data_source: Union[str, Dict[str, Any]], 
    min_size: int = 50,
    document_name: str = None
) -> List[Dict[str, Any]]:
    """
    Создаёт чанки в формате, готовом для передачи в LLM.
    
    Args:
        data_source: Путь к JSON файлу (str) или словарь с данными дерева (Dict)
        min_size: Минимальный размер текста для создания чанка (по умолчанию 50)
        document_name: Имя документа для генерации fragment_id (если не указано, берется из filepath или "document")
    
    Returns:
        Список чанков в формате для LLM
    """
    # Загружаем данные: либо из файла, либо используем переданный словарь
    if isinstance(data_source, str):
        # Это путь к файлу - нормализуем путь
        file_path = Path(data_source).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Файл не найден: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if document_name is None:
            document_name = file_path.stem
    else:
        # Это уже словарь с данными
        data = data_source
        if document_name is None:
            document_name = "document"
    
    chunks = []
    chunk_counter = 0
    
    def process_node(node, breadcrumb_titles=[], breadcrumb_levels=[], depth=0):
        nonlocal chunk_counter
        
        title = node.get("title", "").strip()
        content = node.get("content", "").strip()
        level = node.get("level", 0)
        children = node.get("children", [])
        
        # Текст узла
        if title and content:
            node_text = f"{title}\n\n{content}"
        else:
            node_text = title or content
        
        text_length = len(node_text)
        is_leaf = len(children) == 0
        
        # Создаём чанк если:
        # 1. Это листовой узел (конец ветки)
        # 2. ИЛИ текст >= min_size (промежуточный, но большой)
        should_create_chunk = is_leaf or text_length >= min_size
        
        if should_create_chunk and node_text:
            chunk_counter += 1
            
            # Формируем breadcrumb path для LLM
            breadcrumb_path = []
            for i, (bc_title, bc_level) in enumerate(zip(breadcrumb_titles, breadcrumb_levels)):
                breadcrumb_path.append({
                    "level": bc_level,
                    "title": bc_title,
                    "position": i
                })
            
            chunk = {
                "fragment_id": f"{document_name}_chunk_{chunk_counter}",
                "hierarchy_context": {
                    "breadcrumb_path": breadcrumb_path,
                    "breadcrumb_text": " > ".join(breadcrumb_titles),
                    "current_level": level,
                    "depth": depth
                },
                "fragment_data": {
                    "title": title,
                    "content": content,
                    "combined_text": node_text,
                    "is_leaf_node": is_leaf,
                    "has_children": len(children) > 0,
                    "text_length": text_length
                }
            }
            
            chunks.append(chunk)
        
        # Рекурсия в детей
        new_breadcrumb_titles = breadcrumb_titles + [title] if title else breadcrumb_titles
        new_breadcrumb_levels = breadcrumb_levels + [level] if title else breadcrumb_levels
        
        for child in children:
            process_node(child, new_breadcrumb_titles, new_breadcrumb_levels, depth + 1)
    
    process_node(data)
    return chunks


def process_document_to_chunks(
    document_path: str,
    min_size: int = 50,
    save_intermediate_json: bool = False,
    intermediate_json_path: str = None
) -> List[Dict[str, Any]]:
    """
    Полный цикл обработки документа: документ → docx2json_outline → chunker → чанки для LLM.
    
    Args:
        document_path: Путь к исходному документу (DOCX, PDF и т.д.)
        min_size: Минимальный размер текста для создания чанка
        save_intermediate_json: Сохранять ли промежуточный JSON файл
        intermediate_json_path: Путь для сохранения промежуточного JSON (если не указан, генерируется автоматически)
    
    Returns:
        Список чанков в формате для LLM
    """
    # Нормализуем путь к документу
    doc_path = Path(document_path).resolve()
    if not doc_path.exists():
        raise FileNotFoundError(f"Документ не найден: {doc_path}")
    
    print(f"\n{'='*80}")
    print(f"Обработка документа: {doc_path}")
    print(f"{'='*80}")
    
    # Шаг 1: Обработка документа через docx2json_outline
    print("\n[Шаг 1] Извлечение структуры документа...")
    tree = extract_outline_from_document(str(doc_path))
    print(f"✓ Структура извлечена")
    
    # Сохраняем промежуточный JSON, если нужно
    if save_intermediate_json:
        if intermediate_json_path is None:
            intermediate_json_path = doc_path.parent / f"{doc_path.stem}_outline.json"
        else:
            intermediate_json_path = Path(intermediate_json_path).resolve()
        
        # Создаем директорию, если нужно
        intermediate_json_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(intermediate_json_path, 'w', encoding='utf-8') as f:
            json.dump(tree, f, ensure_ascii=False, indent=2)
        print(f"✓ Промежуточный JSON сохранен: {intermediate_json_path}")
    
    # Шаг 2: Создание чанков
    print("\n[Шаг 2] Создание чанков для LLM...")
    document_name = doc_path.stem
    chunks = create_llm_chunks(tree, min_size=min_size, document_name=document_name)
    print(f"✓ Создано {len(chunks)} чанков")
    
    return chunks


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Обработка документов: извлечение структуры и создание чанков для LLM"
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Пути к документам (DOCX, PDF и т.д.) или JSON файлам со структурой"
    )
    parser.add_argument(
        "-m", "--min-size",
        type=int,
        default=50,
        help="Минимальный размер текста для создания чанка (по умолчанию: 50)"
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=".",
        help="Директория для сохранения результатов (по умолчанию: текущая)"
    )
    parser.add_argument(
        "--save-intermediate",
        action="store_true",
        help="Сохранять промежуточные JSON файлы со структурой документа"
    )
    parser.add_argument(
        "--json-mode",
        action="store_true",
        help="Режим работы только с JSON файлами (без предварительной обработки через docx2json_outline)"
    )
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for input_path in args.inputs:
        try:
            # Нормализуем путь к входному файлу
            input_file = Path(input_path).resolve()
            
            if not input_file.exists():
                print(f"\n[ОШИБКА] Файл не найден: {input_file}")
                continue
            
            if args.json_mode or input_file.suffix.lower() == '.json':
                # Режим работы с готовыми JSON файлами
                print(f"\n{'='*80}")
                print(f"Обработка JSON файла: {input_file}")
                print(f"{'='*80}")
                
                chunks = create_llm_chunks(str(input_file), min_size=args.min_size)
                
                output_file = output_dir.resolve() / f"llm_chunks_{input_file.stem}.json"
                output_file.parent.mkdir(parents=True, exist_ok=True)
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(chunks, f, ensure_ascii=False, indent=2)
                
                print(f"✓ Создано {len(chunks)} чанков")
                print(f"✓ Сохранено в {output_file}")
                
            else:
                # Полный цикл: документ → docx2json_outline → chunker
                intermediate_json_path = None
                if args.save_intermediate:
                    intermediate_json_path = output_dir.resolve() / f"{input_file.stem}_outline.json"
                
                chunks = process_document_to_chunks(
                    str(input_file),
                    min_size=args.min_size,
                    save_intermediate_json=args.save_intermediate,
                    intermediate_json_path=str(intermediate_json_path) if intermediate_json_path else None
                )
                
                # Сохраняем чанки
                output_file = output_dir.resolve() / f"llm_chunks_{input_file.stem}.json"
                output_file.parent.mkdir(parents=True, exist_ok=True)
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(chunks, f, ensure_ascii=False, indent=2)
                
                print(f"✓ Сохранено в {output_file}")
            
            # Примеры
            if chunks:
                print(f"\nПример чанка #1:")
                example = chunks[0]
                print(f"  ID: {example['fragment_id']}")
                print(f"  Breadcrumb: {example['hierarchy_context']['breadcrumb_text']}")
                print(f"  Текст: {example['fragment_data']['combined_text'][:150]}...")
            
        except Exception as e:
            print(f"\n[ОШИБКА] Обработка {input_path}: {e}")
            import traceback
            traceback.print_exc()
