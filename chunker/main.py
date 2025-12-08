"""
FastAPI сервис для создания чанков из документов для передачи в LLM.

Процесс обработки:
1. Документ (DOCX, PDF и т.д.) → docx2json_outline.py → JSON дерево структуры
2. JSON дерево → chunker → чанки для LLM

Endpoints:
- POST /chunk/ - обработка загруженного документа
- POST /chunk-docx/ - legacy endpoint для DOCX файлов
- GET /health - проверка состояния сервиса
- GET / - информация о сервисе
"""

import os
import sys
import json
import logging
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Union, Tuple
from fastapi import FastAPI, File, UploadFile, Query
from fastapi.responses import JSONResponse

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройка путей для импорта docx2json_outline
# Файл теперь находится в той же директории
_current_dir = Path(__file__).parent.absolute()

# Добавляем текущую директорию в путь для импорта
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
                f"Не удалось загрузить модуль docx2json_outline из {docx2json_path}"
            )
    else:
        raise ImportError(
            f"Файл docx2json_outline.py не найден в {_current_dir}. "
            "Убедитесь, что файл находится в той же директории, что и main.py"
        )

app = FastAPI(title="GenA Chunker Service", version="2.0.0")


def extract_all_titles(node: Dict[str, Any], titles: List[str] = None) -> List[str]:
    """
    Извлекает все заголовки (title) из дерева документа.
    
    Args:
        node: Узел дерева документа
        titles: Список для накопления заголовков (используется рекурсивно)
    
    Returns:
        Список всех заголовков документа
    """
    if titles is None:
        titles = []
    
    title = node.get("title", "").strip()
    if title:
        titles.append(title)
    
    # Рекурсивно обрабатываем детей
    for child in node.get("children", []):
        extract_all_titles(child, titles)
    
    return titles


