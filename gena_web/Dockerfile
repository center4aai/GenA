FROM python:3.12.7-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Установка poetry
RUN pip install poetry
RUN pip install extra-streamlit-components
# Копирование файлов проекта
COPY . /app

# Установка зависимостей через poetry
RUN poetry config virtualenvs.create false \
    && poetry install

# Открытие порта
EXPOSE 8516

# Запуск приложения
CMD ["streamlit", "run", "/app/gena/app.py", "--server.address=0.0.0.0", "--server.port=8516"]
