"""
Скрипт для тестирования чанкера через API.

Использование:
    # Сначала запустите сервис: python main.py
    # Затем запустите этот скрипт:
    python test_api.py path/to/document.docx
"""

import sys
import requests
import json
from pathlib import Path

def test_chunker_api(document_path: str, api_url: str = "http://localhost:8517/chunk/", min_size: int = 50):
    """
    Тестирует чанкер через API.
    
    Args:
        document_path: Путь к документу
        api_url: URL API endpoint
        min_size: Минимальный размер текста для создания чанка
    """
    print(f"\n{'='*80}")
    print(f"Тестирование чанкера через API")
    print(f"{'='*80}")
    print(f"Документ: {document_path}")
    print(f"API URL: {api_url}")
    print(f"Min size: {min_size}")
    print(f"{'='*80}\n")
    
    # Проверяем существование файла
    doc_path = Path(document_path)
    if not doc_path.exists():
        print(f"✗ Ошибка: Файл не найден: {doc_path}")
        return None
    
    try:
        # Отправляем файл на API
        print("Отправка файла на API...")
        with open(doc_path, 'rb') as f:
            files = {'file': (doc_path.name, f, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}
            params = {'min_size': min_size}
            response = requests.post(api_url, files=files, params=params)
        
        if response.status_code != 200:
            print(f"✗ Ошибка API: {response.status_code}")
            print(f"  Ответ: {response.text}")
            return None
        
        result = response.json()
        
        print(f"\n✓ Успешно обработано")
        print(f"  Файл: {result['filename']}")
        print(f"  Тип: {result['file_type']}")
        print(f"  Чанков: {result['num_chunks']}")
        print(f"  Метод: {result['chunking_method']}")
        
        # Показываем примеры
        if 'chunks_detailed' in result and result['chunks_detailed']:
            print(f"\n{'='*80}")
            print("Примеры чанков:")
            print(f"{'='*80}\n")
            
            for i, chunk in enumerate(result['chunks_detailed'][:5], 1):
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
        
        # Сохраняем результат
        output_path = doc_path.parent / f"chunks_api_{doc_path.stem}.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"✓ Результат сохранен в: {output_path}")
        
        return result
        
    except requests.exceptions.ConnectionError:
        print(f"✗ Ошибка: Не удалось подключиться к API по адресу {api_url}")
        print("  Убедитесь, что сервис запущен: python main.py")
        return None
    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Тестирование чанкера через API"
    )
    parser.add_argument(
        "document",
        help="Путь к документу (DOCX, PDF и т.д.)"
    )
    parser.add_argument(
        "-u", "--url",
        default="http://localhost:8517/chunk/",
        help="URL API endpoint (по умолчанию: http://localhost:8517/chunk/)"
    )
    parser.add_argument(
        "-m", "--min-size",
        type=int,
        default=50,
        help="Минимальный размер текста для создания чанка (по умолчанию: 50)"
    )
    
    args = parser.parse_args()
    
    test_chunker_api(args.document, args.url, args.min_size)

