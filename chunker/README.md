# GenA Chunker Service

Сервис для семантического разбиения документов на чанки (фрагменты текста) для последующей генерации вопросов.

## Особенности

- **Семантическое разбиение** - использует модель multilingual-e5-large для понимания смысла текста
- **Умные чанки** - разбивает текст по смысловым границам, а не по символам
- **Поддержка русского языка** - оптимизирован для работы с русскими текстами

## Поддерживаемые форматы

- **DOCX** (.docx) - документы Microsoft Word
- **TXT** (.txt) - текстовые файлы
- **PDF** (.pdf) - документы PDF

## API Endpoints

### POST /chunk/
Основной endpoint для разбиения файлов на чанки.

**Поддерживаемые форматы:** DOCX, TXT, PDF

**Пример запроса:**
```bash
curl -X POST "http://localhost:8517/chunk/" \
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
  "chunk_size": 1000,
  "overlap": 50
}
```

### POST /chunk-docx/
Legacy endpoint только для DOCX файлов.

### GET /health
Проверка состояния сервиса.

### GET /
Информация о сервисе.

## Параметры семантического чанкинга

- **model**: multilingual-e5-large (модель для эмбеддингов)
- **threshold**: 0.85 (порог семантической схожести)
- **chunk_size**: 512 (максимальный размер чанка)
- **min_sentences**: 1 (минимальное количество предложений)
- **min_chunk_size**: 1 (минимальный размер чанка)

## Запуск

### Локально
```bash
pip install -r requirements.txt
python main.py
```

### Docker
```bash
docker build -t gena-chunker .
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
