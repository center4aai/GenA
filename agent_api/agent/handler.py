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
        
        # Временно отключаем чекпоинтер из-за проблем со старыми данными в MongoDB
        # TODO: Включить чекпоинтер после очистки старых данных или обновления формата
        use_checkpointer = False
        
        # Используем уникальный thread_id для каждого запроса, чтобы избежать конфликтов со старыми данными
        # Добавляем timestamp для уникальности
        import time
        import logging
        logger = logging.getLogger(__name__)
        unique_thread_id = f"{chat_id}_{int(time.time() * 1000)}"
        # config нужен только при использовании чекпоинтера
        config = {"configurable": {"thread_id": unique_thread_id}} if use_checkpointer else {}
        
        if use_checkpointer:
            # Инициализация MongoDB и чекпоинтера
            mongodb_client = MongoClient(self._options.mongodb_uri)
            # Используем MONGO_DB_NAME из конфига
            checkpointer = MongoDBSaver(mongodb_client, database_name=MONGO_DB_NAME)
            
            # Создание и запуск ассистента с чекпоинтером
            assistant = GENAAssistant(
                generate_question_chain=self._GENA_runnables.generate_question_chain,
                provocativeness_chain=self._GENA_runnables.provocativeness_chain,
                validation_chain=self._GENA_runnables.validation_chain,
                difficulty_chain=self._GENA_runnables.difficulty_chain,
                checkpointer=checkpointer,
            )
            
            try:
                output = assistant.graph.invoke(input_data, config=config)
            except Exception as e:
                if "too many values to unpack" in str(e) or "checkpoint" in str(e).lower():
                    # Если ошибка из-за старых данных в MongoDB, пробуем без чекпоинтера
                    logger.warning(f"Error with checkpoint for thread_id {unique_thread_id}, retrying without checkpoint: {str(e)}")
                    # Создаем новый ассистент без чекпоинтера
                    assistant_no_checkpoint = GENAAssistant(
                        generate_question_chain=self._GENA_runnables.generate_question_chain,
                        provocativeness_chain=self._GENA_runnables.provocativeness_chain,
                        validation_chain=self._GENA_runnables.validation_chain,
                        difficulty_chain=self._GENA_runnables.difficulty_chain,
                        checkpointer=None,  # Без чекпоинтера
                    )
                    output = assistant_no_checkpoint.graph.invoke(input_data, config=config)
                else:
                    raise
        else:
            # Создание и запуск ассистента без чекпоинтера (по умолчанию)
            assistant = GENAAssistant(
                generate_question_chain=self._GENA_runnables.generate_question_chain,
                provocativeness_chain=self._GENA_runnables.provocativeness_chain,
                validation_chain=self._GENA_runnables.validation_chain,
                difficulty_chain=self._GENA_runnables.difficulty_chain,
                checkpointer=None,  # Без чекпоинтера
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
        #checkpointer = MongoDBSaver(mongodb_client, database_name=MONGO_DB_NAME)
        
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
        
    