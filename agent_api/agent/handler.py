from typing import List
from pydantic import BaseModel
from config import MAX_LEN_USER_PROMPT, MONGO_DB_NAME
from agent.safe.length_limit import LengthLimitProtector
from agent.safe.protection_result import ProtectionStatus
from agent.assistant_graph import GENAAssistant
from agent.base import BaseHandler
from agent.pipeline_modes import normalize_pipeline_mode
from agent.runnables import create_GENA_runnables_ollama
from typing import Optional, Dict, Any
import time
import logging
from agent.document_kinds import normalize_document_kind

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
        """Цепочки по умолчанию — на модели `default` из реестра.

        Раньше они строились без конфига и llm_factory брал голые LLM_URL_MODEL /
        LLM_MODEL_NAME из env: provider-kwargs (например, reasoning_effort у
        thinking-модели) до них не доходили, и все задачи без явного model_id
        генерировали с включёнными рассуждениями.
        """
        default_cfg = self._default_model_config()
        self._GENA_runnables = create_GENA_runnables_ollama(
            generation_model=default_cfg,
            validation_model=default_cfg,
        )

    @staticmethod
    def _model_config_dict(cfg) -> Dict[str, Any]:
        return {
            "model_name": cfg.model_name,
            "base_url": cfg.base_url,
            "api_key": cfg.api_key,
            "provider": cfg.provider,
            "extra": cfg.extra,
        }

    def _default_model_config(self) -> Optional[Dict[str, Any]]:
        """Конфиг модели `default` из реестра (env LLM_* + extra её MODEL_ENDPOINTS-двойника)."""
        from models_registry import registry
        cfg = registry.get_default()
        return self._model_config_dict(cfg) if cfg else None

    def _resolve_model_config(self, model_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Возвращает {"model_name", "base_url", "api_key", "provider", "extra"}
        по model_id из реестра, или None если model_id не задан / 'default'
        (тогда используются цепочки по умолчанию).
        """
        if not model_id or model_id == "default":
            return None
        from models_registry import registry
        cfg = registry.get_model(model_id)
        if cfg is None:
            logger.warning(f"Model id '{model_id}' not found in registry, using default")
            return None
        return self._model_config_dict(cfg)

    def validation_chain_for(self, validation_model_id: Optional[str] = None):
        """Only the validation chain, cached per model.

        Revalidation needs nothing else; ``_get_runnables`` would rebuild all
        six chains on every call for a non-default model.
        """
        cfg = self._resolve_model_config(validation_model_id)
        if cfg is None:
            return self._GENA_runnables.validation_chain
        cache = self.__dict__.setdefault("_validation_chains", {})
        chain = cache.get(validation_model_id)
        if chain is None:
            from agent.nodes.llm_validator.validation_chain import create_validation_chain
            from agent.runnables import _extract_provider_kwargs

            chain = create_validation_chain(
                model_name=cfg.get("model_name"),
                base_url=cfg.get("base_url"),
                api_key=cfg.get("api_key"),
                provider=cfg.get("provider", "openai"),
                **_extract_provider_kwargs(cfg),
            )
            cache[validation_model_id] = chain
        return chain

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
        chunk_pre_validated: bool = False,
        pipeline_mode: str = "full",
        document_kind: str = "normative",
        block_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_text = source_text or prompt
        pipeline_mode = normalize_pipeline_mode(pipeline_mode)
        kind = normalize_document_kind(document_kind)

        if MAX_LEN_USER_PROMPT:
            limit = int(MAX_LEN_USER_PROMPT)
            check = LengthLimitProtector(limit).validate(prompt)
            if check.status is not ProtectionStatus.ok:
                raise ValueError(check.message)

        input_data = {
            "chunk": prompt,
            "question_type": question_type,
            "source": source,
            "source_text": source_text,
            "pipeline_mode": pipeline_mode,
            "document_kind": kind,
        }

        if language:
            input_data["language"] = language
        if chunk_pre_validated:
            input_data["chunk_pre_validated"] = True
        if block_type:
            input_data["block_type"] = block_type

        runnables = self._get_runnables(
            generation_model_id=generation_model_id,
            validation_model_id=validation_model_id,
        )
        input_data["refine_available"] = runnables.refine_question_chain is not None

        # The MongoDB checkpointer was hard-disabled (use_checkpointer = False),
        # so only this path ever ran; the retry-without-checkpoint fallback and
        # the MongoClient/MongoDBSaver imports it needed were dead weight.
        assistant = GENAAssistant(
            generate_question_chain=runnables.generate_question_chain,
            provocativeness_chain=runnables.provocativeness_chain,
            validation_chain=runnables.validation_chain,
            difficulty_chain=runnables.difficulty_chain,
            chunk_gate_chain=runnables.chunk_gate_chain,
            refine_question_chain=runnables.refine_question_chain,
            checkpointer=None,
        )
        output = assistant.graph.invoke(input_data, config={})

        shaped_output = {
            **output,
            "question_type": question_type,
            "pipeline_mode": pipeline_mode,
        }
        return {
            "question_type": question_type,
            "pipeline_mode": pipeline_mode,
            "output": shaped_output,
        }

    def ahandle_rephrase_questions(
        self,
        questions: List[Dict[str, Any]],
        model_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not questions:
            return []
        if not all(isinstance(q, dict) for q in questions):
            raise ValueError("Each question must be a dict with a non-empty 'task' field")
        if not all(isinstance(q.get("task"), str) and q.get("task", "").strip() for q in questions):
            raise ValueError("Each question must be a dict with a non-empty 'task' field")

        from agent.nodes.dynamic_implementation.rephrase_question import rephrase_questions_dicts

        model_cfg = self._resolve_model_config(model_id) or self._default_model_config()
        kwargs = {}
        if model_cfg:
            extra = model_cfg.get("extra", {})
            kwargs = {
                "model_name": model_cfg["model_name"],
                "base_url": model_cfg["base_url"],
                "api_key": model_cfg["api_key"],
                "provider": model_cfg.get("provider", "openai"),
            }
            for key in ("scope", "folder_id", "reasoning_effort"):
                if key in extra:
                    kwargs[key] = extra[key]

        return rephrase_questions_dicts(questions, **kwargs)
    