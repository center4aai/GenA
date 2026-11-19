"""
Реестр LLM моделей.
Статичные модели — MODEL_ENDPOINTS (+ GigaChat/YandexGPT, + env default).
Динамические — Application'ы vllm-* из ArgoCD (llm-gitops), вызываемые через
LiteLLM по alias = model.servedName, плюс прочие chat-alias'ы LiteLLM (модели GPU27,
которых нет в ArgoCD). Без пароля ArgoCD его часть пропускается, без LITELLM_API_KEY —
часть LiteLLM.
"""

import os
import re
import json
import time
import logging
import threading
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Callable, List, Optional, Dict, Tuple
from dataclasses import dataclass, field

import httpx

import model_smoke
from config import (
    LLM_MODEL_NAME,
    LLM_URL_MODEL,
    LLM_API_KEY,
    LLM_PROVIDER,
    MODEL_ENDPOINTS_JSON,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_SCOPE,
    GIGACHAT_MODEL,
    YANDEX_CLOUD_API_KEY,
    YANDEX_CLOUD_FOLDER,
    YANDEX_CLOUD_MODEL,
    ARGOCD_URL,
    ARGOCD_USERNAME,
    ARGOCD_PASSWORD,
    LITELLM_BASE_URL,
    LITELLM_API_KEY,
    MODEL_SMOKE_INTERVAL_SEC,
    is_safe_outbound_url,
)

logger = logging.getLogger(__name__)

PROBE_INTERVAL = 60
PROBE_TIMEOUT = 45.0
ARGOCD_TIMEOUT = 10.0
# Пробные chat-запросы (model_smoke) идут параллельно, не больше стольких сразу.
SMOKE_WORKERS = 4

# Схема discovery. Дефолтная модель (первая запись MODEL_ENDPOINTS, config/llm_defaults.env)
# задана статично; её доступность — только health-probe /models с её Bearer-ключом.
# Модели из ArgoCD: Application'ы llm-gitops (vllm-<name>), helm-параметры
# model.servedName/enabled/nodeName/...; Service у них ClusterIP-only, поэтому
# base_url всегда LITELLM_BASE_URL, а model_name = servedName (alias в LiteLLM).
# Без LITELLM_API_KEY такие модели не вызываемы: они видны только в health с reason.
# Остальные alias'ы из GET {LITELLM_BASE_URL}/models (модели GPU27 живут только в
# LiteLLM) регистрируются как litellm-<alias> и без ArgoCD.
ARGOCD_APP_SELECTOR = "app.kubernetes.io/managed-by=llm-gitops"
ARGOCD_APP_PREFIX = "vllm-"

# LiteLLM отдаёт в /models и не-чатовые alias'ы (эмбеддинги, реранкеры, ASR, TTS); тип модели
# есть только в /model/info, которому нужен admin-ключ. Поэтому эвристика: alias
# отбрасывается, если он или любой его сегмент (разделители - _ / : . пробел)
# начинается с одной из подсказок.
LITELLM_NON_CHAT_HINTS = (
    "bge", "e5", "embedding", "rerank", "asr", "whisper", "gigaam", "tts", "silero",
)
_ALIAS_SEGMENT_RE = re.compile(r"[-_/:.\s]+")


def _is_non_chat_alias(alias: str) -> bool:
    segments = [seg for seg in _ALIAS_SEGMENT_RE.split(alias.lower()) if seg]
    return any(seg.startswith(hint) for seg in segments for hint in LITELLM_NON_CHAT_HINTS)


LITELLM_ID_PREFIX = "litellm-"


@dataclass(frozen=True)
class _Listing:
    """Ответ ``GET {base_url}/models``: id моделей или (None, HTTP-статус / None — не ответил)."""

    ids: Optional[frozenset]
    status: Optional[int]

    def missing_reason(self, model_name: str) -> str:
        """Почему модели нет на эндпоинте; пусто — она в списке."""
        if self.ids is None:
            return "сервер недоступен" if self.status is None else f"/models ответил HTTP {self.status}"
        return "" if model_name in self.ids else f"модели {model_name} нет на сервере"


def _served_model_ids(data) -> List[str]:
    """id из ответа /models: ``{"data":[{"id"}]}`` (vLLM, LiteLLM, Ollama /v1) или ``{"models":[{"name"}]}``."""
    if not isinstance(data, dict):
        return []
    rows, key = (data.get("data"), "id") if "data" in data else (data.get("models"), "name")
    return [str(row.get(key, "")) for row in rows or [] if isinstance(row, dict)]


