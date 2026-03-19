from typing import Tuple, List
from langgraph.checkpoint.mongodb import MongoDBSaver
from pydantic import BaseModel
from pymongo import MongoClient
from requests.auth import _basic_auth_str
from config import MAX_LEN_USER_PROMPT, MONGO_DB_NAME
from agent.assistant_graph import GENAAssistant
from agent.base import BaseHandler
from agent.runnables import create_GENA_runnables_ollama
from typing import Optional, Dict, Any
import time
import logging

logger = logging.getLogger(__name__)


class GENAOptions(BaseModel):
    """Конфигурационные параметры для GENAHandler"""
    mongodb_uri: str


class GENAHandler(BaseHandler):
    """Обработчик запросов для GENA ассистента"""

    def __init__(self, options: GENAOptions) -> None:
        self._options = options
        self._init_runnables()

    def _init_runnables(self) -> None:
        """Инициализация цепочек выполнения с моделью по умолчанию"""
        self._GENA_runnables = create_GENA_runnables_ollama()

    def _resolve_model_config(self, model_id: Optional[str]) -> Optional[Dict[str, str]]:
        """
        Возвращает {"model_name": ..., "base_url": ..., "api_key": ...}
        по model_id из реестра, или None если model_id не задан / 'default'.
        """
        if not model_id or model_id == "default":
            return None
        from models_registry import registry
        cfg = registry.get_model(model_id)
        if cfg is None:
            logger.warning(f"Model id '{model_id}' not found in registry, using default")
            return None
        return {
            "model_name": cfg.model_name,
            "base_url": cfg.base_url,
            "api_key": cfg.api_key,
        }

    def _get_runnables(
        self,
        generation_model_id: Optional[str] = None,
        validation_model_id: Optional[str] = None,
    ):
        """
        Возвращает runnables — дефолтные или пересозданные под конкретную модель.
        """
        gen_cfg = self._resolve_model_config(generation_model_id)
        val_cfg = self._resolve_model_config(validation_model_id)

        if gen_cfg is None and val_cfg is None:
            return self._GENA_runnables

        return create_GENA_runnables_ollama(
            generation_model=gen_cfg,
            validation_model=val_cfg,
        )

    def ahandle_prompt(
        self,
        prompt: str,
        question_type: str,
        source: str,
        chat_id: str,
        source_text: Optional[str] = None,
        language: Optional[str] = None,
        generation_model_id: Optional[str] = None,
        validation_model_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_text = source_text or prompt

        input_data = {
            "chunk": prompt,
            "question_type": question_type,
            "source": source,
            "source_text": source_text,
        }

        if language:
            input_data["language"] = language

        use_checkpointer = False

        unique_thread_id = f"{chat_id}_{int(time.time() * 1000)}"
        config = {"configurable": {"thread_id": unique_thread_id}} if use_checkpointer else {}

        runnables = self._get_runnables(
            generation_model_id=generation_model_id,
            validation_model_id=validation_model_id,
        )

        if use_checkpointer:
            mongodb_client = MongoClient(self._options.mongodb_uri)
            checkpointer = MongoDBSaver(mongodb_client, database_name=MONGO_DB_NAME)

            assistant = GENAAssistant(
                generate_question_chain=runnables.generate_question_chain,
                provocativeness_chain=runnables.provocativeness_chain,
                validation_chain=runnables.validation_chain,
                difficulty_chain=runnables.difficulty_chain,
                checkpointer=checkpointer,
            )

            try:
                output = assistant.graph.invoke(input_data, config=config)
            except Exception as e:
                if "too many values to unpack" in str(e) or "checkpoint" in str(e).lower():
                    logger.warning(f"Error with checkpoint for thread_id {unique_thread_id}, retrying without checkpoint: {str(e)}")
                    assistant_no_checkpoint = GENAAssistant(
                        generate_question_chain=runnables.generate_question_chain,
                        provocativeness_chain=runnables.provocativeness_chain,
                        validation_chain=runnables.validation_chain,
                        difficulty_chain=runnables.difficulty_chain,
                        checkpointer=None,
                    )
                    output = assistant_no_checkpoint.graph.invoke(input_data, config=config)
                else:
                    raise
        else:
            assistant = GENAAssistant(
                generate_question_chain=runnables.generate_question_chain,
                provocativeness_chain=runnables.provocativeness_chain,
                validation_chain=runnables.validation_chain,
                difficulty_chain=runnables.difficulty_chain,
                checkpointer=None,
            )
            output = assistant.graph.invoke(input_data, config=config)

        return {'output': output}

    def ahandle_rephrase_questions(
        self,
        questions: List[str],
        model_id: Optional[str] = None,
    ) -> List[str]:
        if not questions:
            return []

        from agent.nodes.dynamic_implementation.rephrase_question import rephrase_questions

        model_cfg = self._resolve_model_config(model_id)
        kwargs = {}
        if model_cfg:
            kwargs = {
                "model_name": model_cfg["model_name"],
                "base_url": model_cfg["base_url"],
                "api_key": model_cfg["api_key"],
            }

        question_texts = [q.get('task', '') for q in questions]
        rephrased_texts = rephrase_questions(question_texts, **kwargs)

        for q, rephrased in zip(questions, rephrased_texts):
            q['task'] = rephrased

        return questions
    