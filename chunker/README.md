# GenA Chunker Service

Сервис для создания чанков из документов с сохранением иерархической структуры для последующей передачи в LLM.

## Особенности

- **Иерархическое разбиение** - использует `docx2json_outline.py` для извлечения структуры документа
- **Сохранение контекста** - каждый чанк содержит информацию о своей позиции в иерархии документа
- **Умные чанки** - разбивает документ по структурным границам (разделы, статьи, пункты)
- **Поддержка русского языка** - оптимизирован для работы с русскими юридическими документами

## Процесс обработки

1. **Документ** (DOCX, PDF и т.д.) → `docx2json_outline.py` → **JSON дерево структуры**
2. **JSON дерево** → `chunker` → **Чанки для LLM с иерархическим контекстом**

## Поддерживаемые форматы

- **DOCX** (.docx) - документы Microsoft Word
- **PDF** (.pdf) - документы PDF
- Другие форматы, поддерживаемые `docx2json_outline.py` (через markitdown)

## API Endpoints

### POST /chunk/

Основной endpoint для обработки документов и создания чанков.

**Параметры:**
- `file` (обязательный) - загружаемый файл
- `min_size` (опциональный, по умолчанию 50) - минимальный размер текста для создания чанка

**Поддерживаемые форматы:** DOCX, PDF и другие форматы, поддерживаемые docx2json_outline

**Пример запроса:**
```bash
curl -X POST "http://localhost:8517/chunk/?min_size=50" \
     -H "accept: application/json" \
     -H "Content-Type: multipart/form-data" \
     -F "file=@document.docx"
```

**Ответ:**
```json
{
  "filename": "document.docx",
  "file_type": "docx",
  "num_chunks": 15,
  "chunks": ["текст чанка 1", "текст чанка 2", ...],
  "chunks_detailed": [
    {
      "fragment_id": "document_chunk_1",
      "hierarchy_context": {
        "breadcrumb_path": [
          {"level": 1, "title": "Раздел 1", "position": 0},
          {"level": 2, "title": "Статья 1", "position": 1}
        ],
        "breadcrumb_text": "Раздел 1 > Статья 1",
        "current_level": 2,
        "depth": 2
      },
      "fragment_data": {
        "title": "Статья 1",
        "content": "Текст статьи...",
        "combined_text": "Статья 1\n\nТекст статьи...",
        "is_leaf_node": true,
        "has_children": false,
        "text_length": 150
      }
    },
    ...
  ],
  "chunking_method": "hierarchical_outline",
  "min_size": 50
}
```

### POST /chunk-docx/

Legacy endpoint только для DOCX файлов.

**Параметры:**
- `file` (обязательный) - загружаемый DOCX файл
- `min_size` (опциональный, по умолчанию 50) - минимальный размер текста для создания чанка

### GET /health

Проверка состояния сервиса.

**Ответ:**
```json
{
  "status": "healthy",
  "service": "chunker",
  "version": "2.0.0"
}
```

### GET /

Информация о сервисе.

## Параметры чанкинга

- **min_size**: Минимальный размер текста для создания чанка (по умолчанию: 50 символов)
- **chunking_method**: `hierarchical_outline` - разбиение по иерархической структуре документа

## Структура чанка

Каждый чанк содержит:

- **fragment_id**: Уникальный идентификатор чанка
- **hierarchy_context**: Контекст иерархии документа
  - **breadcrumb_path**: Путь к чанку в иерархии (массив объектов с level, title, position)
  - **breadcrumb_text**: Текстовое представление пути (например, "Раздел 1 > Статья 1")
  - **current_level**: Уровень текущего узла
  - **depth**: Глубина вложенности
- **fragment_data**: Данные фрагмента
  - **title**: Заголовок узла
  - **content**: Содержимое узла
  - **combined_text**: Объединенный текст (title + content)
  - **is_leaf_node**: Является ли узел листовым
  - **has_children**: Есть ли дочерние узлы
  - **text_length**: Длина текста

## Тестирование

### Способ 1: Прямой вызов функций (рекомендуется для тестирования)

```bash
cd chunker
python test_chunker.py path/to/document.docx
python test_chunker.py path/to/document.docx --min-size 50 --output result.json
```

Этот скрипт:
- Обрабатывает документ через `docx2json_outline`
- Создает чанки
- Показывает статистику и примеры
- Сохраняет результат в JSON файл

### Способ 2: Через API

```bash
# Терминал 1: Запустите сервис
cd chunker
python main.py

# Терминал 2: Запустите тест
python test_api.py path/to/document.docx
```

### Способ 3: Использование chunker.py из корня проекта

```bash
# Из корневой директории проекта
python chunker.py path/to/document.docx -o output_dir --save-intermediate
```

### Способ 4: Через curl (если сервис запущен)

```bash
curl -X POST "http://localhost:8517/chunk/?min_size=50" \
     -H "Content-Type: multipart/form-data" \
     -F "file=@document.docx"
```

### Способ 5: Тестирование генерации вопросов из чанков

После создания чанков можно протестировать генерацию вопросов:

```bash
cd chunker

# Прямой вызов (нужна настройка окружения для agent_api):
python test_question_generation.py files_test/chunks_Family_code_Russian_Federation_1-4.json

# Через API:
python test_question_generation.py files_test/chunks_Family_code_Russian_Federation_1-4.json \
     --api http://localhost:8518/process_prompt/

# Только один тип вопросов, первые 2 чанка:
python test_question_generation.py files_test/chunks_Family_code_Russian_Federation_1-4.json \
     --question-type one --limit 2

# Сохранить результаты:
python test_question_generation.py files_test/chunks_Family_code_Russian_Federation_1-4.json \
     --save
```

Этот скрипт:
- Загружает чанки из JSON файла
- Генерирует вопросы для каждого чанка (для всех типов или выбранных)
- Показывает результаты и статистику
- Может сохранять результаты в JSON файл

## Запуск

### Локально

```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск сервиса
python main.py
```

Сервис будет доступен по адресу: `http://localhost:8517`

### Docker

```bash
# Из папки chunker
cd chunker
docker build -t gena-chunker .

# Запуск контейнера
docker run -p 8517:8517 gena-chunker
```

## Интеграция с GenA

Сервис интегрирован с основным приложением GenA через переменную окружения `API_CHANKS_URL`:

### Для Docker Compose:
```bash
export API_CHANKS_URL="http://gena-chunker:8517/chunk/"
```

### Для локальной разработки:
```bash
export API_CHANKS_URL="http://localhost:8517/chunk/"
```

## Зависимости

Основные зависимости:
- `fastapi` - веб-фреймворк
- `uvicorn` - ASGI сервер
- `markitdown` - конвертация документов в markdown
- `python-docx` - работа с DOCX файлами
- `lxml` - парсинг XML структуры документов

Полный список зависимостей см. в `requirements.txt`.