def identify_document_type(titles: List[str]) -> Dict[str, Any]:
    """
    Определяет тип документа на основе заголовков через LLM.
    
    Args:
        titles: Список заголовков документа
    
    Returns:
        Словарь с информацией о типе документа
    """
    try:
        from dotenv import load_dotenv
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # Загружаем переменные окружения
        load_dotenv()
        
        # Получаем настройки LLM из переменных окружения
        llm_model_name = os.getenv("LLM_MODEL_NAME", "")
        llm_url = os.getenv("LLM_URL_MODEL", "")
        llm_api_key = os.getenv("LLM_API_KEY", "")
        
        # Если LLM не настроен, возвращаем базовую информацию
        if not llm_model_name or not llm_url:
            logger.warning("LLM не настроен, пропускаем определение типа документа")
            return {
                "document_type": "unknown",
                "document_name": "Название не определено",
                "confidence": 0.0,
                "description": "Тип документа не определен (LLM не настроен)",
                "titles_count": len(titles),
                "key_indicators": []
            }
        
        # Формируем промпт
        titles_text = "\n".join([f"- {title}" for title in titles[:50]])  # Берем первые 50 заголовков
        if len(titles) > 50:
            titles_text += f"\n... и еще {len(titles) - 50} заголовков"
        
        from langchain_core.output_parsers import JsonOutputParser
        from pydantic import BaseModel, Field
        
        class DocumentTypeInfo(BaseModel):
            document_type: str = Field(description="Тип документа (например: Конституция, Кодекс, Федеральный закон, ГОСТ, Постановление и т.д.)")
            document_name: str = Field(description="Полное название документа (например: 'Семейный кодекс Российской Федерации', 'Конституция Российской Федерации')")
            confidence: float = Field(description="Уверенность в определении от 0.0 до 1.0", ge=0.0, le=1.0)
            description: str = Field(description="Краткое описание документа (1-2 предложения)")
            key_indicators: list[str] = Field(description="Ключевые признаки, по которым определен тип", default_factory=list)
        
        parser = JsonOutputParser(pydantic_object=DocumentTypeInfo)
        
        system_prompt = """Ты эксперт по анализу юридических и нормативных документов. 
Проанализируй заголовки документа и определи его тип и полное название.

Верни ответ ТОЛЬКО в формате валидного JSON без дополнительного текста:
{
    "document_type": "тип документа (например: Кодекс, Конституция, Федеральный закон)",
    "document_name": "полное название документа (например: 'Семейный кодекс Российской Федерации')",
    "confidence": 0.95,
    "description": "краткое описание документа (1-2 предложения)",
    "key_indicators": ["признак1", "признак2"]
}

ВАЖНО: 
- document_name должно быть полным официальным названием документа
- Если не можешь определить тип или название, укажи "unknown" с confidence 0.0
- Ответ должен быть валидным JSON без markdown разметки и дополнительного текста"""

        human_prompt = f"""Заголовки документа:

{titles_text}

Определи тип и полное официальное название этого документа."""

        # Создаем LLM клиент
        llm = ChatOpenAI(
            model=llm_model_name,
            temperature=0.0,
            openai_api_base=llm_url,
            openai_api_key=llm_api_key,
            max_retries=2,
            timeout=15,
            max_tokens=256
        )
        
        # Отправляем запрос с использованием парсера
        try:
            # Используем JsonOutputParser для более надежного парсинга
            from langchain_core.prompts import ChatPromptTemplate
            
            # Экранируем фигурные скобки в инструкциях парсера, чтобы LangChain не интерпретировал их как переменные
            format_instructions = parser.get_format_instructions().replace("{", "{{").replace("}", "}}")
            
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt + "\n\n" + format_instructions),
                ("human", human_prompt)
            ])
            
            chain = prompt | llm | parser
            result = chain.invoke({})
            
            # Преобразуем Pydantic модель в словарь
            if hasattr(result, 'model_dump'):
                result = result.model_dump()
            elif hasattr(result, 'dict'):
                result = result.dict()
            
            result["titles_count"] = len(titles)
            
            logger.info(f"Определен тип документа: {result.get('document_type', 'unknown')}")
            logger.info(f"Название документа: {result.get('document_name', 'Название не определено')}")
            logger.info(f"Уверенность: {result.get('confidence', 0.0):.2f}")
            return result
            
        except Exception as parse_error:
            # Если парсер не сработал, пытаемся распарсить вручную
            logger.warning(f"JsonOutputParser не сработал, пытаемся парсить вручную: {parse_error}")
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt)
            ]
            
            response = llm.invoke(messages)
            response_text = response.content.strip()
            
            # Парсим JSON ответ вручную
            import re
            
            # Логируем исходный ответ для отладки
            logger.debug(f"Ответ LLM (первые 500 символов): {response_text[:500]}")
            
            # Улучшенная очистка ответа
            # Убираем markdown код блоки
            if "```" in response_text:
                # Ищем JSON между ``` или ```json
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group(1).strip()
                else:
                    # Если не нашли между ```, убираем все до первого {
                    first_brace = response_text.find('{')
                    if first_brace != -1:
                        response_text = response_text[first_brace:]
                    # Убираем все после последнего }
                    last_brace = response_text.rfind('}')
                    if last_brace != -1:
                        response_text = response_text[:last_brace + 1]
            else:
                # Если нет markdown блоков, ищем JSON объект
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group(0).strip()
            
            # Очищаем от возможных лишних символов в начале/конце
            response_text = response_text.strip()
            if not response_text.startswith('{'):
                # Ищем первую открывающую скобку
                first_brace = response_text.find('{')
                if first_brace != -1:
                    response_text = response_text[first_brace:]
            if not response_text.endswith('}'):
                # Ищем последнюю закрывающую скобку
                last_brace = response_text.rfind('}')
                if last_brace != -1:
                    response_text = response_text[:last_brace + 1]
            
            # Пытаемся исправить распространенные ошибки JSON
            # Убираем trailing commas перед закрывающими скобками
            response_text = re.sub(r',\s*}', '}', response_text)
            response_text = re.sub(r',\s*]', ']', response_text)
            
            # Парсим JSON
            try:
                result = json.loads(response_text)
            except json.JSONDecodeError as json_error:
                # Если не удалось распарсить, логируем детали
                logger.error(f"Ошибка парсинга JSON: {json_error}")
                logger.error(f"Проблемный текст (первые 1000 символов): {response_text[:1000]}")
                logger.error(f"Позиция ошибки: {json_error.pos if hasattr(json_error, 'pos') else 'unknown'}")
                # Пытаемся извлечь хотя бы частичную информацию
                result = {
                    "document_type": "unknown",
                    "document_name": "Название не определено",
                    "confidence": 0.0,
                    "description": "Не удалось распарсить ответ LLM",
                    "key_indicators": []
                }
            
            # Проверяем наличие обязательных полей
            if "document_type" not in result:
                result["document_type"] = "unknown"
            if "document_name" not in result:
                result["document_name"] = "Название не определено"
            if "confidence" not in result:
                result["confidence"] = 0.0
            if "description" not in result:
                result["description"] = "Описание не предоставлено"
            if "key_indicators" not in result:
                result["key_indicators"] = []
            
            result["titles_count"] = len(titles)
            
            logger.info(f"Определен тип документа: {result.get('document_type', 'unknown')}")
            logger.info(f"Название документа: {result.get('document_name', 'Название не определено')}")
            logger.info(f"Уверенность: {result.get('confidence', 0.0):.2f}")
            return result
        
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON ответа LLM: {e}")
        logger.error(f"Ответ LLM (первые 500 символов): {response_text[:500] if 'response_text' in locals() else 'N/A'}")
        return {
            "document_type": "unknown",
            "document_name": "Название не определено",
            "confidence": 0.0,
            "description": f"Ошибка при определении типа: {str(e)}",
            "titles_count": len(titles),
            "error": "json_decode_error",
            "key_indicators": []
        }
    except Exception as e:
        logger.error(f"Ошибка при определении типа документа: {e}")
        return {
            "document_type": "unknown",
            "document_name": "Название не определено",
            "confidence": 0.0,
            "description": f"Ошибка при определении типа: {str(e)}",
            "titles_count": len(titles),
            "error": str(e),
            "key_indicators": []
        }


