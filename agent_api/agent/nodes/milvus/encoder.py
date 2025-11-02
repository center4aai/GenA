import logging
from typing import List

from openai import OpenAI

from src.config import TEI_API_KEY, TEI_MODEL_NAME, TEI_URL_EMBEDDER

logger = logging.getLogger(__name__)


class EmbeddingEncoder:
    def __init__(self, model_name: str = TEI_MODEL_NAME, base_url: str = TEI_URL_EMBEDDER, api_key: str = TEI_API_KEY):
        """
        Инициализация клиента для создания эмбеддингов

        Args:
            model_name: Название модели для эмбеддингов (по умолчанию из конфигурации)
            base_url: URL TEI сервера (по умолчанию из конфигурации)
            api_key: API-ключ для доступа к сервису TEI (по умолчанию из конфигурации)
        """
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model_name = model_name

    def encode(self, text: str) -> List[float]:
        """
        Создает эмбеддинг для текста

        Args:
            text: Текст для векторизации

        Returns:
            Список чисел (эмбеддинг) или пустой список при ошибке
        """
        try:
            response = self.client.embeddings.create(model=self.model_name, input=[text], encoding_format="float")
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Ошибка при создании эмбеддинга: {str(e)}")
            return []
