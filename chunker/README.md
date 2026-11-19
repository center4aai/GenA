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
- **chunking_method**: `rules` (по умолчанию в API), `layout`, `semantic`, `llm` — см. таблицу ниже
- **document_kind**: `normative` (по умолчанию), `textbook`, `encyclopedia` — выбирает обработчик и правила разбиения

### Методы чанкинга (`chunking_method`, выбор в UI при создании набора)

Дефолт в UI зависит от вида документа: «Учебник, книга» (`textbook`) — `semantic`, layout и остальные методы
выбираются вручную; нормативка — только `rules`; энциклопедия — `rules`. Сам API без параметра берёт `rules`.

| Метод | Что делает | Для чего | Требования |
|---|---|---|---|
| `rules` (дефолт API и UI для нормативки/энциклопедии) | Заголовки + регулярные правила (см. ниже) | Любые документы, нормативка только так | нет |
| `layout` (вручную) | PDF читается pdfplumber: основной текст против врезок/подписей/заданий по шрифту, кеглю и заливке (`chunker/textbook_layout.py`); врезки → отдельные чанки `inset`, подписи → `caption`, задания/схемы/колонтитулы выбрасываются | PDF учебников с текстовым слоем; иначе фолбэк на `rules` с причиной в `chunking_fallback` | `pdfplumber` |
| `semantic` (UI-дефолт для учебников) | `rules`/`layout` + границы чанков по провалам близости эмбеддингов соседних абзацев (`chunker/semantic_segmenter.py`); для PDF-учебника основа — layout-извлечение, для DOCX/TXT — правила | Учебники и книги; крупные разделы, где нужны смысловые границы. Без `CHUNKER_EMBEDDING_URL` метод недоступен (400 / `chunker_method_unavailable`); при ошибке эмбеддера узел пакуется по правилам (`semantic_errors` в статистике) | сервис `embedder` стека (TEI, `deepvk/USER-bge-m3`, ~1 ГБ видеопамяти) по `CHUNKER_EMBEDDING_URL=http://embedder:80/v1`; модель и GPU — `EMBEDDER_MODEL_ID`, `EMBEDDER_GPU_DEVICE` |
| `llm` | Модель размечает пронумерованные абзацы окнами по 12k символов: типы (inset, exercise, caption, toc, imprint, glossary_entry…) и границы, текст не переписывает (`chunker/llm_segmentation/`); фолбэк на правила по окну при ошибке/дедлайне | DOCX/OCR-тексты без надёжной вёрстки | модель из `MODEL_ENDPOINTS` (`CHUNKER_LLM_SEG_MODEL_ID`) или `LLM_URL_MODEL`/`LLM_MODEL_NAME`; ключ — `api_key_env` элемента или `LLM_API_KEY`; десятки минут на учебник |

`GET /chunking-methods/` возвращает доступность методов по текущей конфигурации. В ответе `/chunk/`:
`chunking_method` (запрошенный), `chunking_engine` (что реально отработало: `hierarchical_outline`, `layout_pdf`,
`…+semantic`, `llm_segmentation`), `chunking_fallback` (причина отката), `dropped_blocks`, `chunks_by_block_type`.
Отчёт для тестировщиков: `scripts/chunk_report.py <файл> --kind textbook --method layout --out report.md`.

### Проза учебников и энциклопедий (`document_kind=textbook|encyclopedia`)

- Абзацы пакуются до `CHUNKER_TEXTBOOK_TARGET_CHARS` (1500), потолок `CHUNKER_TEXTBOOK_MAX_CHARS` (2000);
  длинный абзац режется по предложениям с учётом сокращений («г.», «т.к.», инициалы — `chunker/text_utils.py`).
- Списки 1,2,3 / а,б,в вместе с вводным абзацем — одна единица, не дробятся до `CHUNKER_TEXTBOOK_LIST_HARD_CAP` (6000).
- Словарь терминов («Словарь…», «Основные понятия») — 1 статья = 1 чанк (`block_type=glossary_entry`).
- Выбрасываются (счётчики в ответе `dropped_blocks`): разделы «Вопросы и задания», «Проверяем себя», «Практикум»,
  «Содержание», «Введение», «Список сокращений», «Условные обозначения», «Ключевые понятия»; абзацы-задания
  («Выясните…», «⬤ Узнайте…»); оглавление, выходные данные и списки сокращений, распознанные структурно;
  огрызки вёрстки короче `CHUNKER_PROSE_DEBRIS_MAX_CHARS` (120) без точки на конце.
  Списки заголовков переопределяются через `CHUNKER_TEXTBOOK_DROP_HEADINGS` / `CHUNKER_TEXTBOOK_KEEP_HEADINGS`
  (через запятую), сокращения дополняются через `CHUNKER_ABBREVIATIONS_EXTRA`.
- Короткие единицы укрупняются до `CHUNKER_PROSE_MIN_CHARS_TEXTBOOK` (400) / `CHUNKER_PROSE_MIN_CHARS_ENCYCLOPEDIA` (200).
- Каждый чанк несёт `fragment_data.block_type` (`body` | `list` | `inset` | `caption` | `glossary_entry`) и
  `page_start` / `page_end` (заполняются layout-извлечением PDF; иначе `null`).
- Ответ `/chunk/` дополнительно содержит `dropped_blocks` (`{тип: {count, chars}}`) и `chunks_by_block_type`.

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
# Из корня репозитория (нужен config/llm_defaults.env)
docker build -f chunker/Dockerfile -t gena-chunker .

# Запуск контейнера
docker run -p 8517:8517 gena-chunker
```

## Интеграция с GenA

Сервис интегрирован с основным приложением GenA через переменную окружения `CHUNKER_URL` (task_worker добавляет `/chunk/` сам):

### Для Docker Compose:
```bash
export CHUNKER_URL="http://chunker:8517"
```

### Для локальной разработки:
```bash
export CHUNKER_URL="http://localhost:8517"
```

## Зависимости

Основные зависимости:
- `fastapi` - веб-фреймворк
- `uvicorn` - ASGI сервер
- `markitdown` - конвертация документов в markdown
- `python-docx` - работа с DOCX файлами
- `lxml` - парсинг XML структуры документов

Полный список зависимостей см. в `requirements.txt`.
