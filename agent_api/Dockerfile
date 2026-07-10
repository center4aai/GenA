FROM python:3.12-slim

WORKDIR /app

# Установка зависимостей
COPY agent_api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Код приложения и общие дефолты LLM (config/llm_defaults.env)
COPY agent_api/ /app/
COPY config/llm_defaults.env /app/config/llm_defaults.env

# Порт, который будет слушать приложение
EXPOSE 8790

# Запуск приложения
CMD ["uvicorn", "agent_api:app", "--host", "0.0.0.0", "--port", "8790", "--workers", "4"]