def create_llm_chunks(
    data_source: Union[str, Dict[str, Any]], 
    min_size: int = 50,
    document_name: str = None,
    document_type_info: Dict[str, Any] = None
) -> List[Dict[str, Any]]:
    """
    Создаёт чанки в формате, готовом для передачи в LLM.
    
    Args:
        data_source: Путь к JSON файлу (str) или словарь с данными дерева (Dict)
        min_size: Минимальный размер текста для создания чанка (по умолчанию 50)
        document_name: Имя документа для генерации fragment_id
        document_type_info: Информация о типе документа (для добавления source)
    
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
        
        # Проверяем, является ли загруженный файл файлом с готовыми чанками
        # Если это словарь с ключом "chunks", значит это уже готовые чанки
        if isinstance(data, dict) and "chunks" in data:
            # Это файл с готовыми чанками - возвращаем их напрямую
            chunks_list = data["chunks"]
            if isinstance(chunks_list, list):
                logger.info(f"Обнаружен файл с готовыми чанками, возвращаем {len(chunks_list)} чанков")
                return chunks_list
    else:
        # Это уже словарь с данными
        data = data_source
        if document_name is None:
            document_name = "document"
        
        # Проверяем, является ли переданный словарь файлом с готовыми чанками
        if isinstance(data, dict) and "chunks" in data:
            chunks_list = data["chunks"]
            if isinstance(chunks_list, list):
                logger.info(f"Обнаружены готовые чанки, возвращаем {len(chunks_list)} чанков")
                return chunks_list
    
    chunks = []
    chunk_counter = 0
    
    def split_content_into_items(content: str) -> List[str]:
        """
        Разбивает content на отдельные нумерованные пункты.
        Возвращает список пунктов, где каждый пункт начинается с цифры и точки/скобки.
        Формат: "1. текст", "2. текст", "1) текст", "2) текст"
        """
        if not content or not content.strip():
            return []
        
        import re
        # Паттерн для нумерованных пунктов:
        # Начинается с начала строки или после двойного переноса строки
        # Затем цифра + точка/скобка + пробел + текст до следующего пункта или конца
        # Примеры: "1. текст", "2. текст\n\nеще текст", "1) текст"
        # Учитываем, что пункт может содержать несколько абзацев до следующего пункта
        item_pattern = re.compile(
            r'(?:^|\n\n|\n)(\d+[\.\)]\s+(?:[^\n]+(?:\n(?!\d+[\.\)])[^\n]*)*))',
            re.MULTILINE
        )
        
        matches = list(item_pattern.finditer(content))
        
        if not matches:
            # Если не найдены нумерованные пункты, проверяем есть ли нумерация в начале content
            # Если content начинается с цифры и точки/скобки - это один пункт
            if re.match(r'^\d+[\.\)]\s+', content.strip()):
                return [content.strip()]
            # Иначе возвращаем весь content как один пункт (если >= min_size будет проверено позже)
            return [content.strip()] if content.strip() else []
        
        items = []
        # Извлекаем каждый пункт (только текст пункта, без лишних символов)
        for match in matches:
            item_text = match.group(1).strip()
            if item_text:
                items.append(item_text)
        
        # Если не нашли пункты через паттерн, но content не пустой, возвращаем весь content
        if not items and content.strip():
            items = [content.strip()]
        
        return items
    
    def create_chunk(breadcrumb_text: str, title: str, item_content: str, breadcrumb_path: list, level: int, depth: int) -> Dict[str, Any]:
        """
        Создает один чанк из breadcrumb, title и content одного пункта.
        """
        nonlocal chunk_counter
        
        # Формируем combined_text: breadcrumb > title\n\nitem_content
        if breadcrumb_text and title:
            combined_text = f"{breadcrumb_text} > {title}"
            if item_content:
                combined_text += f"\n\n{item_content}"
        elif title:
            combined_text = title
            if item_content:
                combined_text += f"\n\n{item_content}"
        elif breadcrumb_text:
            combined_text = breadcrumb_text
            if item_content:
                combined_text += f"\n\n{item_content}"
        else:
            combined_text = item_content
        
        # Проверяем минимальный размер чанка (min_size символов)
        if len(combined_text.strip()) < min_size:
            return None
        
        chunk_counter += 1
        
        return {
            "fragment_id": f"{document_name}_chunk_{chunk_counter}",
            "hierarchy_context": {
                "breadcrumb_path": breadcrumb_path,
                "breadcrumb_text": breadcrumb_text,
                "current_level": level,
                "depth": depth
            },
            "fragment_data": {
                "title": title,
                "content": item_content,  # Только один пункт
                "combined_text": combined_text,
                "is_leaf_node": True,
                "has_children": False,
                "text_length": len(combined_text),
                "source": document_type_info.get("document_name", "") if document_type_info else ""
            }
        }
    
    def process_node(node, breadcrumb_titles=[], breadcrumb_levels=[], depth=0):
        nonlocal chunk_counter
        
        title = node.get("title", "").strip()
        content = node.get("content", "").strip()
        level = node.get("level", 0)
        children = node.get("children", [])
        
        # Обновляем breadcrumb для текущего узла (включая его title)
        # Это будет использоваться для детей этого узла
        current_breadcrumb_titles = breadcrumb_titles + [title] if title else breadcrumb_titles
        current_breadcrumb_levels = breadcrumb_levels + [level] if title else breadcrumb_levels
        
        # Формируем breadcrumb path для текущего узла
        breadcrumb_path = []
        # Защита от неправильной распаковки: убеждаемся, что списки имеют одинаковую длину
        min_len = min(len(breadcrumb_titles), len(breadcrumb_levels))
        for i in range(min_len):
            bc_title = breadcrumb_titles[i]
            bc_level = breadcrumb_levels[i]
            breadcrumb_path.append({
                "level": bc_level,
                "title": bc_title,
                "position": i
            })
        
        # Breadcrumb text - только родительские заголовки
        breadcrumb_text = " > ".join(breadcrumb_titles) if breadcrumb_titles else ""
        
        # Создаём чанки ТОЛЬКО для листовых узлов (без детей)
        # Узлы с детьми НЕ создают чанки для себя - только для своих детей
        is_leaf = len(children) == 0
        
        if is_leaf:
            # Листовой узел - разбиваем content на отдельные пункты и создаем чанк для каждого
            if content:
                # Разбиваем content на отдельные нумерованные пункты
                items = split_content_into_items(content)
                
                if items:
                    # Создаем отдельный чанк для каждого пункта
                    for item in items:
                        chunk = create_chunk(breadcrumb_text, title, item, breadcrumb_path, level, depth)
                        if chunk:  # Проверка на минимальный размер
                            chunks.append(chunk)
                else:
                    # Если не удалось разбить на пункты, создаем чанк из всего content
                    chunk = create_chunk(breadcrumb_text, title, content, breadcrumb_path, level, depth)
                    if chunk:  # Проверка на минимальный размер
                        chunks.append(chunk)
            elif title:
                # Есть только title, без content - создаем чанк если достаточно длинный
                chunk = create_chunk(breadcrumb_text, title, "", breadcrumb_path, level, depth)
                if chunk:  # Проверка на минимальный размер
                    chunks.append(chunk)
        
        # Рекурсия в детей - передаем обновленный breadcrumb (включая текущий title)
        for child in children:
            process_node(child, current_breadcrumb_titles, current_breadcrumb_levels, depth + 1)
    
    process_node(data)
    return chunks


def process_document_to_chunks(
    document_path: str,
    min_size: int = 50
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Полный цикл обработки документа: документ → docx2json_outline → определение типа → chunker → чанки для LLM.
    
    Args:
        document_path: Путь к исходному документу (DOCX, PDF и т.д.)
        min_size: Минимальный размер текста для создания чанка
    
    Returns:
        Кортеж: (список чанков в формате для LLM, информация о типе документа)
    """
    # Нормализуем путь к документу
    doc_path = Path(document_path).resolve()
    if not doc_path.exists():
        raise FileNotFoundError(f"Документ не найден: {doc_path}")
    
    logger.info(f"Обработка документа: {doc_path}")
    
    # Шаг 1: Обработка документа через docx2json_outline
    logger.info("[Шаг 1] Извлечение структуры документа...")
    result = extract_outline_from_document(str(doc_path))
    # Защита от неправильной распаковки: убеждаемся, что возвращается одно значение
    if isinstance(result, tuple):
        if len(result) == 1:
            tree = result[0]
        elif len(result) >= 2:
            # Если возвращается больше значений, берем первое (дерево)
            logger.warning(f"extract_outline_from_document вернул кортеж из {len(result)} значений, используем первое")
            tree = result[0]
        else:
            raise ValueError(f"extract_outline_from_document вернул пустой кортеж")
    else:
        tree = result
    logger.info("✓ Структура извлечена")
    
    # Шаг 2: Определение типа документа
    logger.info("[Шаг 2] Определение типа документа...")
    titles = extract_all_titles(tree)
    document_type_info = identify_document_type(titles)
    logger.info(f"✓ Тип документа: {document_type_info.get('document_type', 'unknown')}")
    logger.info(f"✓ Название документа: {document_type_info.get('document_name', 'Название не определено')}")
    
    # Шаг 3: Создание чанков
    logger.info("[Шаг 3] Создание чанков для LLM...")
    document_name = doc_path.stem
    chunks = create_llm_chunks(
        tree, 
        min_size=min_size, 
        document_name=document_name,
        document_type_info=document_type_info
    )
    logger.info(f"✓ Создано {len(chunks)} чанков")
    
    return chunks, document_type_info


