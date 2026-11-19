import copy
import time
import threading
import requests
import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

_CONFIG_PATH = Path(__file__).resolve().parent / "config.py"
_config_spec = importlib.util.spec_from_file_location("task_worker_config", _CONFIG_PATH)
if _config_spec is None or _config_spec.loader is None:
    raise ImportError(f"Cannot load {_CONFIG_PATH}")
_config = importlib.util.module_from_spec(_config_spec)
_config_spec.loader.exec_module(_config)

TASK_QUEUE_API_URL = _config.TASK_QUEUE_API_URL
AGENT_API_URL = _config.AGENT_API_URL
DATASET_API_URL = _config.DATASET_API_URL
CHUNKER_URL = _config.CHUNKER_URL
WORKER_CHUNK_TIMEOUT = _config.WORKER_CHUNK_TIMEOUT
WORKER_CHUNK_TIMEOUT_LLM = getattr(_config, "WORKER_CHUNK_TIMEOUT_LLM", 7200)
WORKER_API_KEY = _config.WORKER_API_KEY
WORKER_POLL_INTERVAL = _config.WORKER_POLL_INTERVAL
WORKER_BATCH_SIZE = _config.WORKER_BATCH_SIZE
WORKER_MAX_RETRIES = _config.WORKER_MAX_RETRIES
WORKER_AGENT_TIMEOUT = _config.WORKER_AGENT_TIMEOUT
WORKER_AGENT_MAX_RETRIES = _config.WORKER_AGENT_MAX_RETRIES
WORKER_STUCK_THRESHOLD_MINUTES = _config.WORKER_STUCK_THRESHOLD_MINUTES
WORKER_RECOVERY_INTERVAL_SECONDS = _config.WORKER_RECOVERY_INTERVAL_SECONDS
WORKER_HEARTBEAT_SECONDS = _config.WORKER_HEARTBEAT_SECONDS
WORKER_MAX_TASK_ATTEMPTS = _config.WORKER_MAX_TASK_ATTEMPTS
WORKER_REVALIDATION_CONCURRENCY = _config.WORKER_REVALIDATION_CONCURRENCY
DATASET_API_USER = _config.DATASET_API_USER
DATASET_API_PASS = _config.DATASET_API_PASS
WORKER_TOKEN_TTL = _config.WORKER_TOKEN_TTL
WORKER_RETRY_BACKOFF_BASE = _config.WORKER_RETRY_BACKOFF_BASE
WORKER_RETRY_BACKOFF_FACTOR = _config.WORKER_RETRY_BACKOFF_FACTOR
WORKER_RETRY_BACKOFF_MAX = _config.WORKER_RETRY_BACKOFF_MAX

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 30

# HTTP-статус в тексте ошибки: только как отдельное число после начала строки,
# пробела, «:» или «(» — как его форматирует _format_http_error. Голое
# ``"401" in text`` ловило любое число («Chunk 401 is empty…», «length: 14012»).
_HTTP_401_RE = re.compile(r"(?:^|[\s:(])401\b")
_HTTP_429_RE = re.compile(r"(?:^|[\s:(])429\b")

# Приоритеты этапов пайплайна: задачи забираются sort=[(priority, -1), (created_at, 1)],
# поэтому гейт должен стоять выше генерации. Иначе первые же пропущенные чанки
# порождают задачи генерации, которые вытесняют оставшиеся гейт-задачи, и этап
# «Отбор фрагментов» вместе со статистикой отсева тянется до конца прогона.
TASK_PRIORITY_CHUNK = 9
TASK_PRIORITY_GATE = 5
TASK_PRIORITY_GENERATION = 1


