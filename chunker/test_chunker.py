"""
Простой скрипт для тестирования чанкера на файле.

Использование:
    python test_chunker.py files_test/document.docx
    python test_chunker.py files_test/document.docx --min-size 50
    python test_chunker.py files_test/document.docx --output output.json
"""

import sys
import json
from pathlib import Path
from main import process_document_to_chunks, create_llm_chunks

def test_chunker(document_path: str, min_size: int = 50, output_file: str = None):
    """
    Тестирует чанкер на указанном документе.
    
    Args:
        document_path: Путь к документу
        min_size: Минимальный размер текста для создания чанка
        output_file: Путь для сохранения результата (если не указан, выводит в консоль)
    """
    print(f"\n{'='*80}")
    print(f"Тестирование чанкера")
    print(f"{'='*80}")
    print(f"Документ: {document_path}")
    print(f"Min size: {min_size}")
    print(f"{'='*80}\n")
    
    try:
        # Обрабатываем документ
        chunks, document_type_info = process_document_to_chunks(document_path, min_size=min_size)
        
        print(f"\n✓ Успешно создано {len(chunks)} чанков\n")
        
        # Показываем информацию о типе документа
        if document_type_info:
            print("Тип документа:")
            print(f"  Тип: {document_type_info.get('document_type', 'unknown')}")
            print(f"  Название: {document_type_info.get('document_name', 'Название не определено')}")
            print(f"  Уверенность: {document_type_info.get('confidence', 0.0):.2f}")
            print(f"  Описание: {document_type_info.get('description', 'Нет описания')}")
            if 'key_indicators' in document_type_info:
                print(f"  Ключевые признаки: {', '.join(document_type_info['key_indicators'])}")
            print()
        
        # Показываем статистику
        print("Статистика:")
        print(f"  Всего чанков: {len(chunks)}")
        if chunks:
            avg_length = sum(c["fragment_data"]["text_length"] for c in chunks) / len(chunks)
            min_length = min(c["fragment_data"]["text_length"] for c in chunks)
            max_length = max(c["fragment_data"]["text_length"] for c in chunks)
            print(f"  Средняя длина: {avg_length:.0f} символов")
            print(f"  Минимальная длина: {min_length} символов")
            print(f"  Максимальная длина: {max_length} символов")
        
        # Показываем примеры чанков
        print(f"\n{'='*80}")
        print("Примеры чанков:")
        print(f"{'='*80}\n")
        
        for i, chunk in enumerate(chunks[:5], 1):  # Показываем первые 5
            print(f"Чанк #{i}:")
            print(f"  ID: {chunk['fragment_id']}")
            print(f"  Breadcrumb: {chunk['hierarchy_context']['breadcrumb_text']}")
            print(f"  Title: {chunk['fragment_data']['title'][:100]}..." if len(chunk['fragment_data']['title']) > 100 else f"  Title: {chunk['fragment_data']['title']}")
            print(f"  Level: {chunk['hierarchy_context']['current_level']}")
            print(f"  Длина: {chunk['fragment_data']['text_length']} символов")
            print(f"  Текст (первые 200 символов):")
            text_preview = chunk['fragment_data']['combined_text'][:200]
            print(f"    {text_preview}...")
            print()
        
        if len(chunks) > 5:
            print(f"... и еще {len(chunks) - 5} чанков\n")
        
        # Сохраняем результат
        result = {
            "document_type": document_type_info,
            "chunks": chunks,
            "statistics": {
                "total_chunks": len(chunks),
                "avg_length": sum(c["fragment_data"]["text_length"] for c in chunks) / len(chunks) if chunks else 0,
                "min_length": min(c["fragment_data"]["text_length"] for c in chunks) if chunks else 0,
                "max_length": max(c["fragment_data"]["text_length"] for c in chunks) if chunks else 0
            }
        }
        
        if output_file:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"✓ Результат сохранен в: {output_path}")
        else:
            # Сохраняем в файл рядом с документом
            doc_path = Path(document_path)
            output_path = doc_path.parent / f"chunks_{doc_path.stem}.json"
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"✓ Результат сохранен в: {output_path}")
        
        return chunks
        
    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Тестирование чанкера на документе"
    )
    parser.add_argument(
        "document",
        help="Путь к документу (DOCX, PDF и т.д.)"
    )
    parser.add_argument(
        "-m", "--min-size",
        type=int,
        default=50,
        help="Минимальный размер текста для создания чанка (по умолчанию: 50)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Путь для сохранения результата (JSON файл)"
    )
    
    args = parser.parse_args()
    
    # Проверяем существование файла
    doc_path = Path(args.document)
    if not doc_path.exists():
        print(f"✗ Ошибка: Файл не найден: {doc_path}")
        sys.exit(1)
    
    test_chunker(str(doc_path), args.min_size, args.output)

