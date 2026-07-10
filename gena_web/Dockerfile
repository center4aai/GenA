FROM python:3.12.7-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Установка poetry
RUN pip install poetry
RUN pip install extra-streamlit-components
# Копирование файлов проекта
COPY . /app

# Streamlit theme
COPY gena/.streamlit /root/.streamlit

# Установка зависимостей через poetry
RUN poetry config virtualenvs.create false \
    && poetry install

# Self-signed TLS certificate so the browser treats this origin as a secure
# context (required for navigator.clipboard.writeText / st.code Copy to clipboard).
# Note: certificates live OUTSIDE /app on purpose — docker-compose.yaml mounts
# ./gena_web:/app as a volume which would hide anything created here at build
# time. To use a real certificate, mount your files over /certs/cert.pem and
# /certs/key.pem via docker-compose.
RUN mkdir -p /certs && \
    openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
        -subj "/CN=gena.local" \
        -keyout /certs/key.pem \
        -out /certs/cert.pem && \
    chmod 644 /certs/cert.pem /certs/key.pem

# Открытие порта
EXPOSE 8516

# Запуск приложения (HTTPS)
CMD ["streamlit", "run", "/app/gena/app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8516", \
     "--server.sslCertFile=/certs/cert.pem", \
     "--server.sslKeyFile=/certs/key.pem"]