def _bearer(api_key: Optional[str]) -> dict:
    """Key-protected endpoints (vLLM proxy, LiteLLM) answer 401 to an anonymous probe."""
    return {"Authorization": f"Bearer {api_key}"} if api_key and api_key != "none" else {}


@dataclass
class LLMModelConfig:
    id: str
    name: str
    base_url: str
    model_name: str
    api_key: str = "none"
    provider: str = "openai"
    extra: dict = field(default_factory=dict)


def resolve_endpoint_api_key(ep: dict) -> Optional[dict]:
    """``api_key_env`` names the env variable with the key, so secrets stay out
    of MODEL_ENDPOINTS (it lives in the repo). Unset variable → None (the
    registry keeps the entry as unavailable with a reason)."""
    env_name = ep.get("api_key_env")
    if not env_name:
        return ep
    ep = {k: v for k, v in ep.items() if k != "api_key_env"}
    key = os.getenv(str(env_name), "").strip()
    if not key:
        return None
    ep["api_key"] = key
    return ep


def _litellm_alias_model(alias: str, litellm_url: str, name: str = "") -> LLMModelConfig:
    return LLMModelConfig(
        id=f"{LITELLM_ID_PREFIX}{alias}",
        name=name or f"{alias} (LiteLLM)",
        base_url=litellm_url,
        model_name=alias,
        api_key=LITELLM_API_KEY,
        provider="openai",
        extra={},
    )


def _litellm_model_by_id(model_id: str) -> Optional[LLMModelConfig]:
    """litellm-<alias> → модель LiteLLM, пока список alias'ов LiteLLM неизвестен.

    id однозначно задаёт alias, поэтому задача с таким id не уходит молча на
    default до первого probe или когда LiteLLM не ответил на /models.
    """
    alias = model_id[len(LITELLM_ID_PREFIX):] if model_id.startswith(LITELLM_ID_PREFIX) else ""
    if not alias or not LITELLM_API_KEY or _is_non_chat_alias(alias):
        return None
    url = LITELLM_BASE_URL.rstrip("/")
    return _litellm_alias_model(alias, url) if is_safe_outbound_url(url) else None


def _argocd_app_reason(served: str, info: dict, litellm_error: str, aliases: Optional[set]) -> str:
    """Почему ArgoCD-модель не вызываема; пусто — вызываема."""
    if info.get("health") != "Healthy" or info.get("sync") != "Synced":
        return f"ArgoCD: health={info.get('health') or '?'}, sync={info.get('sync') or '?'}"
    if litellm_error:
        return litellm_error
    if aliases is not None and served not in aliases:
        return f"LiteLLM не отдаёт alias {served}"
    return ""


def _argocd_models(
    apps: Dict[str, dict],
    static_pairs: set,
    litellm_url: str,
    litellm_error: str,
    aliases: Optional[set],
) -> Tuple[Dict[str, LLMModelConfig], Dict[str, Tuple[str, str]]]:
    """ArgoCD-приложения → (вызываемые, недоступные с reason)."""
    discovered: Dict[str, LLMModelConfig] = {}
    unavailable: Dict[str, Tuple[str, str]] = {}
    for served, info in apps.items():
        if not info.get("enabled", True):
            # выключена в ArgoCD: не показываем, а её alias LiteLLM не регистрируем
            continue
        if (litellm_url, served) in static_pairs:
            # уже задана статично в MODEL_ENDPOINTS — не дублируем
            continue
        node = info.get("node") or ""
        name = f"{served} (LiteLLM, {node})" if node else f"{served} (LiteLLM)"
        model = _litellm_alias_model(served, litellm_url, name=name)
        reason = _argocd_app_reason(served, info, litellm_error, aliases)
        if reason:
            unavailable[model.id] = (name, reason)
        else:
            discovered[model.id] = model
    return discovered, unavailable


def _litellm_only_models(
    aliases: Optional[set], apps: Dict[str, dict], static_pairs: set, litellm_url: str
) -> List[LLMModelConfig]:
    """Alias'ы LiteLLM вне ArgoCD (модели GPU27): статичные и ArgoCD-записи не дублируем,
    не-чатовые отбрасываем эвристикой LITELLM_NON_CHAT_HINTS."""
    return [
        _litellm_alias_model(alias, litellm_url)
        for alias in sorted(aliases or ())
        if alias
        and alias not in apps
        and (litellm_url, alias) not in static_pairs
        and not _is_non_chat_alias(alias)
    ]


