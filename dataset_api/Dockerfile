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

# Запуск приложения
CMD ["uvicorn", "dataset_api:app", "--host", "0.0.0.0", "--port", "8789"]