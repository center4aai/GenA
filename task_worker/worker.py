import time
import threading
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
    WORKER_AGENT_TIMEOUT,
    WORKER_AGENT_MAX_RETRIES,
    WORKER_STUCK_THRESHOLD_MINUTES,
    WORKER_RECOVERY_INTERVAL_SECONDS,
    WORKER_HEARTBEAT_SECONDS,
    WORKER_MAX_TASK_ATTEMPTS,
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# #region agent log
_DBG_PATH = '/tmp/debug-15e35f.log'
def _dbg(loc, msg, data=None, hyp=None):
    try:
        with open(_DBG_PATH, 'a') as f:
            f.write(json.dumps({"sessionId":"15e35f","location":loc,"message":msg,"data":data or {},"hypothesisId":hyp,"timestamp":int(time.time()*1000)}) + '\n')
    except: pass
# #endregion

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
    
    def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        attempts: Optional[int] = None,
    ):
        try:
            payload = {"status": status}
            if result is not None:
                payload["result"] = result
            if error is not None:
                payload["error"] = error
            if attempts is not None:
                payload["attempts"] = attempts

            response = requests.put(
                f"{self.task_queue_url}/tasks/{task_id}/status",
                json=payload
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to update task status: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error updating task status: {str(e)}")

    def _start_heartbeat(self, task_id: str) -> threading.Event:
        """Start a daemon thread that periodically refreshes ``updated_at`` for
        ``task_id`` so the recovery loop does not consider it stuck while it is
        legitimately being processed.  Returns the stop-event the caller must
        set in a ``finally`` block."""
        stop_event = threading.Event()

        def _beat():
            while not stop_event.wait(WORKER_HEARTBEAT_SECONDS):
                try:
                    self.update_task_status(task_id, "processing")
                except Exception as exc:
                    logger.warning(f"Heartbeat failed for task {task_id}: {exc}")

        t = threading.Thread(target=_beat, name=f"hb-{task_id}", daemon=True)
        t.start()
        return stop_event
    
    def process_task(self, task: Dict) -> Optional[Dict]:
        task_id = task["_id"]
        chunk_text = task.get("chunk_text", "")
        question_type = task["question_type"]
        dataset_id = task.get("dataset_id")
        chunk_id = task.get("chunk_id", "unknown")
        
        # Проверяем, что chunk_text не пустой
        if not chunk_text or len(chunk_text.strip()) < 10:
            error_msg = f"Chunk {chunk_id} is empty or too short (length: {len(chunk_text) if chunk_text else 0})"
            logger.warning(f"Task {task_id} skipped: {error_msg}")
            self.update_task_status(task_id, "failed", error=error_msg)
            return None
        
        logger.info(f"Processing task {task_id}: {question_type} question for chunk {chunk_id} (dataset: {dataset_id}, text length: {len(chunk_text)})")
        
        self.update_task_status(task_id, "processing")

        # Keep ``updated_at`` fresh while the agent call is in flight so that
        # recover_stuck_tasks() does not steal a task that is actually working.
        heartbeat_stop = self._start_heartbeat(task_id)

        try:
            payload = {
                "prompt": chunk_text,
                "question_type": question_type,
                "source": task.get("source_document", "unknown"),
                "chat_id": str(chunk_id),
                "source_text": chunk_text,
            }
            if task.get("generation_model_id"):
                payload["generation_model_id"] = task["generation_model_id"]
            if task.get("validation_model_id"):
                payload["validation_model_id"] = task["validation_model_id"]
            if task.get("chunk_pre_validated"):
                payload["chunk_pre_validated"] = True
            if task.get("pipeline_mode"):
                payload["pipeline_mode"] = task["pipeline_mode"]
            
            response = None
            _max_attempts = 1 + WORKER_AGENT_MAX_RETRIES
            for attempt in range(_max_attempts):
                try:
                    response = requests.post(
                        f"{self.agent_api_url}/process_prompt/",
                        json=payload,
                        timeout=WORKER_AGENT_TIMEOUT,
                    )
                    break
                except requests.exceptions.Timeout:
                    if attempt < WORKER_AGENT_MAX_RETRIES:
                        logger.warning(
                            f"Task {task_id} agent request timeout "
                            f"(attempt {attempt + 1}/{_max_attempts}), retrying..."
                        )
                        continue
                    error_msg = (
                        f"Request timeout after {WORKER_AGENT_TIMEOUT} seconds "
                        f"(chunk length: {len(chunk_text)} chars)"
                    )
                    logger.error(f"Task {task_id} (chunk {chunk_id}) failed: {error_msg}")
                    self.update_task_status(task_id, "failed", error=error_msg)
                    return None

            if response is None:
                return None

            if response.status_code == 200:
                result = response.json()
                logger.info(f"Task {task_id} completed successfully for chunk {chunk_id}")
                
                if dataset_id:
                    self.save_question_to_dataset(task, result)
                
                return result
            else:
                error_msg = f"Agent API error: {response.status_code} - {response.text[:500]}"
                logger.error(f"Task {task_id} (chunk {chunk_id}) failed: {error_msg}")
                logger.debug(f"Chunk text length: {len(chunk_text)}, first 100 chars: {chunk_text[:100]}")
                self.update_task_status(task_id, "failed", error=error_msg)
                return None
                
        except Exception as e:
            error_msg = f"Processing error: {str(e)}"
            logger.error(f"Task {task_id} failed: {error_msg}")
            self.update_task_status(task_id, "failed", error=error_msg)
            return None
        finally:
            heartbeat_stop.set()
    
    def save_question_to_dataset(self, task: Dict, result: Dict):
        try:
            dataset_id = task.get("dataset_id")
            if not dataset_id:
                logger.warning(f"No dataset_id for task {task['_id']}, skipping save")
                return
            
            output = result.get("result", {}).get("output", {})

            if output.get("chunk_rejected"):
                gate = output.get("chunk_gate_result") or {}
                logger.info(
                    f"Chunk {task.get('chunk_id')} rejected by gate: "
                    f"{gate.get('rejection_reason', 'unknown')}"
                )
                return

            generated_question = output.get("generated_question") or {}
            sensitivity_score = output.get("sensitivity_score") or {}
            validation_result = output.get("validation_result") or {}
            difficulty_score  = output.get("difficulty_score") or {}
            
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
                "validation_justifications": str(validation_result.get("justifications", {})),
                "retry_count": str(output.get("retry_count", 0)),
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
                processing_tasks = [t for t in dataset_tasks if t.get("status") == "processing"]
                
                total_tasks = len(dataset_tasks)
                completed_count = len(completed_tasks)
                failed_count = len(failed_tasks)
                pending_count = len(pending_tasks)
                processing_count = len(processing_tasks)
                
                logger.info(f"Dataset {dataset_name} status: {completed_count}/{total_tasks} completed, {failed_count} failed, {pending_count} pending, {processing_count} processing")
                
                # #region agent log
                _dbg("worker.py:check_dataset_completion", "completion check", {"dataset": dataset_name, "pending": pending_count, "processing": processing_count, "completed": completed_count, "total": total_tasks}, "H2")
                # #endregion
                
                if pending_count == 0 and processing_count == 0 and total_tasks > 0:
                    logger.info(f"Dataset {dataset_name} processing completed!")
                    self.finalize_dataset(dataset_id, dataset_name, completed_count, failed_count, total_tasks)
            else:
                logger.error(f"Failed to fetch dataset tasks: {response.status_code} {response.text}")
        except Exception as e:
            logger.error(f"Error checking dataset completion: {str(e)}")
    
    def finalize_dataset(self, dataset_id: str, dataset_name: str, completed_count: int, failed_count: int, total_tasks: int):
        try:
            metadata_patch = {
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
                "total_tasks": total_tasks,
                "completed_tasks": completed_count,
                "failed_tasks": failed_count,
                "success_rate": f"{completed_count}/{total_tasks}" if total_tasks > 0 else "0/0"
            }

            response = self.session.patch(
                f"{self.dataset_api_url}/datasets/{dataset_id}/metadata",
                json=metadata_patch,
                timeout=10,
            )

            if response.status_code == 200:
                logger.info(f"Dataset {dataset_name} finalized successfully")
            else:
                logger.error(f"Failed to finalize dataset {dataset_name}: {response.status_code} {response.text}")
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
            # Статус уже обновлен в process_task при ошибке, не нужно обновлять повторно
        
        # Проверяем завершение датасетов только после обработки всех задач в батче
        # Используем небольшую задержку, чтобы дать время обновиться статусам в БД
        import time
        time.sleep(1)
        
        for dataset_id, dataset_task_list in dataset_tasks.items():
            dataset_name = dataset_task_list[0].get("dataset_name", "Unknown")
            self.check_dataset_completion(dataset_id, dataset_name)
    
    def recover_stuck_tasks(self):
        """Reset tasks stuck in 'processing' back to 'pending' (from previous worker crash).

        A per-task ``attempts`` counter is incremented on every recovery; once
        it reaches ``WORKER_MAX_TASK_ATTEMPTS`` the task is force-failed so a
        poison task cannot bounce between ``processing`` and ``pending``
        forever.
        """
        try:
            response = self.session.get(
                f"{self.task_queue_url}/tasks/stuck",
                params={"threshold_minutes": WORKER_STUCK_THRESHOLD_MINUTES},
                timeout=10,
            )
            if response.status_code == 200:
                stuck_tasks = response.json()
                # #region agent log
                _dbg("worker.py:recover_stuck_tasks", "stuck tasks query result", {"count": len(stuck_tasks)}, "H1")
                # #endregion
                requeued = 0
                force_failed = 0
                for task in stuck_tasks:
                    task_id = task["_id"]
                    attempts = int(task.get("attempts") or 0) + 1
                    if attempts >= WORKER_MAX_TASK_ATTEMPTS:
                        error_msg = (
                            f"Force-failed after {attempts} stuck recoveries "
                            f"(>= WORKER_MAX_TASK_ATTEMPTS={WORKER_MAX_TASK_ATTEMPTS})"
                        )
                        self.update_task_status(
                            task_id, "failed", error=error_msg, attempts=attempts
                        )
                        force_failed += 1
                        logger.warning(
                            f"Force-failed stuck task {task_id} after {attempts} attempts"
                        )
                    else:
                        self.update_task_status(task_id, "pending", attempts=attempts)
                        requeued += 1
                        logger.info(
                            f"Reset stuck task {task_id} from 'processing' to "
                            f"'pending' (attempt {attempts}/{WORKER_MAX_TASK_ATTEMPTS})"
                        )
                if stuck_tasks:
                    logger.info(
                        f"Recovered {len(stuck_tasks)} stuck tasks "
                        f"(requeued={requeued}, force_failed={force_failed})"
                    )
            elif response.status_code == 404:
                logger.debug("No /tasks/stuck endpoint, skipping recovery")
            else:
                logger.warning(f"Failed to get stuck tasks: {response.status_code}")
        except Exception as e:
            logger.warning(f"Stuck task recovery skipped: {str(e)}")

    def reconcile_dataset_status(self):
        """Ask the dataset API to finalize datasets whose tasks are all terminal
        but which are still marked non-terminal (frozen at 'processing').

        This is the periodic safety net for the event-driven finalization in
        ``check_dataset_completion`` which can be missed on worker restarts or
        update races."""
        try:
            response = self.session.post(
                f"{self.dataset_api_url}/datasets/reconcile-status",
                timeout=30,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("finalized"):
                    logger.info(
                        f"Reconciled dataset status: finalized {data['finalized']} "
                        f"of {data.get('scanned')} stuck datasets"
                    )
            elif response.status_code == 404:
                logger.debug("No /datasets/reconcile-status endpoint, skipping")
            else:
                logger.warning(f"Dataset reconcile failed: {response.status_code}")
        except Exception as e:
            logger.warning(f"Dataset reconcile skipped: {str(e)}")

    def run(self):
        logger.info("Starting Task Worker...")
        logger.info(f"Polling interval: {self.poll_interval} seconds")
        logger.info(f"Batch size: {self.batch_size}")
        logger.info(f"Task Queue API: {self.task_queue_url}")
        logger.info(f"Agent API: {self.agent_api_url} (using endpoint: {self.agent_api_url}/process_prompt/)")
        logger.info(f"Dataset API: {self.dataset_api_url}")
        
        self.recover_stuck_tasks()
        self.reconcile_dataset_status()
        last_recovery_at = time.monotonic()

        while True:
            try:
                if time.monotonic() - last_recovery_at >= WORKER_RECOVERY_INTERVAL_SECONDS:
                    self.recover_stuck_tasks()
                    self.reconcile_dataset_status()
                    last_recovery_at = time.monotonic()

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