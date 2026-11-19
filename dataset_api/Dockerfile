FROM python:3.12-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . /app

# Порт, который будет слушать приложение
EXPOSE 8789

# Запуск приложения.
# Несколько воркеров: обработчики ходят в Mongo синхронным pymongo, поэтому один
# медленный запрос иначе морозит весь API — включая /tasks/claim и хартбиты
# воркера, из-за чего живые задачи возвращались в pending и перезапускались.
ENV DATASET_API_WORKERS=4
CMD ["sh", "-c", "uvicorn dataset_api:app --host 0.0.0.0 --port 8789 --workers ${DATASET_API_WORKERS}"]