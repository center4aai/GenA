from typing import Dict, List, Optional, TypedDict,Union
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

class FormatOutputInput(TypedDict):
    generated_question: str
    milvus_results: List[Dict[str, Union[str, int, float]]]
    sensitivity_score: int
    difficulty_score: int

class FormatOutputOutput(BaseModel):
    final_json: dict = Field(description="Финальный JSON с результатами")

def create_format_output_chain() -> Runnable[FormatOutputInput, FormatOutputOutput]:
    class FormatOutputRunnable(Runnable[FormatOutputInput, FormatOutputOutput]):
        def invoke(self, input_data: FormatOutputInput) -> FormatOutputOutput:
            return FormatOutputOutput(final_json={
                "question": input_data["generated_question"],
                "sensitivity": input_data["sensitivity_score"],
                "difficulty": input_data["difficulty_score"],
                "references": [
                    {
                        "id": res["id"],
                        "question": res["question"],
                        "score": res["score"]
                    } 
                    for res in input_data["milvus_results"]
                ]
            })
    
    return FormatOutputRunnable()