class ModelsRegistry:
    def __init__(
        self,
        smoke_interval: Optional[int] = None,
        smoke_executor: Optional[Executor] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._static_models: Dict[str, LLMModelConfig] = {}
        self._probed_models: Dict[str, LLMModelConfig] = {}
        # id -> reason: вызываемые модели, которых нет в /models своего эндпоинта
        # (или эндпоинт не ответил). Пересчитывается каждым probe.
        self._probe_reasons: Dict[str, str] = {}
        # Пробный chat-запрос: id -> (clock() запуска, причина последнего провала или "").
        # Запись ставится при запуске, поэтому повтор раньше интервала невозможен
        # и при перезапуске потока probe; результат дописывает поток smoke.
        self._smoke: Dict[str, Tuple[float, str]] = {}
        self._smoke_interval = MODEL_SMOKE_INTERVAL_SEC if smoke_interval is None else smoke_interval
        self._smoke_executor = smoke_executor or ThreadPoolExecutor(
            max_workers=SMOKE_WORKERS, thread_name_prefix="model-smoke"
        )
        self._clock = clock
        # id -> (name, reason): модели, которые нельзя вызвать (нет ключа и т.п.).
        # Статичные — из MODEL_ENDPOINTS, discovered — обновляются каждым probe.
        self._static_unavailable: Dict[str, Tuple[str, str]] = {}
        self._probed_unavailable: Dict[str, Tuple[str, str]] = {}
        # "default" повторяет запись MODEL_ENDPOINTS → в списке не показываем
        self._hide_default: bool = False
        # (base_url, model_name) записей MODEL_ENDPOINTS без ключа — для дедупа "default"
        self._unavailable_endpoints: List[Tuple[str, str]] = []
        self._litellm_listed = False  # последний probe получил alias'ы LiteLLM
        self._last_probe_ts: float = 0.0
        self._lock = threading.Lock()
        self._load_from_env()
        self._start_probe_poller()

    def _load_from_env(self):
        endpoints_json = os.getenv("MODEL_ENDPOINTS") or MODEL_ENDPOINTS_JSON
        if endpoints_json:
            try:
                endpoints = json.loads(endpoints_json)
                for idx, raw_ep in enumerate(endpoints):
                    ep = resolve_endpoint_api_key(raw_ep)
                    if ep is None:
                        self._register_missing_key(idx, raw_ep)
                        continue
                    model = LLMModelConfig(**ep)
                    if model.base_url and not is_safe_outbound_url(model.base_url):
                        logger.warning(
                            f"Skipped MODEL_ENDPOINTS entry with unsafe base_url: {model.id}"
                        )
                        continue
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

        if LLM_URL_MODEL and LLM_MODEL_NAME:
            if is_safe_outbound_url(LLM_URL_MODEL):
                provider = (LLM_PROVIDER or "openai").lower().strip()
                # The env default is usually the same deployment as one of the
                # MODEL_ENDPOINTS entries. Reuse that entry's provider kwargs
                # (e.g. reasoning_effort for a thinking model): tasks without an
                # explicit model id take this path, and losing the kwargs here
                # silently turned reasoning back on for them.
                twin = self._matching_endpoint(LLM_URL_MODEL, LLM_MODEL_NAME)
                # Тот же деплой уже есть в MODEL_ENDPOINTS (вызываемый или без
                # ключа) — "default" остаётся для get_model/get_default, но не
                # дублируется в /models/ и /models/health/.
                self._hide_default = twin is not None or self._matching_unavailable(
                    LLM_URL_MODEL, LLM_MODEL_NAME
                )
                self._static_models.setdefault(
                    "default",
                    LLMModelConfig(
                        id="default",
                        name=f"{LLM_MODEL_NAME} (env)",
                        base_url=LLM_URL_MODEL,
                        model_name=LLM_MODEL_NAME,
                        api_key=LLM_API_KEY or "none",
                        provider=provider,
                        extra=dict(twin.extra) if twin else {},
                    ),
                )
                logger.info(
                    f"Registered default env model: {LLM_MODEL_NAME} via {LLM_PROVIDER}"
                )
            else:
                logger.warning(
                    "Skipped default env LLM_URL_MODEL: unsafe or blocked URL"
                )

    def _matching_endpoint(self, base_url: str, model_name: str) -> Optional[LLMModelConfig]:
        """The MODEL_ENDPOINTS entry that serves the same model at the same URL."""
        url = base_url.rstrip("/")
        for model in self._static_models.values():
            if model.model_name == model_name and model.base_url.rstrip("/") == url:
                return model
        return None

    def _register_missing_key(self, idx: int, ep: dict) -> None:
        """Entry with an unset api_key_env: not callable, but visible in health."""
        mid = str(ep.get("id") or f"endpoint-{idx}")
        env_name = ep.get("api_key_env")
        reason = f"не задана переменная {env_name}"
        self._static_unavailable[mid] = (str(ep.get("name") or mid), reason)
        # Первый элемент — дефолт в UI: без него фронт молча съезжает на следующую модель.
        log = logger.error if idx == 0 else logger.warning
        log(f"MODEL_ENDPOINTS entry {mid} is unavailable: {env_name} is not set")
        self._unavailable_endpoints.append(
            (str(ep.get("base_url") or "").rstrip("/"), str(ep.get("model_name") or ""))
        )

    def _matching_unavailable(self, base_url: str, model_name: str) -> bool:
        url = base_url.rstrip("/")
        return (url, model_name) in self._unavailable_endpoints

    def _merged_models(self) -> Dict[str, LLMModelConfig]:
        """Вызываемые модели в порядке показа (под self._lock)."""
        merged = {**self._static_models, **self._probed_models}
        if self._hide_default:
            merged.pop("default", None)
        return merged

    # ── Public API ──

    def list_models(self) -> List[LLMModelConfig]:
        with self._lock:
            merged = self._merged_models()
        return list(merged.values())

    def get_model(self, model_id: str) -> Optional[LLMModelConfig]:
        with self._lock:
            found = self._static_models.get(model_id) or self._probed_models.get(model_id)
            listed = self._litellm_listed
        # Список alias'ов получен — решает discovery; иначе id разрешается сам.
        return found if found or listed else _litellm_model_by_id(model_id)

    def get_default(self) -> Optional[LLMModelConfig]:
        """Возвращает env default или первую доступную модель."""
        with self._lock:
            if "default" in self._static_models:
                return self._static_models["default"]
            merged = {**self._static_models, **self._probed_models}
        return next(iter(merged.values()), None) if merged else None

    def get_health(self) -> List[dict]:
        """Health всех известных моделей из последнего probe.

        Элемент: ``{id, name, available}``; у недоступных моделей (нет ключа,
        ArgoCD/LiteLLM не готовы, модели нет в /models, провалился пробный
        запрос) дополнительно ``reason``.
        """
        with self._lock:
            merged = self._merged_models()
            reasons = {mid: self._unavailable_reason(mid) for mid in merged}
            unavailable = {**self._static_unavailable, **self._probed_unavailable}
        result = []
        for mid, model in merged.items():
            row = {"id": model.id, "name": model.name, "available": not reasons[mid]}
            if reasons[mid]:
                row["reason"] = reasons[mid]
            result.append(row)
        for mid, (name, reason) in unavailable.items():
            if mid in merged:
                continue
            result.append({
                "id": mid,
                "name": name,
                "available": False,
                "reason": reason,
            })
        return result

    def _unavailable_reason(self, model_id: str) -> str:
        """Причина недоступности вызываемой модели (под self._lock); пусто — доступна."""
        return self._probe_reasons.get(model_id) or self._smoke.get(model_id, (0.0, ""))[1]

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
        # Один GET /models на (base_url, ключ) за probe: его делят статичные записи и LiteLLM.
        listings: Dict[Tuple[str, str], _Listing] = {}
        apps = self._discover_services_from_argocd()
        discovered, unavailable = self._resolve_litellm_models(apps, listings)

        # Статичные openai-совместимые модели: доступна, только если её model_name
        # есть в /models эндпоинта (200 без неё — модель удалена с сервера).
        with self._lock:
            static_snapshot = list(self._static_models.items())
        reasons = {
            mid: reason
            for mid, model in static_snapshot
            if mid not in discovered and (reason := self._listing_reason(model, listings))
        }

        with self._lock:
            added = set(discovered) - set(self._probed_models)
            removed = set(self._probed_models) - set(discovered)
            self._probed_models = discovered
            self._probed_unavailable = unavailable
            self._probe_reasons = reasons
            self._last_probe_ts = time.time()
            shown = list(self._merged_models().values())
        self._schedule_smoke(shown)

        if added:
            logger.info(f"Probe discovered models: {added}")
        if removed:
            logger.info(f"Probe lost models: {removed}")

    def _listing_reason(self, model: LLMModelConfig, listings: Dict[Tuple[str, str], _Listing]) -> str:
        if model.provider in ("gigachat", "yandex") or not model.base_url:
            # у облачных провайдеров нет дешёвого /models — их проверяет только smoke
            return ""
        base = model.base_url.rstrip("/")
        probe_base = base if base.endswith("/v1") else f"{base}/v1"
        return self._listing(listings, probe_base, model.api_key).missing_reason(model.model_name)

    def _listing(
        self, listings: Dict[Tuple[str, str], _Listing], base_url: str, api_key: Optional[str]
    ) -> _Listing:
        key = (base_url, api_key or "")
        if key not in listings:
            listings[key] = self._fetch_listing(base_url, api_key)
        return listings[key]

    def _fetch_listing(self, base_url: str, api_key: Optional[str]) -> _Listing:
        if not is_safe_outbound_url(base_url):
            logger.warning(f"Blocked probe to unsafe URL: {base_url}")
            return _Listing(None, None)
        try:
            with httpx.Client(timeout=PROBE_TIMEOUT) as client:
                resp = client.get(f"{base_url}/models", headers=_bearer(api_key))
            if resp.status_code != 200:
                return _Listing(None, resp.status_code)
            return _Listing(frozenset(_served_model_ids(resp.json())), 200)
        except Exception as e:  # недоступность эндпоинта — результат probe
            logger.debug(f"Model listing {base_url} unavailable: {e}")
            return _Listing(None, None)

    # ── Smoke: пробный chat-запрос раз в MODEL_SMOKE_INTERVAL_SEC ──

    def _schedule_smoke(self, models: List[LLMModelConfig]) -> None:
        """Запускает smoke моделей, у которых интервал истёк; не ждёт результата."""
        if self._smoke_interval <= 0:
            return
        now = self._clock()
        with self._lock:
            due = [m for m in models if self._smoke_due(m.id, now)]
            for m in due:
                self._smoke[m.id] = (now, self._smoke.get(m.id, (0.0, ""))[1])
        for m in due:
            self._smoke_executor.submit(self._run_smoke, m)

    def _smoke_due(self, model_id: str, now: float) -> bool:
        last = self._smoke.get(model_id)
        return last is None or now - last[0] >= self._smoke_interval

    def _run_smoke(self, model: LLMModelConfig) -> None:
        reason = model_smoke.run_smoke(model)
        with self._lock:
            started = self._smoke.get(model.id, (self._clock(), ""))[0]
            self._smoke[model.id] = (started, reason)

    # ── ArgoCD + LiteLLM discovery ──

    def _resolve_litellm_models(
        self, apps: Dict[str, dict], listings: Dict[Tuple[str, str], _Listing]
    ) -> Tuple[Dict[str, LLMModelConfig], Dict[str, Tuple[str, str]]]:
        """ArgoCD-приложения + alias'ы LiteLLM → (вызываемые, недоступные с reason).

        Без ArgoCD (``apps`` пуст) LiteLLM всё равно опрашивается, если задан ключ.
        """
        if not apps and not LITELLM_API_KEY:
            return {}, {}

        with self._lock:
            # и недоступные записи (без ключа): иначе в health модель видна дважды
            static_pairs = {
                (m.base_url.rstrip("/"), m.model_name) for m in self._static_models.values()
            } | set(self._unavailable_endpoints)
        litellm_url = LITELLM_BASE_URL.rstrip("/")
        aliases, litellm_error = self._litellm_listing(litellm_url, listings)
        with self._lock:
            self._litellm_listed = aliases is not None

        discovered, unavailable = _argocd_models(
            apps, static_pairs, litellm_url, litellm_error, aliases
        )
        for model in _litellm_only_models(aliases, apps, static_pairs, litellm_url):
            discovered[model.id] = model
        return discovered, unavailable

    def _litellm_listing(
        self, litellm_url: str, listings: Dict[Tuple[str, str], _Listing]
    ) -> Tuple[Optional[set], str]:
        """(alias'ы LiteLLM, "") или (None, причина), если список не получен."""
        if not litellm_url:
            return None, "LITELLM_BASE_URL не задан"
        if not LITELLM_API_KEY:
            return None, "LITELLM_API_KEY не задан"
        if not is_safe_outbound_url(litellm_url):
            logger.warning("Blocked LiteLLM probe: unsafe LITELLM_BASE_URL")
            return None, "LITELLM_BASE_URL заблокирован"
        listing = self._listing(listings, litellm_url, LITELLM_API_KEY)
        if listing.ids is None:
            if listing.status is None:
                return None, "LiteLLM недоступен"
            return None, f"LiteLLM ответил HTTP {listing.status}"
        return set(listing.ids), ""

    def _discover_services_from_argocd(self) -> Dict[str, dict]:
        """``servedName -> {enabled, node, health, sync, max_model_len, thinking}``.

        Выключенные (model.enabled != true) тоже попадают в dict с ``enabled=False`` —
        чтобы их alias, оставшийся в LiteLLM, не регистрировался как отдельная модель.
        Пустой dict, если ArgoCD не настроен или недоступен.
        """
        if not ARGOCD_URL or not ARGOCD_PASSWORD:
            return {}
        if not is_safe_outbound_url(ARGOCD_URL):
            logger.warning("Blocked ArgoCD discovery: unsafe ARGOCD_URL")
            return {}
        base = ARGOCD_URL.rstrip("/")
        try:
            with httpx.Client(timeout=ARGOCD_TIMEOUT) as client:
                token = self._argocd_auth(client)
                if not token:
                    logger.debug("ArgoCD login failed")
                    return {}
                headers = {"Authorization": f"Bearer {token}"}
                items: list = []
                resp = client.get(
                    f"{base}/api/v1/applications",
                    params={"selector": ARGOCD_APP_SELECTOR},
                    headers=headers,
                )
                if resp.status_code == 200:
                    items = resp.json().get("items") or []
                if not items:
                    # нет label managed-by → все приложения vllm-*
                    resp = client.get(f"{base}/api/v1/applications", headers=headers)
                    if resp.status_code != 200:
                        return {}
                    items = [
                        app for app in (resp.json().get("items") or [])
                        if str(app.get("metadata", {}).get("name", "")).startswith(ARGOCD_APP_PREFIX)
                    ]
        except Exception as e:
            logger.debug(f"ArgoCD unavailable: {e}")
            return {}

        apps: Dict[str, dict] = {}
        for app in items:
            params = _helm_parameters(app)
            served = params.get("model.servedName", "").strip()
            if not served:
                continue
            status = app.get("status") or {}
            apps[served] = {
                "enabled": params.get("model.enabled", "").strip().lower() == "true",
                "node": params.get("model.nodeName", "").strip(),
                "max_model_len": params.get("model.maxModelLen", "").strip(),
                "thinking": params.get("model.thinking", "").strip(),
                "health": (status.get("health") or {}).get("status", ""),
                "sync": (status.get("sync") or {}).get("status", ""),
            }
        if apps:
            logger.debug(f"ArgoCD discovered {len(apps)} models: {list(apps)}")
        return apps

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
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            return [await self._discover_one(client, model) for model in self.list_models()]

    async def _discover_one(self, client: httpx.AsyncClient, model: LLMModelConfig) -> dict:
        row = {
            "id": model.id, "name": model.name,
            "base_url": model.base_url, "model_name": model.model_name,
            "provider": model.provider,
        }
        if model.provider in ("gigachat", "yandex"):
            # /models у облака нет — доступность по последнему smoke
            with self._lock:
                failed = self._unavailable_reason(model.id)
            return {**row, "available": not failed, "served_models": [model.model_name]}
        served = await self._served_models(client, model)
        return {**row, "available": model.model_name in served, "served_models": served}

    async def _served_models(self, client: httpx.AsyncClient, model: LLMModelConfig) -> List[str]:
        base = model.base_url.rstrip("/")
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        if not is_safe_outbound_url(url):
            logger.warning(f"Blocked discover probe to unsafe URL: {url}")
            return []
        try:
            resp = await client.get(url, headers=_bearer(model.api_key))
            return _served_model_ids(resp.json()) if resp.status_code == 200 else []
        except Exception as e:  # недоступный эндпоинт — available: false
            logger.warning(f"Cannot reach {model.id}: {e}")
            return []


def _helm_parameters(app: dict) -> Dict[str, str]:
    """helm.parameters ArgoCD-приложения (spec.source или spec.sources[]) как dict."""
    spec = app.get("spec") or {}
    sources = [spec.get("source") or {}] + list(spec.get("sources") or [])
    out: Dict[str, str] = {}
    for src in sources:
        for p in ((src or {}).get("helm") or {}).get("parameters") or []:
            if isinstance(p, dict) and p.get("name"):
                out[str(p["name"])] = str(p.get("value", ""))
    return out


registry = ModelsRegistry()
