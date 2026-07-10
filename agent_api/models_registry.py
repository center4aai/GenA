"""
Реестр LLM моделей.
Загружает дефолтную модель из env и автоматически обнаруживает
модели через ArgoCD + Traefik probe.
Если ArgoCD недоступен — работает по KNOWN_SERVICES.
"""

import os
import json
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass, field

import httpx

from config import (
    LLM_MODEL_NAME, LLM_URL_MODEL, LLM_API_KEY,
    GIGACHAT_CREDENTIALS, GIGACHAT_SCOPE, GIGACHAT_MODEL,
    YANDEX_CLOUD_API_KEY, YANDEX_CLOUD_FOLDER, YANDEX_CLOUD_MODEL,
)

logger = logging.getLogger(__name__)

TRAEFIK_HOST = os.getenv("TRAEFIK_HOST", "")
TRAEFIK_PORT = int(os.getenv("TRAEFIK_PORT", "27373"))
PROBE_INTERVAL = 60
PROBE_TIMEOUT = 45.0

ARGOCD_URL = os.getenv("ARGOCD_URL", "")
ARGOCD_USERNAME = os.getenv("ARGOCD_USERNAME", "")
ARGOCD_PASSWORD = os.getenv("ARGOCD_PASSWORD", "")

# Fallback: если ArgoCD недоступен, пробуем эти сервисы.
KNOWN_SERVICES = [
    ("llamacpp-qwen3-5-27b-ws5", "llama.cpp"),
    ("vllm-tinyllama-1-1b-v1", "vLLM"),
    ("vllm-qwen3-1-7b-v2", "vLLM"),
    ("vllm-phi4-mini-v3", "vLLM"),
]


@dataclass
class LLMModelConfig:
    id: str
    name: str
    base_url: str
    model_name: str
    api_key: str = "none"
    provider: str = "openai"
    extra: dict = field(default_factory=dict)


