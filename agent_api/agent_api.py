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
        
        result = handler.ahandle_prompt(
            prompt=request.prompt,
            question_type=request.question_type,
            source=request.source,
            chat_id=request.chat_id,
            source_text=request.source_text or request.prompt,
            **(request.additional_params or {})
        )
        
        return ResponseModel(
            status="success",
            result=result
        )
    except Exception as e:
        logger.error(f"Error processing prompt: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
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