class TaskWorker:
    def __init__(self):
        self.task_queue_url = TASK_QUEUE_API_URL
        self.agent_api_url = AGENT_API_URL
        self.dataset_api_url = DATASET_API_URL
        self.chunker_url = CHUNKER_URL
        self.poll_interval = WORKER_POLL_INTERVAL
        self.batch_size = WORKER_BATCH_SIZE
        self.max_retries = WORKER_MAX_RETRIES
        self.worker_id = os.getenv("WORKER_ID", f"worker-{uuid.uuid4().hex[:8]}")
        self.request_id = str(uuid.uuid4())
        # Запрос graceful-остановки (ставится обработчиком SIGTERM/SIGINT в run()).
        self.stop = threading.Event()

        self.dataset_progress = {}
        self.session = requests.Session()
        if WORKER_API_KEY:
            self.session.headers.update({"X-Worker-Key": WORKER_API_KEY})
        self._dataset_token: Optional[str] = None
        self._dataset_token_expires_at: float = 0.0

    @staticmethod
    def _format_http_error(response: requests.Response, prefix: str = "") -> str:
        detail: object = None
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = body.get("detail", body.get("error", body.get("message")))
        except (ValueError, json.JSONDecodeError):
            pass
        if detail is None:
            text = (response.text or "").strip()
            detail = text[:500] if text else response.reason
        if isinstance(detail, (list, dict)):
            detail = json.dumps(detail, ensure_ascii=False)
        detail_str = str(detail)
        # Drop the raw model completion dump that follows this marker —
        # it is long, unreadable in the UI, and not useful to the user.
        marker = "from completion"
        idx = detail_str.lower().find(marker)
        if idx >= 0:
            detail_str = detail_str[: idx + len(marker)].rstrip()
        msg = f"{prefix}{response.status_code}: {detail_str}"
        return msg[:500]

    @staticmethod
    def _classify_error(raw: Optional[str]) -> str:
        """Map a raw error string to a stable message_code for the UI."""
        if not raw:
            return "internal_error"
        text = raw.lower()
        # Точные строки воркера — раньше эвристик по HTTP-кодам, чтобы число в
        # тексте («Chunk 401 is empty…») не перехватывалось как статус.
        if "empty or too short" in text:
            return "chunk_too_short"
        if "force-failed after" in text or "stuck recover" in text:
            return "task_stuck"
        # Константные detail agent_api (request_context.py): /process_prompt/ и
        # /rephrase_questions/ отвечают "llm auth failed", /chunk_gate/ —
        # "gate llm auth failed"; и то и другое — ошибка авторизации модели.
        if "llm auth failed" in text:
            return "model_auth_failed"
        if "gate parse error" in text:
            return "model_output_invalid"
        if "gate llm error" in text:
            return "model_error"
        if _HTTP_401_RE.search(text) or "unauthorized" in text or "authentication" in text:
            # JWT/worker-key 401s are not model auth — those carry a prefix.
            if any(
                p in text
                for p in ("fetch document", "save chunks", "enqueue", "gate ", "chunker")
            ):
                pass
            else:
                return "model_auth_failed"
        if _HTTP_429_RE.search(text) or "rate limit" in text or "quota" in text:
            return "model_unavailable"
        if "context length" in text or "maximum context" in text or "too many tokens" in text:
            return "model_output_invalid"
        if "failed to parse structuredquestionoutput" in text or (
            "failed to parse" in text and "from completion" in text
        ):
            return "model_output_invalid"
        if "failed to save question" in text:
            return "question_save_failed"
        if "fetch document failed" in text:
            return "document_fetch_failed"
        # Константный detail чанкера (ChunkingMethodUnavailable): метод не поддержан
        # для вида документа или не сконфигурирован — до общей ветки chunker_failed.
        if "chunking method" in text and (
            "not configured" in text or "not supported" in text or "unknown chunking method" in text
        ):
            return "chunker_method_unavailable"
        if "chunker error" in text or "chunker request failed" in text:
            return "chunker_failed"
        if "no usable chunks" in text:
            return "no_usable_chunks"
        if "save chunks failed" in text:
            return "chunks_save_failed"
        if "enqueue" in text and "failed" in text:
            return "enqueue_failed"
        # Различимые причины отказа gate-сервиса: сначала специфичные, потом общая.
        if ("gate api error" in text or "gate request failed" in text) and (
            "timeout" in text or "timed out" in text
        ):
            return "gate_timeout"
        if "gate llm busy" in text:
            return "gate_overloaded"
        if "gate api error" in text or "gate request failed" in text or "gate processing error" in text:
            return "gate_unavailable"
        # Константные detail генерации — после gate-веток: "gate llm busy"
        # содержит "llm busy".
        if "llm timeout" in text or "llm unreachable" in text or "llm busy" in text:
            return "model_unavailable"
        if (
            "agent request failed" in text
            or "agent request timed out" in text
            or "timeout" in text
            or "timed out" in text
            or "connection" in text
        ):
            return "model_unavailable"
        if "agent api error" in text:
            return "model_error"
        if "chunk task missing dataset_id" in text or "chunk processing error" in text:
            return "internal_error"
        if "processing error" in text:
            return "internal_error"
        return "internal_error"

    @staticmethod
    def _gate_message_code(
        passed: bool,
        reason: Optional[str],
        question_types: Optional[List[str]] = None,
        types_ok: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Stable message_code for a finished gate task."""
        if not passed:
            key = (reason or "unknown").strip() or "unknown"
            return f"gate_rejected_{key}"
        # Passed overall, but multi was requested and dropped by c3.
        qtypes = question_types or []
        ok = types_ok or []
        if "multi" in qtypes and "multi" not in ok:
            return "gate_passed_chunk_not_suitable_for_multi"
        return None

    @staticmethod
    def _filename_from_disposition(content_disposition: str) -> Optional[str]:
        """Extract the filename, preferring the RFC 5987 `filename*` form.

        Non-ASCII names (e.g. Cyrillic) only survive in `filename*`; the plain
        `filename` is an ASCII fallback.
        """
        if not content_disposition:
            return None
        m = re.search(r"filename\*=(?:UTF-8|utf-8)''([^;]+)", content_disposition)
        if m:
            try:
                return unquote(m.group(1).strip())
            except Exception:
                pass
        m = re.search(r'filename="?([^";]+)"?', content_disposition)
        return m.group(1).strip() if m else None

    def _ensure_dataset_auth(self, headers: Dict[str, str]) -> Dict[str, str]:
        if not DATASET_API_USER or not DATASET_API_PASS:
            return headers
        now = time.monotonic()
        if self._dataset_token and now < self._dataset_token_expires_at:
            headers["Authorization"] = f"Bearer {self._dataset_token}"
            return headers
        response = self.session.request(
            "POST",
            f"{self.dataset_api_url}/auth/login",
            json={"username": DATASET_API_USER, "password": DATASET_API_PASS},
            headers={"X-Request-ID": self.request_id},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code == 200:
            token = response.json().get("access_token")
            if token:
                self._dataset_token = token
                self._dataset_token_expires_at = now + max(WORKER_TOKEN_TTL - 60, 60)
                headers["Authorization"] = f"Bearer {self._dataset_token}"
        return headers

    @staticmethod
    def _backoff_seconds(attempt: int, retry_after: Optional[object] = None) -> float:
        """Пауза перед повтором после попытки №attempt (с нуля): 1, 4, 16 … с,
        не больше WORKER_RETRY_BACKOFF_MAX. Числовой Retry-After сервера имеет
        приоритет (HTTP-дату не разбираем — берём экспоненту)."""
        if isinstance(retry_after, (str, int, float)) and not isinstance(retry_after, bool):
            try:
                value = float(str(retry_after).strip())
            except ValueError:
                value = None
            if value is not None and value >= 0:
                return min(value, WORKER_RETRY_BACKOFF_MAX)
        delay = WORKER_RETRY_BACKOFF_BASE * (WORKER_RETRY_BACKOFF_FACTOR ** max(int(attempt), 0))
        return min(delay, WORKER_RETRY_BACKOFF_MAX)

    @staticmethod
    def _retry_after(response: Optional[requests.Response]) -> Optional[object]:
        """Заголовок Retry-After ответа, если он есть (иначе None)."""
        if response is None:
            return None
        headers = getattr(response, "headers", None)
        if not isinstance(headers, Mapping):
            return None
        return headers.get("Retry-After")

    def _request(
        self,
        method: str,
        url: str,
        *,
        retries: Optional[int] = None,
        dataset_auth: bool = False,
        **kwargs,
    ) -> Optional[requests.Response]:
        if retries is None:
            retries = self.max_retries
        kwargs.setdefault("timeout", HTTP_TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        if isinstance(headers, dict):
            headers = dict(headers)
        headers.setdefault("X-Request-ID", self.request_id)
        if dataset_auth:
            headers = self._ensure_dataset_auth(headers)
        kwargs["headers"] = headers
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                resp = self.session.request(method, url, **kwargs)
                if dataset_auth and resp.status_code == 401 and attempt < retries - 1:
                    logger.warning(f"Dataset auth expired for {url}, refreshing token...")
                    self._dataset_token = None
                    self._dataset_token_expires_at = 0.0
                    headers = dict(kwargs["headers"])
                    headers = self._ensure_dataset_auth(headers)
                    kwargs["headers"] = headers
                    time.sleep(0.5)
                    continue
                if resp.status_code >= 500 and attempt < retries - 1:
                    delay = self._backoff_seconds(attempt, self._retry_after(resp))
                    logger.warning(
                        f"HTTP {resp.status_code} from {url} (attempt {attempt + 1}/{retries}), "
                        f"retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                return resp
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                if attempt < retries - 1:
                    delay = self._backoff_seconds(attempt)
                    logger.warning(
                        f"Request error {url} (attempt {attempt + 1}/{retries}), "
                        f"retrying in {delay}s: {exc}"
                    )
                    time.sleep(delay)
                    continue
                logger.error(f"Request failed after {retries} attempts: {url}: {exc}")
        if last_exc:
            raise last_exc
        return None

    def claim_tasks(self) -> List[Dict]:
        try:
            # retries=1: claim не идемпотентен — успешный на сервере claim с
            # потерянным ответом при повторе забирал бы лишние задачи в processing.
            response = self._request(
                "POST",
                f"{self.task_queue_url}/tasks/claim",
                json={"worker_id": self.worker_id, "limit": self.batch_size},
                retries=1,
            )
            if response and response.status_code == 200:
                return response.json()
            if response:
                logger.error(f"Failed to claim tasks: {response.status_code}")
            return []
        except Exception as e:
            logger.error(f"Error claiming tasks: {str(e)}")
            return []

    def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        attempts: Optional[int] = None,
        expected_status: Optional[str] = None,
        message_code: Optional[str] = None,
        gate_passed: Optional[bool] = None,
        check_owner: bool = True,
    ) -> Optional[bool]:
        try:
            payload: Dict = {"status": status}
            # worker_id попадает в CAS-фильтр сервера: защита от поздних
            # перезаписей чужой задачи. Recovery чужих/старых задач
            # (check_owner=False) владельца не проверяет.
            if check_owner:
                payload["worker_id"] = self.worker_id
            if result is not None:
                payload["result"] = result
            if error is not None:
                payload["error"] = error
            if attempts is not None:
                payload["attempts"] = attempts
            if expected_status is not None:
                payload["expected_status"] = expected_status
            if message_code is not None:
                payload["message_code"] = message_code
            if gate_passed is not None:
                payload["gate_passed"] = gate_passed

            response = self._request(
                "PUT",
                f"{self.task_queue_url}/tasks/{task_id}/status",
                json=payload,
            )
            if response is None:
                return None
            if response.status_code == 409:
                logger.warning(f"CAS conflict updating task {task_id} to {status}")
                return False
            if response.status_code != 200:
                logger.error(f"Failed to update task status: {response.status_code}")
                return None
            return True
        except Exception as e:
            logger.error(f"Error updating task status: {str(e)}")
            return None

    def _complete_task(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        message_code: Optional[str] = None,
        gate_passed: Optional[bool] = None,
    ) -> bool:
        """CAS transition processing -> terminal status with retry."""
        if status == "failed" and message_code is None and error:
            message_code = self._classify_error(error)
        for attempt in range(3):
            if self.update_task_status(
                task_id,
                status,
                result=result,
                error=error,
                expected_status="processing",
                message_code=message_code,
                gate_passed=gate_passed,
            ):
                return True
            time.sleep(0.2 * (attempt + 1))
        logger.error(f"CAS failed completing task {task_id} to {status}")
        return False

    def _start_heartbeat(self, task_id: str) -> Tuple[threading.Event, threading.Event]:
        stop_event = threading.Event()
        lost_event = threading.Event()

        def _beat():
            while not stop_event.wait(WORKER_HEARTBEAT_SECONDS):
                if stop_event.is_set():
                    break
                try:
                    ok = self.update_task_status(
                        task_id, "processing", expected_status="processing"
                    )
                except Exception as exc:
                    logger.warning(f"Heartbeat failed for task {task_id}: {exc}")
                    continue
                if ok is False:
                    logger.info(
                        f"Heartbeat lost ownership of task {task_id} "
                        f"(cancelled or reclaimed)"
                    )
                    lost_event.set()
                    break

        t = threading.Thread(target=_beat, name=f"hb-{task_id}", daemon=True)
        t.start()
        return stop_event, lost_event

    # ── Async chunk gate ──

    @staticmethod
    def _gate_score(value) -> int:
        """Normalize a gate criterion ([0]/[1], bool, int, str) to 0/1."""
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return 1 if int(value) == 1 else 0
        if isinstance(value, str):
            s = value.strip().lower()
            if s in ("1", "true"):
                return 1
        return 0

    @classmethod
    def evaluate_gate(cls, gate_result: Dict, question_types: List[str]) -> Tuple[bool, List[str]]:
        """Mirror of the frontend gateDecision logic: returns (passed, types_ok)."""
        if gate_result.get("rejection_reason") == "gate_disabled_by_pipeline_mode":
            return True, list(question_types)
        if (
            gate_result.get("passed") is True
            and gate_result.get("c1_chunk_informative") is None
            and gate_result.get("c2_chunk_reference_clarity") is None
        ):
            return True, list(question_types)
        c1 = cls._gate_score(gate_result.get("c1_chunk_informative"))
        c2 = cls._gate_score(gate_result.get("c2_chunk_reference_clarity"))
        c3 = cls._gate_score(gate_result.get("c3_chunk_multi_suitability"))
        if c1 == 0 or c2 == 0:
            return False, []
        types_ok = [qt for qt in question_types if qt != "multi" or c3 == 1]
        return len(types_ok) > 0, types_ok

    def _save_chunk_gate(
        self, dataset_id: str, chunk_index, gate_result: Dict,
        passed: bool, types_ok: List[str],
    ) -> bool:
        response = self._request(
            "PUT",
            f"{self.dataset_api_url}/datasets/{dataset_id}/chunks/{chunk_index}/gate",
            json={
                "gate_result": gate_result,
                "gate_passed": passed,
                "question_types_valid": types_ok,
            },
            timeout=10,
        )
        if response is not None and response.status_code == 200:
            return True
        status = response.status_code if response else "no response"
        logger.error(f"Failed to save gate result for {dataset_id}/{chunk_index}: {status}")
        return False

    def _bump_gate_progress(self, dataset_id: str, passed: bool, tasks_added: int) -> None:
        try:
            self._request(
                "POST",
                f"{self.dataset_api_url}/datasets/{dataset_id}/gate-progress",
                json={"passed": passed, "tasks_added": tasks_added},
                timeout=10,
                retries=1,
            )
        except Exception as exc:
            logger.warning(f"Gate progress bump failed for {dataset_id}: {exc}")

    # ── Async chunking ──

    @staticmethod
    def _extract_chunks(chunks_detailed: List[Dict], chunks_text: List[str]) -> List[Dict]:
        """Mirror of the frontend extractChunks: normalize chunker output into
        [{idx, text, fragment_data, hierarchy_context}] dropping empty/short chunks."""
        source: List = chunks_detailed if chunks_detailed else (chunks_text or [])
        extracted: List[Dict] = []
        seen_idx: set = set()
        for index, chunk in enumerate(source):
            if isinstance(chunk, str):
                text = chunk.strip()
                if len(text) >= 10:
                    extracted.append({"idx": index + 1, "text": text, "fragment_data": None, "hierarchy_context": None})
                continue
            if not isinstance(chunk, dict):
                continue
            idx = chunk.get("_chunk_index")
            if idx is None:
                m = re.search(r"_chunk_(\d+)$", str(chunk.get("fragment_id", "")))
                idx = int(m.group(1)) if m else index + 1
            fd = chunk.get("fragment_data") or {}
            text = str(fd.get("combined_text") or fd.get("content") or fd.get("title") or "").strip()
            if len(text) < 10:
                continue
            hierarchy = chunk.get("hierarchy_context") or fd.get("hierarchy_context")
            fragment_data = dict(fd)
            if hierarchy:
                fragment_data["hierarchy_context"] = hierarchy
            if idx in seen_idx:
                continue
            seen_idx.add(idx)
            extracted.append({
                "idx": idx,
                "text": text,
                "fragment_data": fragment_data,
                "hierarchy_context": hierarchy,
            })
        if not extracted and chunks_detailed and chunks_text:
            return TaskWorker._extract_chunks([], chunks_text)
        return extracted

    CHUNKING_STATS_MAX_BYTES = 8192

    @classmethod
    def _compact_chunking_stats(cls, stats: Any) -> Optional[Dict[str, Any]]:
        """chunking_stats из ответа чанкера → метаданные набора (≤ 8 КБ).

        Сначала выбрасываем списки (подозрительные окна, примеры ошибок),
        затем профиль вёрстки; если и этого мало — не сохраняем вовсе.
        """
        if not isinstance(stats, dict) or not stats:
            return None
        compact = copy.deepcopy(stats)

        def _size(d: Dict[str, Any]) -> int:
            return len(json.dumps(d, ensure_ascii=False, default=str))

        if _size(compact) <= cls.CHUNKING_STATS_MAX_BYTES:
            return compact
        seg = compact.get("llm_segmentation")
        if isinstance(seg, dict):
            seg.pop("suspicious_windows", None)
            seg.pop("errors_sample", None)
        if _size(compact) <= cls.CHUNKING_STATS_MAX_BYTES:
            return compact
        compact.pop("layout", None)
        compact.pop("semantic", None)
        return compact if _size(compact) <= cls.CHUNKING_STATS_MAX_BYTES else None

    @staticmethod
    def _block_type_field(extracted_chunk: Dict) -> Dict:
        """``{"block_type": ...}`` for follow-up tasks when the chunker set one."""
        fd = extracted_chunk.get("fragment_data") or {}
        bt = fd.get("block_type") if isinstance(fd, dict) else None
        return {"block_type": bt} if isinstance(bt, str) and bt else {}

    def _patch_dataset(self, dataset_id: str, patch: Dict) -> None:
        try:
            self._request(
                "PATCH",
                f"{self.dataset_api_url}/datasets/{dataset_id}/metadata",
                json=patch,
                timeout=10,
                dataset_auth=True,
            )
        except Exception as exc:
            logger.warning(f"Metadata patch failed for {dataset_id}: {exc}")

    def process_chunk_task(self, task: Dict) -> Tuple[Optional[Dict], str]:
        """Download the raw document, run chunking via the chunker service, save
        the chunks, and fan out gate (or generation) tasks. This moves chunking
        off the browser and into the queue.

        Returns (result, terminal_status): completed on success, failed on error.
        """
        task_id = task["_id"]
        dataset_id = task.get("dataset_id")
        queue_name = task.get("queue_name")
        question_types = task.get("question_types") or [task.get("question_type") or "one"]
        pipeline_mode = task.get("pipeline_mode", "full")
        gate_enabled = pipeline_mode != "generator_validator"
        source_document = task.get("source_document") or "document"
        dataset_name = task.get("dataset_name", "")
        dataset_description = task.get("dataset_description")
        gen_model = task.get("generation_model_id")
        val_model = task.get("validation_model_id")
        document_kind = task.get("document_kind") or "normative"
        # rules | layout | semantic | llm — выбор в UI, проверяет чанкер.
        chunking_method = (task.get("chunking_method") or "rules").strip().lower() or "rules"

        if not dataset_id:
            self._complete_task(task_id, "failed", error="Chunk task missing dataset_id")
            return None, "failed"

        logger.info(
            f"Chunk task {task_id}: chunking document for dataset {dataset_id} "
            f"(types={question_types}, gate={gate_enabled}, kind={document_kind}, method={chunking_method})"
        )

        heartbeat_stop, heartbeat_lost = self._start_heartbeat(task_id)
        try:
            # 1. Download the raw uploaded document from dataset API (GridFS).
            try:
                doc_resp = self._request(
                    "GET",
                    f"{self.dataset_api_url}/datasets/{dataset_id}/document",
                    timeout=60,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                self._complete_task(task_id, "failed", error=f"Fetch document failed: {exc}"[:2000])
                self._patch_dataset(dataset_id, {"status": "failed"})
                return None, "failed"
            if doc_resp is None or doc_resp.status_code != 200:
                error_msg = (
                    self._format_http_error(doc_resp, prefix="Fetch document failed: ")
                    if doc_resp is not None else "Fetch document failed: no response"
                )
                logger.error(f"Chunk task {task_id} failed: {error_msg}")
                self._complete_task(task_id, "failed", error=error_msg)
                self._patch_dataset(dataset_id, {"status": "failed"})
                return None, "failed"

            filename = self._filename_from_disposition(
                doc_resp.headers.get("Content-Disposition", "")
            ) or source_document
            content_type = doc_resp.headers.get("Content-Type", "application/octet-stream")

            # 2. Send the document to the chunker service.
            #    retries=1: повтор выгружал документ заново, пока чанкер ещё
            #    обрабатывал первую копию (таймаут до WORKER_CHUNK_TIMEOUT).
            try:
                chunk_resp = self._request(
                    "POST",
                    f"{self.chunker_url}/chunk/",
                    params={"document_kind": document_kind, "chunking_method": chunking_method},
                    files={"file": (filename, doc_resp.content, content_type)},
                    timeout=WORKER_CHUNK_TIMEOUT_LLM if chunking_method == "llm" else WORKER_CHUNK_TIMEOUT,
                    retries=1,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                self._complete_task(task_id, "failed", error=f"Chunker request failed: {exc}"[:2000])
                self._patch_dataset(dataset_id, {"status": "failed"})
                return None, "failed"
            if chunk_resp is None or chunk_resp.status_code != 200:
                error_msg = (
                    self._format_http_error(chunk_resp, prefix="Chunker error: ")
                    if chunk_resp is not None else "Chunker request failed: no response"
                )
                logger.error(f"Chunk task {task_id} failed: {error_msg}")
                self._complete_task(task_id, "failed", error=error_msg)
                self._patch_dataset(dataset_id, {"status": "failed"})
                return None, "failed"

            if heartbeat_lost.is_set():
                logger.info(f"Chunk task {task_id} no longer processing, aborting")
                return None, "aborted"

            chunk_data = chunk_resp.json()
            doc_type = chunk_data.get("document_type") or {}
            document_name = doc_type.get("document_name") if isinstance(doc_type, dict) else None
            extracted = self._extract_chunks(
                chunk_data.get("chunks_detailed") or [],
                chunk_data.get("chunks") or [],
            )

            if not extracted:
                logger.warning(f"Chunk task {task_id}: no usable chunks produced")
                self._complete_task(task_id, "failed", error="No usable chunks produced from document")
                self._patch_dataset(dataset_id, {"status": "failed", "total_chunks": 0})
                return None, "failed"

            # 3. Persist chunks. Gate-enabled runs mark them pending gate; gate-off
            #    runs mark them pre-passed so generation can start immediately.
            chunk_docs = [
                {
                    "chunk_index": ec["idx"],
                    "chunk_text": ec["text"],
                    "fragment_data": ec.get("fragment_data"),
                    "hierarchy_context": ec.get("hierarchy_context"),
                    "gate_result": None,
                    "gate_passed": not gate_enabled,
                    "gate_pending": gate_enabled,
                    "question_types_valid": [] if gate_enabled else list(question_types),
                }
                for ec in extracted
            ]
            save_resp = self._request(
                "POST",
                f"{self.dataset_api_url}/datasets/{dataset_id}/chunks",
                json=chunk_docs,
                timeout=60,
                dataset_auth=True,
            )
            if save_resp is None or save_resp.status_code != 200:
                error_msg = (
                    self._format_http_error(save_resp, prefix="Save chunks failed: ")
                    if save_resp is not None else "Save chunks failed: no response"
                )
                logger.error(f"Chunk task {task_id}: {error_msg}")
                self._complete_task(task_id, "failed", error=error_msg)
                self._patch_dataset(dataset_id, {"status": "failed"})
                return None, "failed"

            # 4. Fan out follow-up tasks.
            model_fields = {}
            if gen_model:
                model_fields["generation_model_id"] = gen_model
            if val_model:
                model_fields["validation_model_id"] = val_model

            if gate_enabled:
                gate_qtype = "multi" if "multi" in question_types else question_types[0]
                followup = [
                    {
                        "chunk_id": ec["idx"],
                        "chunk_text": ec["text"],
                        "question_type": gate_qtype,
                        "question_types": question_types,
                        "task_type": "gate",
                        "source_document": source_document,
                        "dataset_name": dataset_name,
                        "dataset_id": dataset_id,
                        "dataset_description": dataset_description,
                        "priority": TASK_PRIORITY_GATE,
                        "pipeline_mode": pipeline_mode,
                        "document_kind": document_kind,
                        **self._block_type_field(ec),
                        **model_fields,
                    }
                    for ec in extracted
                ]
            else:
                followup = [
                    {
                        "chunk_id": ec["idx"],
                        "chunk_text": ec["text"],
                        "question_type": qt,
                        "task_type": "generation",
                        "source_document": source_document,
                        "dataset_name": dataset_name,
                        "dataset_id": dataset_id,
                        "dataset_description": dataset_description,
                        "priority": TASK_PRIORITY_GENERATION,
                        "chunk_pre_validated": True,
                        "pipeline_mode": pipeline_mode,
                        "document_kind": document_kind,
                        **self._block_type_field(ec),
                        **model_fields,
                    }
                    for ec in extracted
                    for qt in question_types
                ]

            add_resp = self._request(
                "POST",
                f"{self.task_queue_url}/queues/{queue_name}/tasks/",
                json=followup,
                timeout=30,
                dataset_auth=True,
                retries=1,
            )
            if add_resp is None or add_resp.status_code != 200:
                error_msg = (
                    self._format_http_error(add_resp, prefix="Enqueue follow-up failed: ")
                    if add_resp is not None else "Enqueue follow-up failed: no response"
                )
                logger.error(f"Chunk task {task_id}: {error_msg}")
                self._complete_task(task_id, "failed", error=error_msg)
                self._patch_dataset(dataset_id, {"status": "failed"})
                return None, "failed"
            tasks_added = add_resp.json().get("tasks_added", len(followup))

            # 5. Update dataset metadata now that we know the chunk count.
            patch = {
                "total_chunks": len(extracted),
                "num_chunks_processed": 0,
                "chunking_done": True,
                "status": "processing",
                "last_updated": datetime.utcnow().isoformat(),
            }
            if document_name:
                patch["source_document"] = document_name
            if not gate_enabled:
                patch["chunks_passed_gate"] = len(extracted)
                patch["expected_questions"] = len(extracted) * len(question_types)
            # What the chunker threw away (exercises, front matter, debris) and
            # how many chunks of each block type it made — for the dataset card.
            if isinstance(chunk_data.get("dropped_blocks"), dict):
                patch["chunking_dropped_blocks"] = chunk_data["dropped_blocks"]
            if isinstance(chunk_data.get("chunks_by_block_type"), dict):
                patch["chunks_by_block_type"] = chunk_data["chunks_by_block_type"]
            # Метод, который реально отработал (с причиной фолбэка), для карточки набора.
            if isinstance(chunk_data.get("chunking_method"), str):
                patch["chunking_method"] = chunk_data["chunking_method"]
            if isinstance(chunk_data.get("chunking_engine"), str):
                patch["chunking_engine"] = chunk_data["chunking_engine"]
            if chunk_data.get("chunking_fallback"):
                patch["chunking_fallback"] = str(chunk_data["chunking_fallback"])[:500]
            # Диагностика чанкера (окна llm-сегментации, источник абзацев,
            # профиль вёрстки) — для карточки набора; ограничена по размеру.
            chunking_stats = self._compact_chunking_stats(chunk_data.get("chunking_stats"))
            if chunking_stats:
                patch["chunking_stats"] = chunking_stats
            self._patch_dataset(dataset_id, patch)

            summary = {
                "chunks": len(extracted),
                "gate_enabled": gate_enabled,
                "tasks_added": tasks_added,
            }
            logger.info(
                f"Chunk task {task_id}: {len(extracted)} chunks, {tasks_added} "
                f"{'gate' if gate_enabled else 'generation'} tasks queued"
            )
            self._complete_task(task_id, "completed", result=summary)
            return summary, "completed"

        except Exception as e:
            error_msg = f"Chunk processing error: {str(e)}"
            logger.error(f"Chunk task {task_id} failed: {error_msg}")
            self._complete_task(task_id, "failed", error=error_msg[:2000])
            try:
                self._patch_dataset(dataset_id, {"status": "failed"})
            except Exception:
                pass
            return None, "failed"
        finally:
            heartbeat_stop.set()

    def process_gate_task(self, task: Dict) -> Tuple[Optional[Dict], str]:
        """Validate one chunk in the background and fan out generation tasks.

        Returns (result, terminal_status): gate_done = gate passed and
        generation tasks enqueued, gate_rejected = chunk rejected,
        failed = error. Both gate_done and gate_rejected are already marked
        completed on the task document.
        """
        task_id = task["_id"]
        chunk_text = task.get("chunk_text", "")
        dataset_id = task.get("dataset_id")
        chunk_id = task.get("chunk_id", "unknown")
        question_types = task.get("question_types") or [task.get("question_type", "one")]

        logger.info(
            f"Gate task {task_id}: validating chunk {chunk_id} "
            f"(dataset: {dataset_id}, types: {question_types})"
        )

        heartbeat_stop, heartbeat_lost = self._start_heartbeat(task_id)
        try:
            gate_qtype = "multi" if "multi" in question_types else question_types[0]
            payload = {
                "chunk": chunk_text,
                "question_type": gate_qtype,
                "document_kind": task.get("document_kind") or "normative",
            }
            if task.get("block_type"):
                payload["block_type"] = task["block_type"]
            if task.get("validation_model_id"):
                payload["validation_model_id"] = task["validation_model_id"]

            try:
                # retries=1 как в генерации: ретраи здесь множили запросы к
                # и так не отвечающему LLM-хосту (5xx повторялись всем циклом).
                response = self._request(
                    "POST",
                    f"{self.agent_api_url}/chunk_gate/",
                    json=payload,
                    timeout=WORKER_AGENT_TIMEOUT,
                    retries=1,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                error_msg = f"Gate request failed: {exc}"
                logger.error(f"Gate task {task_id} failed: {error_msg}")
                self._complete_task(task_id, "failed", error=error_msg[:2000])
                return None, "failed"

            if response is None or response.status_code != 200:
                error_msg = (
                    self._format_http_error(response, prefix="Gate API error: ")
                    if response is not None
                    else "Gate request failed: no response"
                )
                logger.error(f"Gate task {task_id} failed: {error_msg}")
                self._complete_task(task_id, "failed", error=error_msg)
                return None, "failed"

            gate_result = response.json().get("result", {}) or {}
            passed, types_ok = self.evaluate_gate(gate_result, question_types)

            if heartbeat_lost.is_set():
                logger.info(f"Gate task {task_id} no longer processing, aborting")
                return None, "aborted"

            if dataset_id:
                if not self._save_chunk_gate(dataset_id, chunk_id, gate_result, passed, types_ok):
                    self._complete_task(
                        task_id, "failed", error="Failed to save gate verdict to dataset"
                    )
                    return None, "failed"

            tasks_added = 0
            if passed and types_ok:
                gen_tasks = [
                    {
                        "chunk_id": task["chunk_id"],
                        "chunk_text": chunk_text,
                        "question_type": qt,
                        "source_document": task.get("source_document", "unknown"),
                        "dataset_name": task.get("dataset_name", ""),
                        "dataset_id": dataset_id,
                        "dataset_description": task.get("dataset_description"),
                        "priority": TASK_PRIORITY_GENERATION,
                        "chunk_pre_validated": True,
                        "pipeline_mode": task.get("pipeline_mode", "full"),
                        "task_type": "generation",
                        "document_kind": task.get("document_kind") or "normative",
                        **({"block_type": task["block_type"]} if task.get("block_type") else {}),
                        **(
                            {"generation_model_id": task["generation_model_id"]}
                            if task.get("generation_model_id") else {}
                        ),
                        **(
                            {"validation_model_id": task["validation_model_id"]}
                            if task.get("validation_model_id") else {}
                        ),
                    }
                    for qt in types_ok
                ]
                queue_name = task.get("queue_name")
                add_resp = self._request(
                    "POST",
                    f"{self.task_queue_url}/queues/{queue_name}/tasks/",
                    json=gen_tasks,
                    timeout=30,
                    dataset_auth=True,
                    retries=1,
                )
                if add_resp is None or add_resp.status_code != 200:
                    error_msg = (
                        self._format_http_error(add_resp, prefix="Enqueue generation failed: ")
                        if add_resp is not None
                        else "Enqueue generation failed: no response"
                    )
                    logger.error(f"Gate task {task_id}: {error_msg}")
                    self._complete_task(task_id, "failed", error=error_msg)
                    return None, "failed"
                tasks_added = add_resp.json().get("tasks_added", len(gen_tasks))

            if dataset_id:
                self._bump_gate_progress(dataset_id, passed, tasks_added)

            gate_summary = {"gate_result": gate_result, "types_ok": types_ok, "tasks_added": tasks_added}
            if passed:
                msg_code = self._gate_message_code(
                    True,
                    gate_result.get("rejection_reason"),
                    question_types=question_types,
                    types_ok=types_ok,
                )
                logger.info(f"Gate task {task_id}: chunk {chunk_id} passed, {tasks_added} generation tasks queued")
                self._complete_task(
                    task_id,
                    "completed",
                    result=gate_summary,
                    message_code=msg_code,
                    gate_passed=True,
                )
                return gate_summary, "gate_done"

            reason = gate_result.get("rejection_reason", "unknown")
            logger.info(f"Gate task {task_id}: chunk {chunk_id} rejected ({reason})")
            self._complete_task(
                task_id,
                "completed",
                result=gate_summary,
                message_code=self._gate_message_code(False, reason),
                gate_passed=False,
            )
            return gate_summary, "gate_rejected"

        except Exception as e:
            error_msg = f"Gate processing error: {str(e)}"
            logger.error(f"Gate task {task_id} failed: {error_msg}")
            self._complete_task(task_id, "failed", error=error_msg[:2000])
            return None, "failed"
        finally:
            heartbeat_stop.set()

    def process_task(self, task: Dict) -> Tuple[Optional[Dict], str]:
        """Returns (result, terminal_status).

        terminal_status: completed|failed|cancelled|gate_done|gate_rejected|aborted
        (aborted = task no longer processing; do not update status).
        """
        task_id = task["_id"]

        if task.get("status") == "cancelled":
            logger.info(f"Task {task_id} is cancelled, skipping")
            return None, "cancelled"

        if task.get("task_type") == "chunk":
            return self.process_chunk_task(task)

        if task.get("task_type") == "gate":
            return self.process_gate_task(task)

        chunk_text = task.get("chunk_text", "")
        question_type = task["question_type"]
        dataset_id = task.get("dataset_id")
        chunk_id = task.get("chunk_id", "unknown")

        if not chunk_text or len(chunk_text.strip()) < 10:
            error_msg = (
                f"Chunk {chunk_id} is empty or too short "
                f"(length: {len(chunk_text) if chunk_text else 0})"
            )
            logger.warning(f"Task {task_id} skipped: {error_msg}")
            self._complete_task(task_id, "failed", error=error_msg)
            return None, "failed"

        logger.info(
            f"Processing task {task_id}: {question_type} question for chunk {chunk_id} "
            f"(dataset: {dataset_id}, text length: {len(chunk_text)})"
        )

        heartbeat_stop, heartbeat_lost = self._start_heartbeat(task_id)

        try:
            payload = {
                "prompt": chunk_text,
                "question_type": question_type,
                "source": task.get("source_document", "unknown"),
                "chat_id": str(chunk_id),
                "source_text": chunk_text,
                "document_kind": task.get("document_kind") or "normative",
            }
            if task.get("block_type"):
                payload["block_type"] = task["block_type"]
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
                if heartbeat_lost.is_set():
                    logger.info(f"Task {task_id} no longer processing, aborting")
                    return None, "aborted"
                try:
                    response = self._request(
                        "POST",
                        f"{self.agent_api_url}/process_prompt/",
                        json=payload,
                        timeout=WORKER_AGENT_TIMEOUT,
                        retries=1,
                    )
                except requests.exceptions.Timeout as exc:
                    # Таймаут не повторяем: пайплайн на стороне agent_api продолжает
                    # работать, и повтор запускал бы параллельный пайплайн на тот же
                    # чанк (инцидент «503 busy» на общем LLM-хосте). Задача падает с
                    # model_unavailable, повтор — вручную через retry-failed.
                    error_msg = (
                        f"Agent request timed out after {attempt + 1} attempt(s), not retrying "
                        f"(chunk length: {len(chunk_text)} chars): {exc}"
                    )
                    logger.error(f"Task {task_id} (chunk {chunk_id}) failed: {error_msg}")
                    self._complete_task(
                        task_id, "failed", error=error_msg, message_code="model_unavailable"
                    )
                    return None, "failed"
                except requests.exceptions.ConnectionError as exc:
                    # Соединения не было — пайплайн на сервере не запущен, повтор
                    # безопасен, но с экспоненциальной паузой.
                    if attempt < WORKER_AGENT_MAX_RETRIES:
                        delay = self._backoff_seconds(attempt)
                        logger.warning(
                            f"Task {task_id} agent connection error "
                            f"(attempt {attempt + 1}/{_max_attempts}), retrying in {delay}s: {exc}"
                        )
                        time.sleep(delay)
                        continue
                    error_msg = (
                        f"Agent request failed after {_max_attempts} attempts "
                        f"(chunk length: {len(chunk_text)} chars): {exc}"
                    )
                    logger.error(f"Task {task_id} (chunk {chunk_id}) failed: {error_msg}")
                    self._complete_task(task_id, "failed", error=error_msg)
                    return None, "failed"
                if response is None:
                    if attempt < WORKER_AGENT_MAX_RETRIES:
                        time.sleep(self._backoff_seconds(attempt))
                        continue
                    error_msg = (
                        f"Agent request failed after {_max_attempts} attempts "
                        f"(chunk length: {len(chunk_text)} chars)"
                    )
                    logger.error(f"Task {task_id} (chunk {chunk_id}) failed: {error_msg}")
                    self._complete_task(task_id, "failed", error=error_msg)
                    return None, "failed"
                if response.status_code == 504:
                    # Gateway timeout ("llm timeout"): как и клиентский таймаут —
                    # серверный пайплайн ещё жив, повтор только множит нагрузку.
                    break
                if response.status_code >= 500 and attempt < WORKER_AGENT_MAX_RETRIES:
                    # 502/503 ("llm unreachable"/"llm busy") и прочие 5xx — повтор с
                    # backoff 1, 4, 16 с, Retry-After сервера имеет приоритет.
                    delay = self._backoff_seconds(attempt, self._retry_after(response))
                    logger.warning(
                        f"Task {task_id} agent {response.status_code} "
                        f"(attempt {attempt + 1}/{_max_attempts}), retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                # 2xx/4xx — без повторов.
                break

            if response is None:
                error_msg = (
                    f"Agent request failed after {_max_attempts} attempts "
                    f"(chunk length: {len(chunk_text)} chars)"
                )
                self._complete_task(task_id, "failed", error=error_msg)
                return None, "failed"

            if heartbeat_lost.is_set():
                logger.info(f"Task {task_id} no longer processing, aborting")
                return None, "aborted"

            if response.status_code == 200:
                result = response.json()
                output = result.get("result", {}).get("output", {})

                if heartbeat_lost.is_set():
                    logger.info(f"Task {task_id} no longer processing, aborting")
                    return None, "aborted"

                if output.get("chunk_rejected"):
                    gate = output.get("chunk_gate_result") or {}
                    reason = gate.get("rejection_reason", "unknown")
                    logger.info(f"Chunk {chunk_id} rejected by gate: {reason}")
                    self._complete_task(
                        task_id,
                        "completed",
                        result=result,
                        message_code=self._gate_message_code(False, reason),
                        gate_passed=False,
                    )
                    return result, "gate_rejected"

                if dataset_id:
                    if heartbeat_lost.is_set():
                        logger.info(f"Task {task_id} no longer processing, aborting")
                        return None, "aborted"
                    if not self.save_question_to_dataset(task, result):
                        self._complete_task(
                            task_id, "failed", error="Failed to save question to dataset"
                        )
                        return None, "failed"

                logger.info(f"Task {task_id} completed successfully for chunk {chunk_id}")
                return result, "completed"

            error_msg = self._format_http_error(response, prefix="Agent API error: ")
            logger.error(f"Task {task_id} (chunk {chunk_id}) failed: {error_msg}")
            self._complete_task(task_id, "failed", error=error_msg)
            return None, "failed"

        except Exception as e:
            error_msg = f"Processing error: {str(e)}"
            logger.error(f"Task {task_id} failed: {error_msg}")
            self._complete_task(task_id, "failed", error=error_msg)
            return None, "failed"
        finally:
            heartbeat_stop.set()

    @staticmethod
    def validation_fields_from_result(validation_result: Dict) -> Dict[str, str]:
        """Validator output → the validation_* fields stored on a question.

        One mapping for generation and revalidation, so the UI parses both the same.
        """
        vr = validation_result or {}
        details = {
            **(vr.get("by_block") or {}),
            **(
                {
                    "__block_weighted__": vr["block_weighted"],
                    "__block_max__": vr.get("block_max", {}),
                }
                if vr.get("block_weighted")
                else {}
            ),
        }
        return {
            "validation_passed": str(vr.get("passed", False)),
            "validation_score": f"{vr.get('total', 'N/A')}/{vr.get('max_total', 'N/A')}",
            "validation_threshold": str(vr.get("threshold", "N/A")),
            "validation_details": json.dumps(details, ensure_ascii=False),
            "validation_justifications": json.dumps(vr.get("justifications", {}), ensure_ascii=False),
        }

    # ── Quality revalidation ──

    def claim_revalidation_item(self) -> Optional[Dict]:
        try:
            resp = self._request(
                "POST",
                f"{self.dataset_api_url}/revalidation/claim",
                json={"worker_id": self.worker_id},
                timeout=HTTP_TIMEOUT,
                retries=1,
                dataset_auth=True,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            logger.warning(f"Revalidation claim failed: {exc}")
            return None
        if resp is None or resp.status_code != 200:
            if resp is not None and resp.status_code != 404:
                logger.warning(f"Revalidation claim HTTP {resp.status_code}")
            return None
        return (resp.json() or {}).get("item")

    def _revalidation_heartbeat(self, item_id: str) -> threading.Event:
        stop_event = threading.Event()

        def _beat():
            while not stop_event.wait(WORKER_HEARTBEAT_SECONDS):
                try:
                    self._request(
                        "PUT",
                        f"{self.dataset_api_url}/revalidation/items/{item_id}/heartbeat",
                        timeout=10,
                        retries=1,
                        dataset_auth=True,
                    )
                except Exception as exc:
                    logger.warning(f"Revalidation heartbeat failed for {item_id}: {exc}")

        threading.Thread(target=_beat, name=f"rv-hb-{item_id}", daemon=True).start()
        return stop_event

    def process_revalidation_item(self, item: Dict) -> str:
        """Run only the quality validator for one question; report result or error.

        Returns "done" or "error". A technical error is reported as an error,
        never as a failed verdict: dataset_api keeps the previous result.
        """
        item_id = item["item_id"]
        logger.info(
            f"Revalidation item {item_id}: question {item.get('question_id')} "
            f"(dataset {item.get('dataset_id')} v{item.get('version')})"
        )
        payload = {
            "question": item.get("question") or {},
            "source_text": item.get("source_text") or "",
            "document_kind": item.get("document_kind") or "normative",
        }
        if item.get("validation_model_id"):
            payload["validation_model_id"] = item["validation_model_id"]
        body: Dict[str, Any]
        hb_stop = self._revalidation_heartbeat(item_id)
        try:
            try:
                resp = self._request(
                    "POST",
                    f"{self.agent_api_url}/validate_question/",
                    json=payload,
                    timeout=WORKER_AGENT_TIMEOUT,
                    retries=2,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                resp = None
                body = {"error": f"Agent request failed: {exc}"[:500]}
            else:
                if resp is not None and resp.status_code == 200:
                    result = (resp.json() or {}).get("result") or {}
                    body = {"result": self.validation_fields_from_result(result)}
                elif resp is not None:
                    body = {"error": self._format_http_error(resp, prefix="Agent API error: ")}
                else:
                    body = {"error": "Agent request failed: no response"}
        finally:
            hb_stop.set()
        body["worker_id"] = self.worker_id
        try:
            put = self._request(
                "PUT",
                f"{self.dataset_api_url}/revalidation/items/{item_id}",
                json=body,
                timeout=HTTP_TIMEOUT,
                retries=2,
                dataset_auth=True,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            logger.error(f"Revalidation item {item_id}: failed to report result: {exc}")
            return "error"
        if put is None or put.status_code != 200:
            code = put.status_code if put is not None else "no response"
            logger.error(f"Revalidation item {item_id}: result not accepted ({code})")
            return "error"
        status = "done" if "result" in body else "error"
        logger.info(f"Revalidation item {item_id}: {status}" + (f" ({body.get('error')})" if status == "error" else ""))
        return status

    def _process_revalidation_item_safe(self, item: Dict) -> None:
        try:
            self.process_revalidation_item(item)
        except Exception:
            logger.exception("Error processing revalidation item %s", item.get("item_id"))

    def process_revalidation_items(self, limit: int) -> int:
        """Claim and process up to `limit` revalidation items; returns how many.

        Up to WORKER_REVALIDATION_CONCURRENCY items run at once: each item is
        self-contained (own heartbeat, own result report), so a failing one
        never affects the others.
        """
        concurrency = max(1, WORKER_REVALIDATION_CONCURRENCY)
        processed = 0
        while processed < limit and not self.stop.is_set():
            batch: List[Dict] = []
            while len(batch) < min(concurrency, limit - processed):
                item = self.claim_revalidation_item()
                if not item:
                    break
                batch.append(item)
            if not batch:
                break
            if len(batch) == 1:
                self._process_revalidation_item_safe(batch[0])
            else:
                with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    list(pool.map(self._process_revalidation_item_safe, batch))
            processed += len(batch)
            if len(batch) < concurrency:
                break  # the queue ran dry
        return processed

    def save_question_to_dataset(self, task: Dict, result: Dict) -> bool:
        try:
            dataset_id = task.get("dataset_id")
            if not dataset_id:
                logger.warning(f"No dataset_id for task {task['_id']}, skipping save")
                return True

            output = result.get("result", {}).get("output", {})
            generated_question = output.get("generated_question") or {}
            sensitivity_score = output.get("sensitivity_score") or {}
            validation_result = output.get("validation_result") or {}
            difficulty_score = output.get("difficulty_score") or {}

            options_dict = {}
            for i in range(1, 10):
                option_key = f"option_{i}"
                if option_key in generated_question and generated_question[option_key] not in [None, "None"]:
                    options_dict[option_key] = generated_question[option_key]

            # Контракт agent_api: при assessment_failed оценки (sensitivity/difficulty)
            # равны None — не подменяем их дефолтами и не превращаем в строку "None".
            # Флаг читаем и top-level (assessment_failed/assessment_errors), и на
            # самих объектах оценок (assessment_failed/assessment_error).
            assessment_failed = bool(
                result.get("assessment_failed") or output.get("assessment_failed")
            )
            sensitivity_failed = assessment_failed or bool(sensitivity_score.get("assessment_failed"))
            difficulty_failed = assessment_failed or bool(difficulty_score.get("assessment_failed"))
            if sensitivity_failed or difficulty_failed:
                assessment_errors = list(
                    result.get("assessment_errors") or output.get("assessment_errors") or []
                )
                for label, score in (("sensitivity", sensitivity_score), ("difficulty", difficulty_score)):
                    if score.get("assessment_error"):
                        assessment_errors.append(f"{label}: {score['assessment_error']}")
                logger.warning(
                    f"Task {task['_id']}: assessment failed, saving scores as None: "
                    f"{assessment_errors}"
                )
            provocativeness_raw = None if sensitivity_failed else sensitivity_score.get(
                "provocativeness_score"
            )
            difficulty_raw = None if difficulty_failed else difficulty_score.get("difficulty")
            provocativeness = None if provocativeness_raw is None else str(provocativeness_raw)
            difficulty = None if difficulty_raw is None else str(difficulty_raw)

            resolved_question_type = output.get("question_type") or task["question_type"]
            question_data = {
                "chunk_id": task["chunk_id"],
                "question_type": resolved_question_type,
                "task": generated_question.get("task", ""),
                "options": options_dict,
                "correct_answer": str(generated_question.get("outputs", "")),
                # QuestionData.provocativeness в dataset_api — обязательная str
                # (None → 422), поэтому «нет оценки» кодируем пустой строкой, как
                # это делает фронт (questionPayload: prov == null ? ''); difficulty
                # там Optional — уходит null.
                "provocativeness": "" if provocativeness is None else provocativeness,
                "difficulty": difficulty,
                "sensitivity_explanation": str(sensitivity_score.get("explanation", "") or ""),
                "difficulty_explanation": str(difficulty_score.get("explanation", "") or ""),
                **self.validation_fields_from_result(validation_result),
                "validation_is_current": True,
                "validation_model_id": task.get("validation_model_id") or None,
                "retry_count": str(output.get("retry_count", 0)),
                "source_chunk": task["chunk_text"],
                "question_id": str(uuid.uuid4()),
                "idempotency_key": f"{task['chunk_id']}:{task['question_type']}",
            }

            response = self._request(
                "POST",
                f"{self.dataset_api_url}/datasets/{dataset_id}/add-question",
                json=question_data,
                timeout=HTTP_TIMEOUT,
                dataset_auth=True,
            )

            if response and response.status_code == 200:
                save_result = response.json()
                if save_result.get("duplicate"):
                    logger.info(
                        f"Question already in dataset {dataset_id} "
                        f"(idempotent, total: {save_result.get('total_questions')})"
                    )
                else:
                    logger.info(
                        f"Question saved to dataset {dataset_id}, "
                        f"total questions: {save_result.get('total_questions')}"
                    )
                    self.update_dataset_progress(
                        dataset_id, task.get("dataset_name", "Unknown")
                    )
                return True

            status = response.status_code if response else "no response"
            body = (
                self._format_http_error(response)
                if response
                else "no response"
            )
            logger.error(f"Failed to save question to dataset {dataset_id}: {status} {body}")
            return False

        except Exception as e:
            logger.error(f"Error saving question to dataset: {str(e)}")
            return False

    def update_dataset_progress(self, dataset_id: str, dataset_name: str):
        if dataset_id not in self.dataset_progress:
            self.dataset_progress[dataset_id] = {
                "name": dataset_name,
                "processed_tasks": 0,
                "total_tasks": 0,
                "last_updated": datetime.now(),
            }
        self.dataset_progress[dataset_id]["processed_tasks"] += 1
        self.dataset_progress[dataset_id]["last_updated"] = datetime.now()
        logger.info(
            f"Dataset {dataset_name} progress: "
            f"{self.dataset_progress[dataset_id]['processed_tasks']} tasks processed"
        )

    def check_dataset_completion(self, dataset_id: str, dataset_name: str):
        try:
            response = self._request(
                "GET",
                f"{self.dataset_api_url}/datasets/{dataset_id}/tasks",
                timeout=10,
                dataset_auth=True,
            )
            if response and response.status_code == 200:
                dataset_tasks = response.json()
                completed_tasks = [t for t in dataset_tasks if t.get("status") == "completed"]
                failed_tasks = [t for t in dataset_tasks if t.get("status") == "failed"]
                # Gate rejections used to be status=skipped; now they are
                # completed with gate_passed=False / message_code gate_rejected_*.
                # _is_gate_rejection also covers legacy skipped gate rows.
                rejected_gate_tasks = [
                    t for t in dataset_tasks
                    if self._is_gate_rejection(t)
                ]
                pending_tasks = [t for t in dataset_tasks if t.get("status") == "pending"]
                processing_tasks = [t for t in dataset_tasks if t.get("status") == "processing"]

                total_tasks = len(dataset_tasks)
                completed_count = len(completed_tasks)
                failed_count = len(failed_tasks)
                skipped_count = len(rejected_gate_tasks)
                pending_count = len(pending_tasks)
                processing_count = len(processing_tasks)

                logger.info(
                    f"Dataset {dataset_name} status: {completed_count}/{total_tasks} completed, "
                    f"{failed_count} failed, {skipped_count} gate-rejected, "
                    f"{pending_count} pending, {processing_count} processing"
                )

                if pending_count == 0 and processing_count == 0 and total_tasks > 0:
                    logger.info(f"Dataset {dataset_name} processing completed!")
                    self.finalize_dataset(
                        dataset_id,
                        dataset_name,
                        completed_count,
                        failed_count,
                        skipped_count,
                        total_tasks,
                        dataset_tasks=dataset_tasks,
                    )
            elif response:
                logger.error(f"Failed to fetch dataset tasks: {response.status_code} {response.text}")
        except Exception as e:
            logger.error(f"Error checking dataset completion: {str(e)}")

    @staticmethod
    def _is_gate_rejection(task: Dict) -> bool:
        """True when a (gate) task finished by rejecting the chunk."""
        if task.get("gate_passed") is False:
            return True
        code = task.get("message_code") or ""
        if isinstance(code, str) and code.startswith("gate_rejected_"):
            return True
        # Legacy: status=skipped with chunk_rejected error.
        if task.get("status") == "skipped":
            err = task.get("error") or ""
            if isinstance(err, str) and err.startswith("chunk_rejected"):
                return True
            if task.get("task_type") == "gate":
                return True
        return False

    @staticmethod
    def _is_no_valid_chunks(dataset_tasks: List[Dict]) -> bool:
        """True when gate ran, rejected every chunk, and nothing was enqueued for generation."""
        gate_tasks = [t for t in dataset_tasks if t.get("task_type") == "gate"]
        gen_tasks = [
            t for t in dataset_tasks
            if t.get("task_type") not in ("gate", "chunk")
        ]
        if not gate_tasks or gen_tasks:
            return False
        return all(TaskWorker._is_gate_rejection(t) for t in gate_tasks)

    def finalize_dataset(
        self,
        dataset_id: str,
        dataset_name: str,
        completed_count: int,
        failed_count: int,
        skipped_count: int,
        total_tasks: int,
        dataset_tasks: Optional[List[Dict]] = None,
    ):
        try:
            completed_rejections = 0
            if dataset_tasks:
                completed_rejections = sum(
                    1
                    for t in dataset_tasks
                    if t.get("status") == "completed" and self._is_gate_rejection(t)
                )
            effective_completed = max(0, completed_count - completed_rejections)
            if dataset_tasks and self._is_no_valid_chunks(dataset_tasks):
                status = "no_valid_chunks"
            elif failed_count > 0 and effective_completed == 0:
                # Nothing came out of the run at all.
                status = "failed"
            elif failed_count > 0:
                # Some questions are missing — say so instead of plain "completed".
                status = "completed_with_failures"
            else:
                status = "completed"
            metadata_patch = {
                "status": status,
                "completed_at": datetime.utcnow().isoformat(),
                "total_tasks": total_tasks,
                "completed_tasks": effective_completed,
                "failed_tasks": failed_count,
                "skipped_tasks": skipped_count,
                "success_rate": f"{effective_completed}/{total_tasks}" if total_tasks > 0 else "0/0",
            }
            if status == "no_valid_chunks":
                metadata_patch["error"] = "no_valid_chunks"

            response = self._request(
                "PATCH",
                f"{self.dataset_api_url}/datasets/{dataset_id}/metadata",
                json=metadata_patch,
                timeout=10,
                dataset_auth=True,
            )

            if response and response.status_code == 200:
                logger.info(f"Dataset {dataset_name} finalized successfully (status={status})")
            elif response:
                logger.error(
                    f"Failed to finalize dataset {dataset_name}: {response.status_code} {response.text}"
                )
        except Exception as e:
            logger.error(f"Error finalizing dataset: {str(e)}")

    def process_batch(self, tasks: List[Dict]):
        if not tasks:
            return

        logger.info(f"Processing batch of {len(tasks)} tasks")
        dataset_tasks: Dict[str, List[Dict]] = {}
        for task in tasks:
            dataset_id = task.get("dataset_id")
            if dataset_id:
                dataset_tasks.setdefault(dataset_id, []).append(task)

        for i, task in enumerate(tasks):
            if self.stop.is_set():
                # Остановка запрошена: новые задачи не начинаем, остаток
                # батча возвращаем в pending (владелец — мы, CAS по worker_id).
                self._requeue_unstarted_tasks(tasks[i:])
                break
            try:
                result, terminal_status = self.process_task(task)
            except Exception as exc:
                logger.exception("Error processing task %s", task.get("_id"))
                try:
                    self._complete_task(task["_id"], "failed", error=f"Processing error: {exc}"[:2000])
                except Exception:
                    pass
                terminal_status = "failed"
                result = None
            if terminal_status == "completed" and result:
                self._complete_task(task["_id"], "completed", result=result)
            elif terminal_status == "cancelled":
                # Claimed while already cancelled — ensure terminal status sticks.
                self._complete_task(task["_id"], "cancelled")
            elif terminal_status == "aborted":
                pass  # task reclaimed or cancelled elsewhere
            elif terminal_status in ("failed", "gate_done", "gate_rejected"):
                pass  # already marked inside process_task / process_gate_task

            # Recovery between tasks; skip unprocessed tasks still in this batch.
            # При запрошенной остановке не начинаем и recovery.
            if i < len(tasks) - 1 and not self.stop.is_set():
                remaining = {t["_id"] for t in tasks[i + 1 :]}
                self.recover_stuck_tasks(exclude_task_ids=remaining)

        time.sleep(1)

        for dataset_id, dataset_task_list in dataset_tasks.items():
            dataset_name = dataset_task_list[0].get("dataset_name", "Unknown")
            self.check_dataset_completion(dataset_id, dataset_name)
        # Sweep any datasets that the event-driven finalize missed (TOCTOU
        # between two workers closing the last two tasks).
        self.reconcile_dataset_status()

    def _requeue_unstarted_tasks(self, tasks: List[Dict]) -> int:
        """Вернуть ещё не начатые задачи батча в pending при graceful stop."""
        requeued = 0
        for task in tasks:
            task_id = task["_id"]
            try:
                ok = self.update_task_status(task_id, "pending", expected_status="processing")
            except Exception as exc:
                logger.warning(f"Failed to requeue task {task_id} on stop: {exc}")
                continue
            if ok:
                requeued += 1
            else:
                logger.warning(f"Failed to requeue task {task_id} on stop (result={ok})")
        logger.info(f"Stop requested: requeued {requeued}/{len(tasks)} unstarted tasks to 'pending'")
        return requeued

    def recover_stuck_tasks(self, exclude_task_ids: Optional[set] = None):
        try:
            response = self._request(
                "GET",
                f"{self.task_queue_url}/tasks/stuck",
                params={"threshold_minutes": WORKER_STUCK_THRESHOLD_MINUTES},
                timeout=10,
            )
            if response and response.status_code == 200:
                stuck_tasks = response.json()
                requeued = 0
                force_failed = 0
                skipped = 0
                for task in stuck_tasks:
                    task_id = task["_id"]
                    if exclude_task_ids and task_id in exclude_task_ids:
                        skipped += 1
                        continue
                    attempts = int(task.get("attempts") or 0) + 1
                    if attempts >= WORKER_MAX_TASK_ATTEMPTS:
                        error_msg = (
                            f"Force-failed after {attempts} stuck recoveries "
                            f"(>= WORKER_MAX_TASK_ATTEMPTS={WORKER_MAX_TASK_ATTEMPTS})"
                        )
                        ok = self.update_task_status(
                            task_id,
                            "failed",
                            error=error_msg,
                            attempts=attempts,
                            expected_status="processing",
                            message_code=self._classify_error(error_msg),
                            check_owner=False,
                        )
                        if ok:
                            force_failed += 1
                            logger.warning(f"Force-failed stuck task {task_id} after {attempts} attempts")
                        else:
                            logger.warning(f"Failed to force-fail stuck task {task_id} (result={ok})")
                    else:
                        ok = self.update_task_status(
                            task_id,
                            "pending",
                            attempts=attempts,
                            expected_status="processing",
                            check_owner=False,
                        )
                        if ok:
                            requeued += 1
                            logger.info(
                                f"Reset stuck task {task_id} from 'processing' to "
                                f"'pending' (attempt {attempts}/{WORKER_MAX_TASK_ATTEMPTS})"
                            )
                        else:
                            logger.warning(f"Failed to requeue stuck task {task_id} (result={ok})")
                if stuck_tasks:
                    logger.info(
                        f"Recovered {len(stuck_tasks)} stuck tasks "
                        f"(requeued={requeued}, force_failed={force_failed}, "
                        f"skipped_in_batch={skipped})"
                    )
            elif response and response.status_code == 404:
                logger.debug("No /tasks/stuck endpoint, skipping recovery")
            elif response:
                logger.warning(f"Failed to get stuck tasks: {response.status_code}")
        except Exception as e:
            logger.warning(f"Stuck task recovery skipped: {str(e)}")

    def reconcile_dataset_status(self):
        try:
            response = self._request(
                "POST",
                f"{self.dataset_api_url}/datasets/reconcile-status",
                timeout=30,
            )
            if response and response.status_code == 200:
                data = response.json()
                if data.get("finalized"):
                    logger.info(
                        f"Reconciled dataset status: finalized {data['finalized']} "
                        f"of {data.get('scanned')} stuck datasets"
                    )
            elif response and response.status_code == 404:
                logger.debug("No /datasets/reconcile-status endpoint, skipping")
            elif response:
                logger.warning(f"Dataset reconcile failed: {response.status_code}")
        except Exception as e:
            logger.warning(f"Dataset reconcile skipped: {str(e)}")

    def run(self):
        logger.info("Starting Task Worker...")
        logger.info(f"Worker ID: {self.worker_id}")
        logger.info(f"Polling interval: {self.poll_interval} seconds")
        logger.info(f"Batch size: {self.batch_size}")
        logger.info(f"Task Queue API: {self.task_queue_url}")
        logger.info(f"Agent API: {self.agent_api_url} (using endpoint: {self.agent_api_url}/process_prompt/)")
        logger.info(f"Dataset API: {self.dataset_api_url}")

        stop = self.stop

        def _handle_stop(signum, _frame):
            logger.info("Worker received signal %s, finishing current batch", signum)
            stop.set()

        try:
            import signal
            signal.signal(signal.SIGTERM, _handle_stop)
            signal.signal(signal.SIGINT, _handle_stop)
        except Exception:
            pass

        self.recover_stuck_tasks()
        self.reconcile_dataset_status()
        last_recovery_at = time.monotonic()

        while not stop.is_set():
            try:
                if time.monotonic() - last_recovery_at >= WORKER_RECOVERY_INTERVAL_SECONDS:
                    self.recover_stuck_tasks()
                    self.reconcile_dataset_status()
                    last_recovery_at = time.monotonic()

                claimed_tasks = self.claim_tasks()
                if claimed_tasks:
                    logger.info(f"Claimed {len(claimed_tasks)} tasks")
                    self.process_batch(claimed_tasks)
                    # Interleave: a long generation run must not starve a
                    # user-started revalidation completely.
                    self.process_revalidation_items(max(1, WORKER_REVALIDATION_CONCURRENCY))
                elif self.process_revalidation_items(max(self.batch_size, WORKER_REVALIDATION_CONCURRENCY)):
                    continue
                else:
                    logger.info("No pending tasks found")
                    stop.wait(30)
                    continue
                stop.wait(self.poll_interval)
            except KeyboardInterrupt:
                logger.info("Worker stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {str(e)}")
                stop.wait(self.poll_interval)

        logger.info("Worker stopped")


if __name__ == "__main__":
    worker = TaskWorker()
    worker.run()
