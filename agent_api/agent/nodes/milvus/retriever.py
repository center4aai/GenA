import logging
import time
from typing import Dict, List, TypedDict, Union

from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field
from pymilvus import Collection, MilvusException, connections

from src.config import MILVUS_HOST, MILVUS_PORT
from src.nodes.milvus.encoder import EmbeddingEncoder

logger = logging.getLogger(__name__)


class MilvusSearchInput(TypedDict):
    query: str


class MilvusSearchOutput(BaseModel):
    search_result: List[Dict[str, Union[str, int, float, None, Dict[str, Union[str, None]]]]] = Field(
        description="Результаты поиска по заданиям agent"
    )


class MilvusRetriever:
    def __init__(
        self,
        encoder: EmbeddingEncoder,
        host: str = MILVUS_HOST,
        port: int = MILVUS_PORT,
        connection_attempts: int = 3,
        retry_delay: int = 1,
    ):
        self.encoder = encoder
        self.host = host
        self.port = port
        self.connection_attempts = connection_attempts
        self.retry_delay = retry_delay

    def _connect_to_milvus(self) -> bool:
        for attempt in range(1, self.connection_attempts + 1):
            try:
                connections.connect("default", host=self.host, port=self.port)
                logger.info("Успешное подключение к Milvus")
                return True
            except MilvusException as e:
                logger.warning(f"Попытка подключения {attempt}/{self.connection_attempts} не удалась: {str(e)}")
                if attempt < self.connection_attempts:
                    time.sleep(self.retry_delay)
        logger.error("Не удалось подключиться к Milvus после всех попыток")
        return False

    def search(self, query: str) -> List[Dict[str, Union[str, int, float, None, Dict[str, Union[str, None]]]]]:
        query_embedding = self.encoder.encode(query)
        if not query_embedding:
            return []

        if not self._connect_to_milvus():
            return []

        try:
            collection = Collection("agentQ")
            collection.load()

            search_params = {
                "data": [query_embedding],
                "anns_field": "task_e5",
                "param": {"metric_type": "COSINE", "params": {}},
                "limit": 10,
                "output_fields": ["task", "text", "options"],
            }

            results = collection.search(**search_params)
            all_results = []
            for hit in results[0]:
                all_results.append({
                    "task": hit.fields.get("task", ""),
                    "text": hit.fields.get("text"),
                    "options": hit.fields.get("options", {}),
                    "score": float(hit.score),
                    "id": hit.id,
                })

            return sorted(all_results, key=lambda x: x["score"], reverse=True)[:5]

        except Exception as e:
            logger.error(f"Ошибка при выполнении поиска: {str(e)}")
            return []


def create_retriever_chain() -> Runnable[MilvusSearchInput, MilvusSearchOutput]:
    encoder = EmbeddingEncoder()
    retriever = MilvusRetriever(encoder=encoder)

    class MilvusSearchRunnable(Runnable[MilvusSearchInput, MilvusSearchOutput]):
        def invoke(self, input_data: MilvusSearchInput) -> MilvusSearchOutput:
            results = retriever.search(input_data["query"])
            return MilvusSearchOutput(search_result=results)

    return MilvusSearchRunnable()
