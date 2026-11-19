from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from contextlib import asynccontextmanager
from agent.handler import GENAHandler, GENAOptions
from agent.nodes.chunk_gate.chunk_gate import GateParseError, synthetic_gate_pass
from agent.pipeline_modes import normalize_pipeline_mode
from config import MONGO_DB_PATH
from models_registry import registry
from agent.document_kinds import normalize_document_kind
from agent.nodes.llm_validator.stored_question import stored_to_validator_question
from request_context import (
    GATE_LLM_ERROR_DETAIL,
    GATE_PARSE_ERROR_DETAIL,
    VALIDATION_INPUT_ERROR_DETAIL,
    UNKNOWN_VALIDATION_MODEL_DETAIL,
    VALIDATION_LLM_ERROR_DETAIL,
    RequestIdMiddleware,
    install_error_handlers,
    llm_error_to_http,
)
import asyncio
import logging
import traceback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ──────────────────── Request / Response models ────────────────────

class PromptRequest(BaseModel):
    prompt: str
    question_type: str
    source: str
    chat_id: int
    source_text: Optional[str] = None
    additional_params: Optional[dict] = None
    generation_model_id: Optional[str] = None
    validation_model_id: Optional[str] = None
    chunk_pre_validated: bool = False
    pipeline_mode: str = "full"
    document_kind: str = "normative"
    # body | list | inset | caption | glossary_entry — from the chunker's
    # fragment_data.block_type; None for legacy chunks.
    block_type: Optional[str] = None

class RephraseQuestionsRequest(BaseModel):
    dataset_name: str
    questions: list
    model_id: Optional[str] = None

class ResponseModel(BaseModel):
    status: str
    result: dict | list
    error: Optional[str] = None

class ModelInfo(BaseModel):
    id: str
    name: str
    base_url: str
    model_name: str
    provider: str = "openai"
    available: Optional[bool] = None
    served_models: Optional[List[str]] = None


# ──────────────────── App lifecycle ────────────────────

def get_academic_handler():
    try:
        options = GENAOptions(mongodb_uri=MONGO_DB_PATH)
        return GENAHandler(options)
    except Exception as e:
        logger.error(f"Error initializing GENA handler: {str(e)}")
        raise

@asynccontextmanager
async def lifespan(app: FastAPI):
    global handler
    try:
        handler = get_academic_handler()
        logger.info("GENA handler initialized successfully")
        yield
    except Exception as e:
        logger.error(f"Failed to initialize GENA handler: {str(e)}")
        raise

app = FastAPI(title="GENA Academic Handler API", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)
install_error_handlers(app, logger)


# ──────────────────── Endpoints ────────────────────

@app.get("/models/", response_model=List[ModelInfo])
async def list_models():
    """Возвращает список зарегистрированных моделей (без проверки доступности)."""
    return [
        ModelInfo(
            id=m.id,
            name=m.name,
            base_url=m.base_url,
            model_name=m.model_name,
            provider=m.provider,
        )
        for m in registry.list_models()
    ]


class ModelHealth(BaseModel):
    id: str
    name: str
    available: bool
    reason: Optional[str] = None

class ValidateQuestionRequest(BaseModel):
    """One stored question for quality revalidation (no generation, no refine)."""

    question: dict
    source_text: Optional[str] = None
    document_kind: str = "normative"
    validation_model_id: Optional[str] = None


class ChunkGateRequest(BaseModel):
    chunk: str
    question_type: str
    validation_model_id: Optional[str] = None
    document_kind: str = "normative"
    block_type: Optional[str] = None


@app.get("/models/health/", response_model=List[ModelHealth], response_model_exclude_none=True)
async def models_health():
    """Возвращает health-статус моделей из последнего фонового probe (быстрый)."""
    return [ModelHealth(**h) for h in registry.get_health()]


@app.get("/models/discover/", response_model=List[ModelInfo])
async def discover_models():
    """Опрашивает все endpoints и проверяет доступность моделей."""
    discovered = await registry.discover_models()
    return [ModelInfo(**m) for m in discovered]


