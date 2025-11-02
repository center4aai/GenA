import os
import gc
import logging
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from typing import List
from docx import Document
from chonkie import SemanticChunker, Visualizer, SentenceTransformerEmbeddings
from datetime import datetime
from tqdm import tqdm
import tempfile
import PyPDF2
import io

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from pymilvus import (
    connections, FieldSchema, CollectionSchema,
    DataType, Collection, utility
)

app = FastAPI(title="GenA Chunker Service", version="1.0.0")


def read_docx_text(path) -> str:
    logger.info(f"Reading DOCX file: {path}")
    doc = Document(path)
    full_text = []
    for para in doc.paragraphs:
        if para.text.startswith('ГАРАНТ'):
            continue
        full_text.append(para.text)
    text = "\n".join(full_text)
    logger.info(f"Extracted {len(text)} characters from DOCX file")
    return text


def read_txt_text(path) -> str:
    logger.info(f"Reading TXT file: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    logger.info(f"Extracted {len(text)} characters from TXT file")
    return text


def read_pdf_text(path) -> str:
    logger.info(f"Reading PDF file: {path}")
    text = ""
    with open(path, 'rb') as file:
        pdf_reader = PyPDF2.PdfReader(file)
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
    logger.info(f"Extracted {len(text)} characters from PDF file")
    return text


class SemanticChunkerService:
    
    def __init__(self, model_path='/app/models/multilingual-e5-large'):
        # Явно указываем использовать GPU
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        print(f"Using device: {device}")
        
        self.st_embeddings = SentenceTransformerEmbeddings(model_path, device=device)
        self.chunker = SemanticChunker(
            embedding_model=self.st_embeddings, 
            threshold=0.95,  #0.95              
            chunk_size=1024,                
            min_sentences=10,#10
            min_chunk_size=50
        )  
        
    def file_to_chunks(self, path_to_file, filename):
        logger.info(f"Processing file: {filename} at path: {path_to_file}")
        
        # Определяем тип файла и читаем текст
        file_extension = filename.lower().split('.')[-1]
        logger.info(f"File extension detected: {file_extension}")
        
        if file_extension == 'docx':
            text = read_docx_text(path_to_file)
        elif file_extension == 'txt':
            text = read_txt_text(path_to_file)
        elif file_extension == 'pdf':
            text = read_pdf_text(path_to_file)
        else:
            raise ValueError(f"Unsupported file type: {file_extension}")
        
        logger.info(f"Text extracted, length: {len(text)} characters")
        logger.info(f"Text preview (first 200 chars): {text[:200]}...")
        
        # Разбиваем на семантические чанки
        chunks = self.chunker.chunk(text)
        chunks_list = [chunk.text for chunk in chunks]
        
        # Фильтруем пустые чанки
        chunks_list = [chunk for chunk in chunks_list if chunk.strip()]
        
        logger.info(f"Created {len(chunks_list)} chunks")
        for i, chunk in enumerate(chunks_list[:3]):  # Показываем первые 3 чанка
            logger.info(f"Chunk {i+1} preview: {chunk[:100]}...")
        
        gc.collect()
        
        return chunks_list


# Глобальный экземпляр чанкера
chunker_inst = None

def get_chunker():
    global chunker_inst
    if chunker_inst is None:
        chunker_inst = SemanticChunkerService()
    return chunker_inst

# Получаем экземпляр семантического чанкера
chunker = None

def get_chunker_instance():
    global chunker
    if chunker is None:
        chunker = get_chunker()
    return chunker


@app.get("/")
async def root():
    return {"message": "GenA Chunker Service", "version": "1.0.0"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "chunker"}


@app.post("/chunk/")
async def chunk_file(file: UploadFile = File(...)):
    """
    Chunk uploaded file into smaller text pieces using semantic chunking.
    Supports .docx, .txt, and .pdf files.
    """
    logger.info(f"Received file upload request: {file.filename}, size: {file.size} bytes")
    
    # Проверяем расширение файла
    file_extension = file.filename.lower().split('.')[-1]
    logger.info(f"File extension: {file_extension}")
    
    if file_extension not in ['docx', 'txt', 'pdf']:
        logger.warning(f"Unsupported file type: {file_extension}")
        return JSONResponse(
            status_code=400, 
            content={"error": "Only .docx, .txt, and .pdf files are supported."}
        )
    
    # Создаем временный файл
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as tmp:
            content = await file.read()
            logger.info(f"Read {len(content)} bytes from uploaded file")
            tmp.write(content)
            tmp_path = tmp.name
            logger.info(f"Created temporary file: {tmp_path}")
        
        # Обрабатываем файл
        chunker_instance = get_chunker_instance()
        chunks = chunker_instance.file_to_chunks(tmp_path, file.filename)
        
        logger.info(f"Successfully processed file, created {len(chunks)} chunks")
        
        return {
            "filename": file.filename,
            "file_type": file_extension,
            "num_chunks": len(chunks),
            "chunks": chunks,
            "chunking_method": "semantic",
            "model": "multilingual-e5-large",
            "threshold": 0.85,
            "chunk_size": 512
        }
        
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Error processing file: {str(e)}"}
        )
    finally:
        # Удаляем временный файл
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
            logger.info(f"Removed temporary file: {tmp_path}")


@app.post("/chunk-docx/")
async def chunk_docx(file: UploadFile = File(...)):
    """
    Legacy endpoint for DOCX files only.
    """
    if not file.filename.lower().endswith(".docx"):
        return JSONResponse(
            status_code=400, 
            content={"error": "Only .docx files are supported by this endpoint."}
        )
    
    return await chunk_file(file)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8517)
