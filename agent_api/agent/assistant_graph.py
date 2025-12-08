from typing import TypedDict, Annotated, List, Literal
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.runnables import Runnable
from dataclasses import dataclass

from typing import Optional

# Типы состояний для графа
class AgentState(TypedDict):
    chunk: str
    question_type: str
    source: str
    generated_question: dict
    sensitivity_score: dict
    difficulty_score: dict
    validation_result: dict
    final_json: dict
    questions: Optional[List[str]] # поле для перефразированных вопросов


@dataclass
class GENAAssistant:
    generate_question_chain: Runnable
    provocativeness_chain: Runnable
    validation_chain: Runnable
    difficulty_chain: Runnable 
#    retriever_chain: Runnable
#    format_output_chain: Runnable
    checkpointer: Optional[BaseCheckpointSaver] = None

    def __post_init__(self):
        builder = StateGraph(AgentState)
        
        # Добавляем ноды
        builder.add_node("generate_question", self.generate_question_node)
        builder.add_node("provocativeness", self.provocativeness_node)
        builder.add_node("validation", self.validation_node)
        builder.add_node("difficulty", self.difficulty_node)
#        builder.add_node("search_milvus", self.search_milvus_node)
#        builder.add_node("format_output", self.format_output_node)
        
        # Определяем поток
        builder.set_entry_point("generate_question")
        builder.add_edge("generate_question", "provocativeness")
        builder.add_edge("provocativeness", "difficulty") 
        builder.add_edge("difficulty", "validation")
        builder.add_edge("validation", END)
#        builder.add_edge("search_milvus", "assess_sensitivity")
#        builder.add_edge("assess_sensitivity", "format_output")
        
        # Компилируем граф с чекпоинтером, если он указан
        if self.checkpointer is not None:
            self.graph = builder.compile(checkpointer=self.checkpointer)
        else:
            self.graph = builder.compile()

    def generate_question_node(self, state: AgentState) -> AgentState:
        """Генерация вопроса"""
        result = self.generate_question_chain.invoke({
            "input_text": state["chunk"],
            "question_type": state["question_type"]
        })
        return {
            **state, 
            "generated_question": result
        }


    def provocativeness_node(self, state: AgentState) -> AgentState:
        """Оценка чувствительности"""
        # Убеждаемся, что generated_question - это словарь
        generated_question = state["generated_question"]
        if not isinstance(generated_question, dict):
            if hasattr(generated_question, 'model_dump'):
                generated_question = generated_question.model_dump()
            elif hasattr(generated_question, 'dict'):
                generated_question = generated_question.dict()
        
        result = self.provocativeness_chain.invoke({
            "generated_question": generated_question
            })
        
        # Убеждаемся, что result - это объект ProvocativenessOutput
        if isinstance(result, dict):
            provocativeness_score = result.get('provocativeness_score', 0)
            explanation = result.get('explanation', '')
        else:
            provocativeness_score = result.provocativeness_score
            explanation = result.explanation
            
        return {
            **state, 
            "sensitivity_score": {'provocativeness_score': provocativeness_score, 'explanation': explanation}
        }
    def difficulty_node(self, state: AgentState) -> AgentState:
        """Оценка сложности вопроса"""
        # Убеждаемся, что generated_question - это словарь
        generated_question = state["generated_question"]
        if not isinstance(generated_question, dict):
            if hasattr(generated_question, 'model_dump'):
                generated_question = generated_question.model_dump()
            elif hasattr(generated_question, 'dict'):
                generated_question = generated_question.dict()
        
        result = self.difficulty_chain.invoke({
            "generated_question": generated_question
        })
        
        # Убеждаемся, что result - это объект DifficultyOutput
        if isinstance(result, dict):
            difficulty = result.get('difficulty', 0)
            explanation = result.get('explanation', '')
        else:
            difficulty = result.difficulty
            explanation = result.explanation
            
        return {
            **state,
            "difficulty_score": {
                "difficulty": difficulty,
                "explanation": explanation
            }
        }

    def validation_node(self, state: AgentState) -> AgentState:
        """Валидация качества вопроса"""
        # Убеждаемся, что generated_question - это словарь
        generated_question = state["generated_question"]
        if not isinstance(generated_question, dict):
            if hasattr(generated_question, 'model_dump'):
                generated_question = generated_question.model_dump()
            elif hasattr(generated_question, 'dict'):
                generated_question = generated_question.dict()
            else:
                raise ValueError(f"Unexpected type for generated_question: {type(generated_question)}")
        
        result = self.validation_chain.invoke({
            "question_type": state["question_type"],
            "source_text": generated_question.get("source_text", ""),
            "question": {k: v for k, v in generated_question.items() if k != "source_text"}
        })
        return {
            **state, 
            "validation_result": {
                "type": result.type,
                "by_block": result.by_block,
                "total": result.total,
                "max_total": result.max_total,
                "threshold": result.threshold,
                "passed": result.passed
            }
        }

    # def search_milvus_node(self, state: AgentState) -> AgentState:
    #     """Поиск в Milvus"""
    #     result = self.retriever_chain.invoke({
    #         "query": state["generated_question"]
    #     })
    #     return {
    #         **state, 
    #         "milvus_results": result.search_result
    #     }
        
    # def format_output_node(self, state: AgentState) -> AgentState:
    #     """Форматирование вывода"""
    #     result = self.format_output_chain.invoke({
    #         "generated_question": state["generated_question"],
    #         "milvus_results": state["milvus_results"],
    #         "sensitivity_score": state["sensitivity_score"]
    #     })
    #     return {
    #         **state, 
    #         "final_json": result.final_json
    #     }