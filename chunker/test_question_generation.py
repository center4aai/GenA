"""
Скрипт для тестирования генерации вопросов из чанков.

Этот скрипт проверяет полный цикл: чанки → генерация вопросов.

Использование:
    # Тест через прямой вызов (без API):
    python test_question_generation.py chunks_Family_code_Russian_Federation_1-4.json
    
    # Тест через API:
    python test_question_generation.py chunks_Family_code_Russian_Federation_1-4.json --api http://localhost:8518/process_prompt/
    
    # Тест только для одного типа вопросов:
    python test_question_generation.py chunks_Family_code_Russian_Federation_1-4.json --question-type one
    
    # Тест только первых N чанков:
    python test_question_generation.py chunks_Family_code_Russian_Federation_1-4.json --limit 3
"""

import sys
import json
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional
import argparse
from datetime import datetime

# Для прямого вызова (без API) - добавляем путь к agent_api
# Это нужно для правильных импортов, аналогично agent_api.py
_project_root = Path(__file__).parent.parent
_agent_api_path = _project_root / "agent_api"
sys.path.insert(0, str(_agent_api_path))

# Загружаем переменные окружения из .env файла (если есть)
# Пробуем несколько возможных путей
try:
    from dotenv import load_dotenv
    # Пробуем загрузить .env из корня проекта
    env_file_root = _project_root / ".env"
    env_file_agent = _agent_api_path / ".env"
    if env_file_root.exists():
        load_dotenv(env_file_root)
    elif env_file_agent.exists():
        load_dotenv(env_file_agent)
    else:
        # Пробуем автоматический поиск
        load_dotenv(_project_root)
except ImportError:
    pass  # dotenv не установлен, используем переменные окружения системы