@app.get("/")
async def root():
    return {
        "message": "GenA Chunker Service",
        "version": "2.0.0",
        "description": "Сервис для создания чанков из документов с использованием docx2json_outline",
        "endpoints": {
            "/chunk/": "POST - обработка загруженного документа",
            "/chunk-docx/": "POST - legacy endpoint для DOCX файлов",
            "/health": "GET - проверка состояния сервиса"
        }
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "chunker", "version": "2.0.0"}


@app.post("/chunk/")
async def chunk_file(
    file: UploadFile = File(...),
    min_size: int = Query(50, description="Минимальный размер текста для создания чанка")
):
    """
    Обработка загруженного документа: извлечение структуры и создание чанков для LLM.
    Поддерживает DOCX, PDF и другие форматы, которые поддерживает docx2json_outline.
    """
    logger.info(f"Получен запрос на обработку файла: {file.filename}, размер: {file.size} bytes")
    
    # Определяем расширение файла
    file_extension = file.filename.lower().split('.')[-1] if '.' in file.filename else ''
    logger.info(f"Расширение файла: {file_extension}")
    
    # Создаем временный файл
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as tmp:
            content = await file.read()
            logger.info(f"Прочитано {len(content)} байт из загруженного файла")
            tmp.write(content)
            tmp_path = tmp.name
            logger.info(f"Создан временный файл: {tmp_path}")
        
        # Обрабатываем документ через docx2json_outline и создаем чанки
        result = process_document_to_chunks(tmp_path, min_size=min_size)
        # Защита от неправильной распаковки: убеждаемся, что возвращается ровно 2 значения
        if isinstance(result, tuple) and len(result) == 2:
            chunks, document_type_info = result
        else:
            raise ValueError(f"process_document_to_chunks вернул неожиданное количество значений: {len(result) if isinstance(result, tuple) else 'не кортеж'}")
        
        logger.info(f"Успешно обработан файл, создано {len(chunks)} чанков")
        
        # Преобразуем чанки в формат для ответа
        # Извлекаем только текстовые данные для обратной совместимости
        chunks_text = [chunk["fragment_data"]["combined_text"] for chunk in chunks]
        
        return {
            "filename": file.filename,
            "file_type": file_extension,
            "num_chunks": len(chunks),
            "chunks": chunks_text,  # Простой список текстов для обратной совместимости
            "chunks_detailed": chunks,  # Полная информация о чанках с иерархией
            "chunking_method": "hierarchical_outline",
            "min_size": min_size,
            "document_type": document_type_info  # Информация о типе документа
        }
        
    except Exception as e:
        logger.error(f"Ошибка при обработке файла: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Ошибка при обработке файла: {str(e)}"}
        )
    finally:
        # Удаляем временный файл
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
            logger.info(f"Удален временный файл: {tmp_path}")


@app.post("/chunk-docx/")
async def chunk_docx(file: UploadFile = File(...), min_size: int = 50):
    """
    Legacy endpoint для DOCX файлов только.
    """
    if not file.filename.lower().endswith(".docx"):
        return JSONResponse(
            status_code=400, 
            content={"error": "Этот endpoint поддерживает только .docx файлы."}
        )
    
    return await chunk_file(file, min_size=min_size)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8517)