@app.post("/process_prompt/", response_model=ResponseModel)
async def process_prompt(request: PromptRequest):
    try:
        logger.info(f"Processing prompt for chat_id: {request.chat_id}")

        prompt_text = request.prompt
        if isinstance(prompt_text, dict):
            prompt_text = prompt_text.get("fragment_data", {}).get("combined_text", str(prompt_text))
            logger.warning("Received dict instead of string for prompt, extracted text")

        source_text = request.source_text or prompt_text
        if isinstance(source_text, dict):
            source_text = source_text.get("fragment_data", {}).get("combined_text", str(source_text))

        if request.generation_model_id:
            logger.info(f"Using generation model: {request.generation_model_id}")
        if request.validation_model_id:
            logger.info(f"Using validation model: {request.validation_model_id}")
        pipeline_mode = normalize_pipeline_mode(request.pipeline_mode)

        try:
            result = await asyncio.to_thread(
                handler.ahandle_prompt,
                prompt=prompt_text,
                question_type=request.question_type,
                source=request.source,
                chat_id=str(request.chat_id),
                source_text=source_text,
                generation_model_id=request.generation_model_id,
                validation_model_id=request.validation_model_id,
                chunk_pre_validated=request.chunk_pre_validated,
                pipeline_mode=pipeline_mode,
                document_kind=request.document_kind or "normative",
                block_type=request.block_type,
            )
        except GateParseError as ge:
            # Gate внутри полного пайплайна: ответ модели не разобран. Это не
            # ошибка клиента (GateParseError — подкласс ValueError), а сбой
            # вывода LLM → 500 с константным detail (worker: model_output_invalid).
            logger.error(f"Gate parse error in ahandle_prompt: {ge}")
            raise HTTPException(status_code=500, detail=GATE_PARSE_ERROR_DETAIL)
        except ValueError as ve:
            if "too many values to unpack" in str(ve):
                logger.error(f"ValueError with unpacking in ahandle_prompt: {str(ve)}")
                logger.error(f"Full traceback:\n{traceback.format_exc()}")
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error(f"Exception in ahandle_prompt: {type(e).__name__}: {str(e)}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            raise

        return ResponseModel(status="success", result=result)
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error processing prompt: {error_msg}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        http_exc = llm_error_to_http(e, gate=False)
        if http_exc is not None:
            raise http_exc
        raise HTTPException(status_code=500, detail=error_msg)


@app.post("/rephrase_questions/", response_model=ResponseModel)
async def rephrase_questions(request: RephraseQuestionsRequest):
    try:
        logger.info("Processing rephrase questions")

        rephrased_questions = await asyncio.to_thread(
            handler.ahandle_rephrase_questions,
            questions=request.questions,
            model_id=request.model_id,
        )

        return ResponseModel(status="success", result=rephrased_questions)

    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error processing rephrase: {str(e)}")
        http_exc = llm_error_to_http(e, gate=False)
        if http_exc is not None:
            raise http_exc
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chunk_gate/", response_model=ResponseModel)
async def chunk_gate(request: ChunkGateRequest):
    """Standalone chunk gate — validate a chunk without full generation pipeline."""
    try:
        runnables = handler._get_runnables(
            validation_model_id=request.validation_model_id,
        )
        if runnables.chunk_gate_chain is None:
            return ResponseModel(
                status="success",
                result=synthetic_gate_pass("gate_chain_not_configured"),
            )

        result = await asyncio.to_thread(
            runnables.chunk_gate_chain.invoke,
            {
                "chunk": request.chunk,
                "question_type": request.question_type,
                "document_kind": request.document_kind or "normative",
                "block_type": request.block_type,
            },
        )
        gate_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        return ResponseModel(status="success", result=gate_dict)
    except HTTPException:
        raise
    except GateParseError as e:
        # LLM ответил, но вердикта нет — это не отсев чанка, а сбой gate.
        logger.error(f"Chunk gate parse error: {e}")
        raise HTTPException(status_code=500, detail=GATE_PARSE_ERROR_DETAIL)
    except Exception as e:
        logger.error(f"Chunk gate error: {e}")
        logger.error(traceback.format_exc())
        http_exc = llm_error_to_http(e, gate=True)
        if http_exc is not None:
            raise http_exc
        # Нетранзиентная ошибка LLM (400/404 и т.п.): полный текст — только в лог.
        raise HTTPException(status_code=500, detail=GATE_LLM_ERROR_DETAIL)


@app.post("/validate_question/", response_model=ResponseModel)
async def validate_question(request: ValidateQuestionRequest):
    """Quality validator alone, for revalidating a stored question.

    Only the validator runs: the question is scored as it is and never
    refined. Infrastructure errors come back as 5xx with constant details so
    the worker records a technical error instead of a failed verdict.
    """
    try:
        question = stored_to_validator_question(request.question)
    except ValueError as exc:
        logger.warning(f"validate_question: unusable question: {exc}")
        raise HTTPException(status_code=422, detail=f"{VALIDATION_INPUT_ERROR_DETAIL}: {exc}")
    qtype = str(request.question.get("question_type")).strip().lower()
    model_id = request.validation_model_id
    if model_id and model_id != "default" and registry.get_model(model_id) is None:
        # Never fall back to the default model here: a revalidation run is
        # labelled with the chosen validator, so a silent swap would mislabel
        # every verdict. The worker records a technical error instead.
        raise HTTPException(status_code=400, detail=f"{UNKNOWN_VALIDATION_MODEL_DETAIL}: {model_id}")
    try:
        chain = handler.validation_chain_for(model_id)
        result = await asyncio.to_thread(
            chain.invoke,
            {
                "question_type": qtype,
                "source_text": request.source_text or "",
                "question": question,
                "document_kind": normalize_document_kind(request.document_kind),
            },
        )
        data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        data.pop("raw", None)
        return ResponseModel(status="success", result=data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"validate_question error: {e}")
        logger.error(traceback.format_exc())
        http_exc = llm_error_to_http(e, gate=False)
        if http_exc is not None:
            raise http_exc
        raise HTTPException(status_code=500, detail=VALIDATION_LLM_ERROR_DETAIL)


@app.get("/health/")
async def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8790)