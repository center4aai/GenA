from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from contextlib import asynccontextmanager
from agent.runnables import create_GENA_runnables_ollama
from agent.handler import GENAHandler, GENAOptions
from config import MONGO_DB_PATH
import logging

# Настройка логгирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PromptRequest(BaseModel):
    prompt: str
    question_type: str
    source: str
    chat_id: int
    source_text: Optional[str] = None  
    additional_params: Optional[dict] = None

class RephraseQuestionsRequest(BaseModel):
    dataset_name: str
    questions: list

class ResponseModel(BaseModel):
    status: str
    result: dict | list
    error: Optional[str] = None

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

@app.post("/process_prompt/", response_model=ResponseModel)
async def process_prompt(request: PromptRequest):
    try:
        logger.info(f"Processing prompt for chat_id: {request.chat_id}")
        
        # Убеждаемся, что prompt - это строка (на случай если пришел словарь)
        prompt_text = request.prompt
        if isinstance(prompt_text, dict):
            # Если это словарь чанка, извлекаем текст
            prompt_text = prompt_text.get("fragment_data", {}).get("combined_text", str(prompt_text))
            logger.warning(f"Received dict instead of string for prompt, extracted text")
        
        source_text = request.source_text or prompt_text
        if isinstance(source_text, dict):
            source_text = source_text.get("fragment_data", {}).get("combined_text", str(source_text))
        
        # Проверяем additional_params - должен быть словарем
        additional_params = request.additional_params
        if additional_params is not None:
            if not isinstance(additional_params, dict):
                logger.warning(f"additional_params is not a dict, got {type(additional_params)}, ignoring")
                additional_params = None
        
        # ahandle_prompt не принимает **kwargs, поэтому игнорируем additional_params
        # Если нужно передать дополнительные параметры, их нужно добавить в сигнатуру функции
        logger.info(f"Calling ahandle_prompt with chat_id type: {type(request.chat_id)}, value: {request.chat_id}")
        try:
            result = handler.ahandle_prompt(
                prompt=prompt_text,
                question_type=request.question_type,
                source=request.source,
                chat_id=str(request.chat_id),  # Убеждаемся, что chat_id - строка
                source_text=source_text
            )
        except ValueError as ve:
            if "too many values to unpack" in str(ve):
                logger.error(f"ValueError with unpacking in ahandle_prompt: {str(ve)}")
                import traceback
                logger.error(f"Full traceback:\n{traceback.format_exc()}")
            raise
        except Exception as e:
            logger.error(f"Exception in ahandle_prompt: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            raise
        
        return ResponseModel(
            status="success",
            result=result
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error processing prompt: {error_msg}")
        import traceback
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=error_msg
        )

@app.post("/rephrase_questions/", response_model=ResponseModel)
async def rephrase_questions(request: RephraseQuestionsRequest):
    try:
        logger.info(f"Processing rephrase questions")
        
        rephrased_questions = handler.ahandle_rephrase_questions(
            questions = request.questions
        )

        return ResponseModel(
            status="success",
            result=rephrased_questions
        )

    except Exception as e:
        logger.error(f"Error processing prompt: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.get("/health/")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8787)