def test_question_generation_direct(
    chunk: Dict[str, Any],
    question_type: str,
    verbose: bool = True,
    source: str = "test"
) -> Optional[Dict[str, Any]]:
    """
    Тестирует генерацию вопроса напрямую (без API).
    
    Args:
        chunk: Чанк в формате для LLM
        question_type: Тип вопроса ("one", "multi", "open")
        verbose: Выводить ли подробную информацию
    
    Returns:
        Результат генерации вопроса или None при ошибке
    """
    try:
        # Импортируем так же, как в agent_api/agent_api.py
        # Путь agent_api уже добавлен в sys.path выше
        from agent.handler import GENAHandler, GENAOptions
        from config import MONGO_DB_PATH
        from agent.assistant_graph import GENAAssistant
        from agent.runnables import create_GENA_runnables_ollama
        from langgraph.checkpoint.memory import MemorySaver
        
        # Получаем текст чанка
        chunk_text = chunk["fragment_data"]["combined_text"]
        chunk_id = chunk["fragment_id"]
        
        if verbose:
            print(f"  Обработка чанка: {chunk_id}")
            print(f"  Длина текста: {len(chunk_text)} символов")
        
        # Проверяем, настроен ли MongoDB, если нет - используем MemorySaver для тестирования
        use_mongodb = (MONGO_DB_PATH and 
                      MONGO_DB_PATH != "mongodb://:/" and 
                      not MONGO_DB_PATH.startswith("mongodb://:") and
                      "://:" not in MONGO_DB_PATH)
        
        if use_mongodb:
            # Используем обычный handler с MongoDB
            handler_options = GENAOptions(mongodb_uri=MONGO_DB_PATH)
            handler = GENAHandler(options=handler_options)
        else:
            # Используем MemorySaver вместо MongoDB для тестирования
            if verbose:
                print(f"  [INFO] MongoDB не настроен, используем MemorySaver для тестирования")
            
            # Создаем assistant напрямую с MemorySaver
            runnables = create_GENA_runnables_ollama()
            memory_checkpointer = MemorySaver()
            assistant = GENAAssistant(
                generate_question_chain=runnables.generate_question_chain,
                provocativeness_chain=runnables.provocativeness_chain,
                validation_chain=runnables.validation_chain,
                difficulty_chain=runnables.difficulty_chain,
                checkpointer=memory_checkpointer,
            )
            
            # Подготовка входных данных
            input_data = {
                "chunk": chunk_text,
                "question_type": question_type,
                "source": source,
                "source_text": chunk_text
            }
            config = {"configurable": {"thread_id": f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{chunk_id}"}}
            
            # Запускаем граф напрямую
            output = assistant.graph.invoke(input_data, config=config)
            # Возвращаем состояние напрямую, так как оно уже содержит все нужные данные
            return output
        
        # Генерируем вопрос через handler (с MongoDB)
        result = handler.ahandle_prompt(
            prompt=chunk_text,
            question_type=question_type,
            source=source,
            chat_id=f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{chunk_id}",
            source_text=chunk_text
        )
        
        return result.get("output", {})
        
    except ImportError as e:
        print(f"  [ERROR] Ошибка импорта: {e}")
        print(f"     Совет: используйте режим API (--api URL) вместо прямого вызова")
        print(f"     Или убедитесь, что:")
        print(f"      1. Все зависимости установлены: pip install -r agent_api/requirements.txt")
        print(f"      2. Переменные окружения настроены в .env файле")
        print(f"      3. Вы запускаете скрипт из правильной директории")
        return None
    except ValueError as e:
        # Специальная обработка для ошибок конфигурации
        print(f"  [ERROR] Ошибка конфигурации: {e}")
        return None
    except Exception as e:
        error_msg = str(e)
        # Проверяем, не связана ли ошибка с MongoDB
        if "username" in error_msg.lower() or "mongodb" in error_msg.lower() or "uri" in error_msg.lower():
            print(f"  [ERROR] Ошибка подключения к MongoDB: {error_msg}")
            print(f"     Возможные причины:")
            print(f"      1. Переменные окружения не настроены (MONGO_USERNAME, MONGO_PASSWORD, MONGO_HOST, MONGO_PORT)")
            print(f"      2. Файл .env отсутствует или находится не в правильной директории")
            print(f"     Решение: используйте режим API (--api URL) - он не требует настройки MongoDB")
        else:
            print(f"  [ERROR] Ошибка генерации: {error_msg}")
        
        if verbose:
            import traceback
            traceback.print_exc()
        return None


def test_question_generation_api(
    chunk: Dict[str, Any],
    question_type: str,
    api_url: str,
    verbose: bool = True,
    source: str = "test"
) -> Optional[Dict[str, Any]]:
    """
    Тестирует генерацию вопроса через API.
    
    Args:
        chunk: Чанк в формате для LLM
        question_type: Тип вопроса ("one", "multi", "open")
        api_url: URL API endpoint
        verbose: Выводить ли подробную информацию
    
    Returns:
        Результат генерации вопроса или None при ошибке
    """
    try:
        # Получаем текст чанка
        chunk_text = chunk["fragment_data"]["combined_text"]
        chunk_id = chunk["fragment_id"]
        
        if verbose:
            print(f"  Обработка чанка: {chunk_id}")
            print(f"  Длина текста: {len(chunk_text)} символов")
        
        # Подготовка запроса
        payload = {
            "prompt": chunk_text,
            "question_type": question_type,
            "source": source,
            "chat_id": f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{chunk_id}",
            "source_text": chunk_text
        }
        
        # Отправка запроса
        response = requests.post(api_url, json=payload, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            return result.get("result", {}).get("output", {})
        else:
            print(f"  [ERROR] Ошибка API: {response.status_code}")
            print(f"  Ответ: {response.text}")
            return None
            
    except requests.exceptions.ConnectionError:
        print(f"  [ERROR] Ошибка: Не удалось подключиться к API по адресу {api_url}")
        return None
    except Exception as e:
        print(f"  [ERROR] Ошибка: {e}")
        import traceback
        if verbose:
            traceback.print_exc()
        return None


def print_question_result(
    result: Dict[str, Any],
    chunk_id: str,
    question_type: str,
    verbose: bool = True,
    show_full_json: bool = True
):
    """
    Выводит результат генерации вопроса в читаемом формате.
    
    Args:
        result: Результат генерации вопроса
        chunk_id: ID чанка
        question_type: Тип вопроса
        verbose: Выводить ли полную информацию
        show_full_json: Показывать ли полный JSON результат
    """
    print(f"\n{'='*80}")
    print(f"Результат генерации вопроса")
    print(f"{'='*80}")
    print(f"Чанк: {chunk_id}")
    print(f"Тип вопроса: {question_type}")
    print(f"{'='*80}\n")
    
    # Выводим полный JSON результат в первую очередь
    if show_full_json:
        print(f"{'='*80}")
        print(f"ПОЛНЫЙ JSON РЕЗУЛЬТАТ ОТ LLM:")
        print(f"{'='*80}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"{'='*80}\n")
    
    # Извлекаем данные
    # Результат может быть прямо состоянием или словарем с ключами
    gq = result.get("generated_question", {})
    sensitivity = result.get("sensitivity_score", {})
    validation = result.get("validation_result", {})
    difficulty = result.get("difficulty_score", {})
    
    # Если generated_question является словарем, извлекаем данные
    if not isinstance(gq, dict):
        gq = {}
    
    # Вопрос
    task = gq.get("task", "") if gq else ""
    if not task:
        # Пробуем найти вопрос в других полях
        task = result.get("task", "Не сгенерирован")
    
    text = gq.get("text", "") if gq else ""
    print(f"ВОПРОС:")
    print(f"  {task if task else 'Не сгенерирован'}")
    if text:
        print(f"\nДополнительный контекст:")
        print(f"  {text}")
    
    # Варианты ответов
    options = []
    if gq:
        for k, v in gq.items():
            if k.startswith("option_") and v not in [None, "None", ""]:
                try:
                    option_num = int(k.replace("option_", ""))
                    options.append((option_num, v))
                except ValueError:
                    pass
    
    if options:
        options.sort()
        print(f"\nВАРИАНТЫ ОТВЕТОВ:")
        for num, opt_text in options:
            print(f"  {num}. {opt_text}")
    
    # Правильный ответ
    outputs = gq.get("outputs", "") if gq else ""
    if not outputs:
        outputs = "Не указан"
    print(f"\nПРАВИЛЬНЫЙ ОТВЕТ: {outputs}")
    
    # Метаданные
    if verbose:
        print(f"\nМЕТАДАННЫЕ:")
        if sensitivity:
            print(f"  Чувствительность: {sensitivity}")
        if validation:
            print(f"  Валидация: {validation}")
        if difficulty:
            print(f"  Сложность: {difficulty}")
        
        source_text = gq.get("source_text", "") if gq else ""
        if not source_text:
            source_text = result.get("source_text", "")
        if source_text:
            print(f"\nИСХОДНЫЙ ТЕКСТ (первые 200 символов):")
            print(f"  {source_text[:200]}...")
    
    print(f"\n{'='*80}\n")


def test_questions_from_chunks(
    chunks_file: str,
    question_types: List[str] = ["one", "multi", "open"],
    limit: Optional[int] = None,
    api_url: Optional[str] = None,
    verbose: bool = True,
    save_results: bool = False,
    show_json: bool = True
):
    """
    Тестирует генерацию вопросов из чанков.
    
    Args:
        chunks_file: Путь к JSON файлу с чанками
        question_types: Список типов вопросов для тестирования
        limit: Ограничение на количество чанков (None = все)
        api_url: URL API endpoint (None = прямой вызов)
        verbose: Выводить ли подробную информацию
        save_results: Сохранять ли результаты в файл
    """
    print(f"\n{'='*80}")
    print(f"ТЕСТИРОВАНИЕ ГЕНЕРАЦИИ ВОПРОСОВ ИЗ ЧАНКОВ")
    print(f"{'='*80}")
    print(f"Файл с чанками: {chunks_file}")
    print(f"Типы вопросов: {', '.join(question_types)}")
    if limit:
        print(f"Ограничение: первые {limit} чанков")
    print(f"Режим: {'API' if api_url else 'Прямой вызов'}")
    print(f"{'='*80}\n")
    
    # Загружаем чанки
    chunks_path = Path(chunks_file)
    if not chunks_path.exists():
        print(f"[ERROR] Ошибка: Файл не найден: {chunks_path}")
        return
    
    try:
        with open(chunks_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Извлекаем чанки (могут быть напрямую в списке или в ключе "chunks")
        document_name = None
        if isinstance(data, list):
            chunks = data
        elif isinstance(data, dict) and "chunks" in data:
            chunks = data["chunks"]
            # Извлекаем document_name из document_type, если есть
            if "document_type" in data and isinstance(data["document_type"], dict):
                document_name = data["document_type"].get("document_name")
        else:
            print(f"[ERROR] Ошибка: Неверный формат файла. Ожидается список чанков или словарь с ключом 'chunks'")
            return
        
        # Если document_name не найден в корне, пытаемся извлечь из первого чанка
        if not document_name and chunks:
            first_chunk = chunks[0]
            if isinstance(first_chunk, dict):
                fragment_data = first_chunk.get("fragment_data", {})
                source = fragment_data.get("source", "")
                if source:
                    # source может содержать полное описание, извлекаем только название
                    document_name = source.split("—")[0].strip() if "—" in source else source
                    # Ограничиваем длину, если слишком длинное описание
                    if len(document_name) > 100:
                        document_name = document_name[:100]
        
        total_chunks = len(chunks)
        if limit:
            chunks = chunks[:limit]
        
        print(f"[OK] Загружено {total_chunks} чанков")
        if limit:
            print(f"[OK] Будут протестированы первые {len(chunks)} чанков")
        print()
        
        # Результаты
        results = []
        success_count = 0
        error_count = 0
        
        # Обрабатываем каждый чанк
        for chunk_idx, chunk in enumerate(chunks, 1):
            chunk_id = chunk.get("fragment_id", f"chunk_{chunk_idx}")
            
            print(f"[{chunk_idx}/{len(chunks)}] Чанк: {chunk_id}")
            
            # Обрабатываем каждый тип вопроса
            for question_type in question_types:
                print(f"\n  Тип вопроса: {question_type}")
                
                # Генерируем вопрос
                # Используем document_name в качестве source, если он определен
                source = document_name if document_name else "test"
                if api_url:
                    result = test_question_generation_api(chunk, question_type, api_url, verbose, source=source)
                else:
                    result = test_question_generation_direct(chunk, question_type, verbose, source=source)
                
                if result:
                    success_count += 1
                    
                    # Нормализуем результат - проверяем, обернут ли он в 'output'
                    # Если используется прямой вызов графа, результат уже является состоянием
                    # Если используется handler, результат обернут в {'output': state}
                    if isinstance(result, dict) and "output" in result and len(result) == 1:
                        result = result["output"]
                    
                    # Добавляем информацию о чанке в результат
                    result["chunk_id"] = chunk_id
                    result["chunk_index"] = chunk_idx
                    result["question_type"] = question_type
                    results.append(result)
                    
                    # Выводим результат
                    if verbose:
                        print_question_result(result, chunk_id, question_type, verbose, show_full_json=True)
                    else:
                        # В невербальном режиме тоже показываем полный JSON
                        print(f"  [OK] Вопрос создан успешно")
                        print(f"\n{'='*80}")
                        print(f"ПОЛНЫЙ JSON РЕЗУЛЬТАТ:")
                        print(f"{'='*80}")
                        print(json.dumps(result, ensure_ascii=False, indent=2))
                        print(f"{'='*80}\n")
                else:
                    error_count += 1
                    print(f"  [ERROR] Ошибка генерации")
            
            print()
        
        # Итоговая статистика
        print(f"\n{'='*80}")
        print(f"ИТОГОВАЯ СТАТИСТИКА")
        print(f"{'='*80}")
        print(f"Всего чанков обработано: {len(chunks)}")
        print(f"Всего вопросов создано: {success_count}")
        print(f"Ошибок: {error_count}")
        print(f"Успешность: {success_count / (success_count + error_count) * 100:.1f}%" if (success_count + error_count) > 0 else "N/A")
        print(f"{'='*80}\n")
        
        # Сохраняем результаты
        if save_results and results:
            output_file = chunks_path.parent / f"questions_{chunks_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "chunks_file": str(chunks_file),
                    "timestamp": datetime.now().isoformat(),
                    "statistics": {
                        "total_chunks": len(chunks),
                        "total_questions": success_count,
                        "errors": error_count
                    },
                    "results": results
                }, f, ensure_ascii=False, indent=2)
            print(f"[OK] Результаты сохранены в: {output_file}\n")
        
        return results
        
    except json.JSONDecodeError as e:
        print(f"[ERROR] Ошибка парсинга JSON: {e}")
        return None
    except Exception as e:
        print(f"[ERROR] Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Тестирование генерации вопросов из чанков",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  # Прямой вызов (нужна настройка окружения):
  python test_question_generation.py files_test/chunks_Family_code_Russian_Federation_1-4.json
  
  # Через API:
  python test_question_generation.py files_test/chunks_Family_code_Russian_Federation_1-4.json --api http://localhost:8518/process_prompt/
  
  # Только один тип вопросов, первые 2 чанка:
  python test_question_generation.py files_test/chunks_Family_code_Russian_Federation_1-4.json --question-type one --limit 2
  
  # Сохранить результаты:
  python test_question_generation.py files_test/chunks_Family_code_Russian_Federation_1-4.json --save
        """
    )
    
    parser.add_argument(
        "chunks_file",
        help="Путь к JSON файлу с чанками"
    )
    parser.add_argument(
        "--api",
        help="URL API endpoint (если не указан, используется прямой вызов)"
    )
    parser.add_argument(
        "--question-type",
        choices=["one", "multi", "open"],
        action="append",
        dest="question_types",
        help="Тип вопроса для тестирования (можно указать несколько раз)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Ограничение на количество чанков для тестирования"
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Сохранить результаты в JSON файл"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Минимальный вывод (без подробностей)"
    )
    
    args = parser.parse_args()
    
    # Определяем типы вопросов
    if args.question_types:
        question_types = args.question_types
    else:
        question_types = ["one", "multi", "open"]
    
    # Запускаем тестирование
    test_questions_from_chunks(
        chunks_file=args.chunks_file,
        question_types=question_types,
        limit=args.limit,
        api_url=args.api,
        verbose=not args.quiet,
        save_results=args.save
    )

