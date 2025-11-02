import time
import requests
import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional
from config import (
    TASK_QUEUE_API_URL, 
    AGENT_API_URL, 
    DATASET_API_URL,
    WORKER_POLL_INTERVAL,
    WORKER_BATCH_SIZE,
    WORKER_MAX_RETRIES,
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TaskWorker:
    def __init__(self):
        self.task_queue_url = TASK_QUEUE_API_URL
        self.agent_api_url = AGENT_API_URL
        self.dataset_api_url = DATASET_API_URL
        self.poll_interval = WORKER_POLL_INTERVAL
        self.batch_size = WORKER_BATCH_SIZE
        self.max_retries = WORKER_MAX_RETRIES
        
        self.dataset_progress = {}
        self.session = requests.Session()  

    def get_pending_tasks(self) -> List[Dict]:
        try:
            response = requests.get(
                f"{self.task_queue_url}/tasks/pending",
                params={"limit": self.batch_size}
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get pending tasks: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error getting pending tasks: {str(e)}")
            return []
    
    def update_task_status(self, task_id: str, status: str, result: Optional[Dict] = None, error: Optional[str] = None):
        try:
            payload = {"status": status}
            if result is not None:
                payload["result"] = result
            if error is not None:
                payload["error"] = error
                
            response = requests.put(
                f"{self.task_queue_url}/tasks/{task_id}/status",
                json=payload
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to update task status: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error updating task status: {str(e)}")
    
    def process_task(self, task: Dict) -> Optional[Dict]:
        task_id = task["_id"]
        chunk_text = task["chunk_text"]
        question_type = task["question_type"]
        dataset_id = task.get("dataset_id")
        
        logger.info(f"Processing task {task_id}: {question_type} question for chunk {task['chunk_id']} (dataset: {dataset_id})")
        
        self.update_task_status(task_id, "processing")
        
        try:
            payload = {
                "prompt": chunk_text,
                "question_type": question_type,
                "source": task["source_document"],
                "chat_id": task["chunk_id"],
                "source_text": chunk_text
            }
            
            response = requests.post(
                f"{self.agent_api_url}/process_prompt/",
                json=payload,
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Task {task_id} completed successfully")
                
                if dataset_id:
                    self.save_question_to_dataset(task, result)
                
                return result
            else:
                error_msg = f"Agent API error: {response.status_code} - {response.text}"
                logger.error(error_msg)
                self.update_task_status(task_id, "failed", error=error_msg)
                return None
                
        except requests.exceptions.Timeout:
            error_msg = "Request timeout"
            logger.error(f"Task {task_id} failed: {error_msg}")
            self.update_task_status(task_id, "failed", error=error_msg)
            return None
        except Exception as e:
            error_msg = f"Processing error: {str(e)}"
            logger.error(f"Task {task_id} failed: {error_msg}")
            self.update_task_status(task_id, "failed", error=error_msg)
            return None
    
    def save_question_to_dataset(self, task: Dict, result: Dict):
        try:
            dataset_id = task.get("dataset_id")
            if not dataset_id:
                logger.warning(f"No dataset_id for task {task['_id']}, skipping save")
                return
            
            output = result.get("result", {}).get("output", {})
            generated_question = output.get("generated_question", {})
            sensitivity_score = output.get("sensitivity_score", {})
            validation_result = output.get("validation_result", {})
            difficulty_score  = output.get("difficulty_score", {})
            
            options_dict = {}
            for i in range(1, 10):
                option_key = f"option_{i}"
                if option_key in generated_question and generated_question[option_key] not in [None, "None"]:
                    options_dict[option_key] = generated_question[option_key]
            
            question_data = {
                "chunk_id": task["chunk_id"],
                "question_type": task["question_type"],
                "task": generated_question.get("task", ""),
                "options": options_dict,
                "correct_answer": str(generated_question.get("outputs", "")),
                "provocativeness": str(sensitivity_score.get("provocativeness_score", "")),
                "difficulty": str(difficulty_score.get("difficulty", "")),
                "validation_passed": str(validation_result.get("passed", False)),
                "validation_score": f"{validation_result.get('total', 'N/A')}/{validation_result.get('max_total', 'N/A')}",
                "validation_threshold": str(validation_result.get("threshold", "N/A")),
                "validation_details": str(validation_result.get("by_block", {})),
                "source_chunk": task["chunk_text"]
            }
            
            response = self.session.post(
                f"{self.dataset_api_url}/datasets/{dataset_id}/add-question",
                json=question_data,
                timeout=10
            )
            
            if response.status_code == 200:
                save_result = response.json()
                logger.info(f"Question saved to dataset {dataset_id}, total questions: {save_result.get('total_questions')}")
                self.update_dataset_progress(dataset_id, task.get("dataset_name", "Unknown"))
            else:
                logger.error(f"Failed to save question to dataset {dataset_id}: {response.status_code} {response.text}")
                
        except Exception as e:
            logger.error(f"Error saving question to dataset: {str(e)}")
    
    def update_dataset_progress(self, dataset_id: str, dataset_name: str):
        if dataset_id not in self.dataset_progress:
            self.dataset_progress[dataset_id] = {
                "name": dataset_name,
                "processed_tasks": 0,
                "total_tasks": 0,
                "last_updated": datetime.now()
            }
        self.dataset_progress[dataset_id]["processed_tasks"] += 1
        self.dataset_progress[dataset_id]["last_updated"] = datetime.now()
        logger.info(f"Dataset {dataset_name} progress: {self.dataset_progress[dataset_id]['processed_tasks']} tasks processed")
    
    def get_dataset_tasks_count(self, dataset_id: str) -> int:
        try:
            response = requests.get(f"{self.task_queue_url}/tasks/pending")
            if response.status_code == 200:
                tasks = response.json()
                dataset_tasks = [t for t in tasks if t.get("dataset_id") == dataset_id]
                return len(dataset_tasks)
        except Exception as e:
            logger.error(f"Error getting dataset tasks count: {str(e)}")
        return 0
    
    def check_dataset_completion(self, dataset_id: str, dataset_name: str):
        try:
            response = self.session.get(f"{self.dataset_api_url}/datasets/{dataset_id}/tasks", timeout=10)
            if response.status_code == 200:
                dataset_tasks = response.json()
                completed_tasks = [t for t in dataset_tasks if t.get("status") == "completed"]
                failed_tasks = [t for t in dataset_tasks if t.get("status") == "failed"]
                pending_tasks = [t for t in dataset_tasks if t.get("status") == "pending"]
                
                total_tasks = len(dataset_tasks)
                completed_count = len(completed_tasks)
                failed_count = len(failed_tasks)
                pending_count = len(pending_tasks)
                
                logger.info(f"Dataset {dataset_name} status: {completed_count}/{total_tasks} completed, {failed_count} failed, {pending_count} pending")
                
                if pending_count == 0 and total_tasks > 0:
                    logger.info(f"Dataset {dataset_name} processing completed!")
                    self.finalize_dataset(dataset_id, dataset_name, completed_count, failed_count, total_tasks)
            else:
                logger.error(f"Failed to fetch dataset tasks: {response.status_code} {response.text}")
        except Exception as e:
            logger.error(f"Error checking dataset completion: {str(e)}")
    
    def finalize_dataset(self, dataset_id: str, dataset_name: str, completed_count: int, failed_count: int, total_tasks: int):
        try:
            response = self.session.get(f"{self.dataset_api_url}/datasets/{dataset_id}", timeout=10)
            if response.status_code == 200:
                dataset = response.json()
                metadata = dataset.get("metadata", {})
                metadata.update({
                    "status": "completed",
                    "completed_at": datetime.now().isoformat(),
                    "total_tasks": total_tasks,
                    "completed_tasks": completed_count,
                    "failed_tasks": failed_count,
                    "success_rate": f"{completed_count}/{total_tasks}" if total_tasks > 0 else "0/0"
                })
                
                update_response = self.session.put(
                    f"{self.dataset_api_url}/datasets/{dataset_id}",
                    json={"questions": dataset.get("questions", []), "metadata": metadata},
                    timeout=10
                )
                
                if update_response.status_code == 200:
                    logger.info(f"Dataset {dataset_name} finalized successfully")
                else:
                    logger.error(f"Failed to finalize dataset {dataset_name}: {update_response.status_code} {update_response.text}")
            else:
                logger.error(f"Failed to fetch dataset for finalization: {response.status_code} {response.text}")
        except Exception as e:
            logger.error(f"Error finalizing dataset: {str(e)}")
    
    def process_batch(self, tasks: List[Dict]):
        if not tasks:
            return
        
        logger.info(f"Processing batch of {len(tasks)} tasks")
        dataset_tasks = {}
        for task in tasks:
            dataset_id = task.get("dataset_id")
            if dataset_id:
                dataset_tasks.setdefault(dataset_id, []).append(task)
        
        for task in tasks:
            result = self.process_task(task)
            if result:
                task["result"] = result
                self.update_task_status(task["_id"], "completed", result=result)
            else:
                self.update_task_status(task["_id"], "failed")
        
        for dataset_id, dataset_task_list in dataset_tasks.items():
            dataset_name = dataset_task_list[0].get("dataset_name", "Unknown")
            self.check_dataset_completion(dataset_id, dataset_name)
    
    def run(self):
        logger.info("Starting Task Worker...")
        logger.info(f"Polling interval: {self.poll_interval} seconds")
        logger.info(f"Batch size: {self.batch_size}")
        logger.info(f"Task Queue API: {self.task_queue_url}")
        logger.info(f"Agent API: {self.agent_api_url}")
        logger.info(f"Dataset API: {self.dataset_api_url}")
        
        while True:
            try:
                pending_tasks = self.get_pending_tasks()
                if pending_tasks:
                    logger.info(f"Found {len(pending_tasks)} pending tasks")
                    self.process_batch(pending_tasks)
                else:
                    logger.info("No pending tasks found")
                    time.sleep(30)
                time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                logger.info("Worker stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {str(e)}")
                time.sleep(self.poll_interval)

if __name__ == "__main__":
    worker = TaskWorker()
    worker.run()