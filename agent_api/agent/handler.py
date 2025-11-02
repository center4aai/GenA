from typing import Tuple, List
from langgraph.checkpoint.mongodb import MongoDBSaver
from pydantic import BaseModel
from pymongo import MongoClient
from requests.auth import _basic_auth_str
from config import MAX_LEN_USER_PROMPT
from agent.assistant_graph import GENAAssistant
from agent.base import BaseHandler
from agent.runnables import create_GENA_runnables_ollama
from typing import Optional, Dict, Any 


class GENAOptions(BaseModel):
    """Конфигурационные параметры для GENAHandler"""
    mongodb_uri: str


class GENAHandler(BaseHandler):
    """Обработчик запросов для GENA ассистента"""

    def __init__(self, options: GENAOptions) -> None:
        """
        Инициализация обработчика
        
        Args:
            options: Конфигурационные параметры
        """
        self._options = options
        self._init_runnables()
        
    def _init_runnables(self) -> None:
        """Инициализация цепочек выполнения"""
        self._GENA_runnables = create_GENA_runnables_ollama()


    def ahandle_prompt(
        self, 
        prompt: str,
        question_type: str,
        source: str,
        chat_id: str,
        source_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_text = source_text or prompt
        """
        Асинхронная обработка пользовательского запроса
        
        Args:
            prompt: Текст запроса пользователя
            chat_id: Идентификатор чата
            
        Returns:
            Кортеж из (ответ, значение) для пользователя
        """

        # Подготовка входных данных
        input_data = {"chunk": prompt, "question_type": question_type, "source": source,  "source_text": source_text, }
        config = {"configurable": {"thread_id": chat_id}}
    
        # Инициализация MongoDB и чекпоинтера
        mongodb_client = MongoClient(self._options.mongodb_uri)
        # Используем gena_db для всех данных проекта
        checkpointer = MongoDBSaver(mongodb_client, database_name="gena_db")
        
        # Создание и запуск ассистента
        assistant = GENAAssistant(
            generate_question_chain=self._GENA_runnables.generate_question_chain,
            provocativeness_chain=self._GENA_runnables.provocativeness_chain,
            validation_chain=self._GENA_runnables.validation_chain,
            difficulty_chain=self._GENA_runnables.difficulty_chain,
            checkpointer=checkpointer,
        )
        output = assistant.graph.invoke(input_data, config=config)
        return {'output': output}

    def ahandle_rephrase_questions(self, questions: List[str]) -> List[str]:
        """
        Перефразирует список вопросов с помощью ассистента
        
        Args:
            questions: Список строк-вопросов
            
        Returns:
            Список переформулированных вопросов
        """
        if not questions:
            return []

        #mongodb_client = MongoClient(self._options.mongodb_uri)
        #checkpointer = MongoDBSaver(mongodb_client, database_name="gena_db")
        
        from agent.nodes.dynamic_implementation.rephrase_question import rephrase_questions

        # Извлекаем тексты вопросов, сохраняя ссылки на исходные словари
        question_texts = [q.get('task', '') for q in questions]

        # Получаем перефразированные версии
        rephrased_texts = rephrase_questions(question_texts)

        # Обновляем поле 'task' в исходных словарях
        for q, rephrased in zip(questions, rephrased_texts):
            q['task'] = rephrased

        # Возвращаем обновлённую структуру
        return questions
        
    