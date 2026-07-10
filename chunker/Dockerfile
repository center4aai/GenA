FROM python:3.12.7-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов зависимостей
COPY chunker/requirements.txt /app/requirements.txt

# Установка зависимостей
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Код приложения и общие дефолты LLM
COPY chunker/ /app/
COPY config/llm_defaults.env /app/config/llm_defaults.env

# Открытие порта
EXPOSE 8517

# Запуск приложения
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8517"]