class ModelsRegistry:
    def __init__(self):
        self._static_models: Dict[str, LLMModelConfig] = {}
        self._probed_models: Dict[str, LLMModelConfig] = {}
        self._health: Dict[str, bool] = {}
        self._last_probe_ts: float = 0.0
        self._lock = threading.Lock()
        self._load_from_env()
        self._start_probe_poller()

    def _load_from_env(self):
        endpoints_json = os.getenv("MODEL_ENDPOINTS", "")
        if endpoints_json:
            try:
                endpoints = json.loads(endpoints_json)
                for ep in endpoints:
                    model = LLMModelConfig(**ep)
                    self._static_models[model.id] = model
                    logger.info(f"Loaded model from env: {model.id} -> {model.name}")
            except Exception as e:
                logger.error(f"Failed to parse MODEL_ENDPOINTS: {e}")

        if GIGACHAT_CREDENTIALS:
            self._static_models["gigachat"] = LLMModelConfig(
                id="gigachat",
                name=f"GigaChat ({GIGACHAT_MODEL})",
                base_url="",
                model_name=GIGACHAT_MODEL,
                api_key=GIGACHAT_CREDENTIALS,
                provider="gigachat",
                extra={"scope": GIGACHAT_SCOPE},
            )
            logger.info(f"Registered GigaChat model: {GIGACHAT_MODEL}")

        if YANDEX_CLOUD_API_KEY and YANDEX_CLOUD_FOLDER:
            self._static_models["yandexgpt"] = LLMModelConfig(
                id="yandexgpt",
                name=f"YandexGPT ({YANDEX_CLOUD_MODEL})",
                base_url="https://ai.api.cloud.yandex.net/v1",
                model_name=YANDEX_CLOUD_MODEL,
                api_key=YANDEX_CLOUD_API_KEY,
                provider="yandex",
                extra={"folder_id": YANDEX_CLOUD_FOLDER},
            )
            logger.info(f"Registered YandexGPT model: {YANDEX_CLOUD_MODEL}")

    # ── Public API ──

    def list_models(self) -> List[LLMModelConfig]:
        with self._lock:
            merged = {**self._static_models, **self._probed_models}
        return list(merged.values())

    def get_model(self, model_id: str) -> Optional[LLMModelConfig]:
        with self._lock:
            return self._static_models.get(model_id) or self._probed_models.get(model_id)

    def get_default(self) -> Optional[LLMModelConfig]:
        """Возвращает первую доступную модель как дефолтную."""
        models = self.list_models()
        return models[0] if models else None

    def get_health(self) -> List[dict]:
        """Возвращает health-статус всех известных моделей из последнего probe."""
        with self._lock:
            merged = {**self._static_models, **self._probed_models}
            health_copy = dict(self._health)
        result = []
        for mid, model in merged.items():
            result.append({
                "id": model.id,
                "name": model.name,
                "available": health_copy.get(mid, True),
            })
        for mid, available in health_copy.items():
            if mid not in merged and not available:
                result.append({
                    "id": mid,
                    "name": mid,
                    "available": False,
                })
        return result

    # ── Auto-discovery ──

    def _start_probe_poller(self):
        t = threading.Thread(target=self._probe_loop, daemon=True)
        t.start()
        logger.info(f"Model probe started, interval={PROBE_INTERVAL}s")

    def _probe_loop(self):
        self._do_probe()
        while True:
            time.sleep(PROBE_INTERVAL)
            try:
                self._do_probe()
            except Exception as e:
                logger.error(f"Probe error: {e}")

    def _do_probe(self):
        services = self._discover_services_from_argocd()
        if not services:
            services = {prefix: engine for prefix, engine in KNOWN_SERVICES}
            logger.debug("ArgoCD unavailable, using KNOWN_SERVICES fallback")

        discovered: Dict[str, LLMModelConfig] = {}
        health: Dict[str, bool] = {}

        def probe_one(svc_prefix: str, engine: str) -> Tuple[str, str, str, Optional[str]]:
            base_url = f"http://{TRAEFIK_HOST}:{TRAEFIK_PORT}/{svc_prefix}/v1"
            model_name = self._probe_model(base_url)
            return svc_prefix, engine, base_url, model_name

        with ThreadPoolExecutor(max_workers=len(services)) as pool:
            futures = {
                pool.submit(probe_one, prefix, engine): prefix
                for prefix, engine in services.items()
            }
            for future in as_completed(futures):
                try:
                    svc_prefix, engine, base_url, model_name = future.result()
                    if model_name:
                        discovered[svc_prefix] = LLMModelConfig(
                            id=svc_prefix,
                            name=f"{model_name} ({engine})",
                            base_url=base_url,
                            model_name=model_name,
                            api_key="none",
                        )
                        health[svc_prefix] = True
                    else:
                        health[svc_prefix] = False
                except Exception as e:
                    logger.warning(f"Probe future error: {e}")
                    prefix = futures[future]
                    health[prefix] = False

        for mid in self._static_models:
            if mid not in health:
                health[mid] = True

        with self._lock:
            added = set(discovered) - set(self._probed_models)
            removed = set(self._probed_models) - set(discovered)
            self._probed_models = discovered
            self._health = health
            self._last_probe_ts = time.time()

        if added:
            logger.info(f"Probe discovered models: {added}")
        if removed:
            logger.info(f"Probe lost models: {removed}")

    def _probe_model(self, base_url: str) -> Optional[str]:
        try:
            with httpx.Client(timeout=PROBE_TIMEOUT) as client:
                resp = client.get(f"{base_url}/models")
            if resp.status_code != 200:
                return None
            data = resp.json()
            if "data" in data and data["data"]:
                return data["data"][0].get("id", "")
            if "models" in data and data["models"]:
                return data["models"][0].get("name", "")
        except Exception:
            pass
        return None

    # ── ArgoCD discovery ──

    def _discover_services_from_argocd(self) -> Dict[str, str]:
        if not ARGOCD_URL or not ARGOCD_PASSWORD:
            return {}
        try:
            with httpx.Client(timeout=10.0, verify=False) as client:
                token = self._argocd_auth(client)
                if not token:
                    return {}

                resp = client.get(
                    f"{ARGOCD_URL.rstrip('/')}/api/v1/applications",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code != 200:
                    return {}

                services: Dict[str, str] = {}
                for app in resp.json().get("items", []):
                    app_name = app.get("metadata", {}).get("name", "")
                    if "vllm" in app_name.lower():
                        engine = "vLLM"
                    elif "llama" in app_name.lower():
                        engine = "llama.cpp"
                    else:
                        continue

                    for res in app.get("status", {}).get("resources", []):
                        if res.get("kind") != "Service":
                            continue
                        svc_name = res.get("name", "")
                        if svc_name:
                            services[svc_name] = engine

                if services:
                    logger.info(f"ArgoCD discovered {len(services)} services: {list(services.keys())}")
                return services
        except Exception as e:
            logger.debug(f"ArgoCD unavailable: {e}")
            return {}

    def _argocd_auth(self, client: httpx.Client) -> Optional[str]:
        resp = client.post(
            f"{ARGOCD_URL.rstrip('/')}/api/v1/session",
            json={"username": ARGOCD_USERNAME, "password": ARGOCD_PASSWORD},
        )
        if resp.status_code == 200:
            return resp.json().get("token")
        return None

    # ── Discover (async, on-demand) ──

    async def discover_models(self) -> List[dict]:
        all_models = self.list_models()
        results = []
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            for model in all_models:
                if model.provider in ("gigachat", "yandex"):
                    results.append({
                        "id": model.id, "name": model.name,
                        "base_url": model.base_url, "model_name": model.model_name,
                        "provider": model.provider,
                        "available": True, "served_models": [model.model_name],
                    })
                    continue

                base = model.base_url.rstrip("/")
                url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
                try:
                    headers = {}
                    if model.api_key and model.api_key != "none":
                        headers["Authorization"] = f"Bearer {model.api_key}"
                    resp = await client.get(url, headers=headers)
                    available = resp.status_code == 200
                    served_models = []
                    if available:
                        data = resp.json()
                        if "data" in data:
                            served_models = [m.get("id", "") for m in data["data"]]
                        elif "models" in data:
                            served_models = [m.get("name", "") for m in data["models"]]
                    results.append({
                        "id": model.id, "name": model.name,
                        "base_url": model.base_url, "model_name": model.model_name,
                        "provider": model.provider,
                        "available": available, "served_models": served_models,
                    })
                except Exception as e:
                    logger.warning(f"Cannot reach {model.id}: {e}")
                    results.append({
                        "id": model.id, "name": model.name,
                        "base_url": model.base_url, "model_name": model.model_name,
                        "provider": model.provider,
                        "available": False, "served_models": [],
                    })
        return results


registry = ModelsRegistry()
