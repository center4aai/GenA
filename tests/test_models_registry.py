"""ModelsRegistry: the env default model must carry the provider kwargs of its MODEL_ENDPOINTS twin."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from import_paths import prefer_agent_api

prefer_agent_api()

import models_registry as mod  # noqa: E402

URL = "http://1.2.3.4:11434/v1"
ENDPOINTS = [
    {
        "id": "qwen3-6-27b",
        "name": "Qwen 3.6 27B (Ollama)",
        "base_url": URL,
        "model_name": "qwen3.6:27b",
        "api_key": "none",
        "extra": {"reasoning_effort": "none"},
    },
    {
        "id": "t-pro-32b",
        "name": "T-pro",
        "base_url": URL,
        "model_name": "t-tech/T-pro-it-2.1:q4_K_M",
        "api_key": "none",
    },
]


def _registry(monkeypatch, *, url: str, model_name: str):
    monkeypatch.setenv("MODEL_ENDPOINTS", json.dumps(ENDPOINTS))
    monkeypatch.setattr(mod, "LLM_URL_MODEL", url)
    monkeypatch.setattr(mod, "LLM_MODEL_NAME", model_name)
    monkeypatch.setattr(mod, "GIGACHAT_CREDENTIALS", "")
    monkeypatch.setattr(mod, "YANDEX_CLOUD_API_KEY", "")
    with patch.object(mod.ModelsRegistry, "_start_probe_poller", lambda self: None):
        return mod.ModelsRegistry()


def test_default_env_model_inherits_extra_from_its_endpoint_twin(monkeypatch):
    """Tasks without a model id use "default"; it must not re-enable reasoning."""
    registry = _registry(monkeypatch, url=URL, model_name="qwen3.6:27b")
    default = registry.get_model("default")
    assert default is not None
    assert default.extra == {"reasoning_effort": "none"}
    # a copy: mutating the default must not leak into the endpoint entry
    assert default.extra is not registry.get_model("qwen3-6-27b").extra
    assert registry.get_default().id == "default"


def test_default_env_model_matches_url_ignoring_trailing_slash(monkeypatch):
    registry = _registry(monkeypatch, url=URL + "/", model_name="qwen3.6:27b")
    assert registry.get_model("default").extra == {"reasoning_effort": "none"}


def test_default_env_model_without_twin_keeps_empty_extra(monkeypatch):
    registry = _registry(monkeypatch, url=URL, model_name="mistral-small3.2:24b")
    assert registry.get_model("default").extra == {}


def test_twin_with_no_extra_yields_empty_extra(monkeypatch):
    registry = _registry(monkeypatch, url=URL, model_name="t-tech/T-pro-it-2.1:q4_K_M")
    assert registry.get_model("default").extra == {}


GEMMA = {
    "id": "gemma4-31b-ws1",
    "name": "Gemma (vLLM ws1)",
    "base_url": "http://1.2.3.5:8000/v1",
    "model_name": "gemma4-31b-it",
    "api_key_env": "GEMMA_VLLM_API_KEY",
}


def _registry_with(monkeypatch, endpoints):
    monkeypatch.setenv("MODEL_ENDPOINTS", json.dumps(endpoints))
    monkeypatch.setattr(mod, "LLM_URL_MODEL", "")
    monkeypatch.setattr(mod, "LLM_MODEL_NAME", "")
    monkeypatch.setattr(mod, "GIGACHAT_CREDENTIALS", "")
    monkeypatch.setattr(mod, "YANDEX_CLOUD_API_KEY", "")
    with patch.object(mod.ModelsRegistry, "_start_probe_poller", lambda self: None):
        return mod.ModelsRegistry()


def test_api_key_env_takes_the_key_from_that_variable(monkeypatch):
    monkeypatch.setenv("GEMMA_VLLM_API_KEY", "sk-secret")
    registry = _registry_with(monkeypatch, ENDPOINTS + [GEMMA])
    gemma = registry.get_model("gemma4-31b-ws1")
    assert gemma is not None
    assert gemma.api_key == "sk-secret"
    assert registry.get_model("qwen3-6-27b").api_key == "none"


def test_api_key_env_unset_skips_only_that_entry(monkeypatch):
    monkeypatch.delenv("GEMMA_VLLM_API_KEY", raising=False)
    registry = _registry_with(monkeypatch, ENDPOINTS + [GEMMA])
    assert registry.get_model("gemma4-31b-ws1") is None
    assert registry.get_model("qwen3-6-27b") is not None


def test_health_probe_sends_the_endpoint_key(monkeypatch):
    """A key-protected endpoint (LiteLLM) must be probed with its key, else 401 → "down"."""
    monkeypatch.setenv("GEMMA_VLLM_API_KEY", "sk-secret")
    registry = _registry_with(monkeypatch, [GEMMA])
    monkeypatch.setattr(registry, "_discover_services_from_argocd", lambda: {})
    seen = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            seen["auth"] = (headers or {}).get("Authorization")
            ok = seen["auth"] == "Bearer sk-secret"
            return SimpleNamespace(status_code=200 if ok else 401, json=lambda: {"data": [{"id": "gemma4-31b-it"}]})

    monkeypatch.setattr(mod.httpx, "Client", _Client)
    registry._do_probe()
    assert seen["auth"] == "Bearer sk-secret"
    assert {h["id"]: h["available"] for h in registry.get_health()}["gemma4-31b-ws1"] is True


def test_repo_model_endpoints_hold_no_secrets():
    """MODEL_ENDPOINTS lives in the repo: keys only by reference (api_key_env)."""
    text = (_REPO / "config" / "llm_defaults.env").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("MODEL_ENDPOINTS="))
    endpoints = json.loads(line.split("=", 1)[1].strip("'"))
    assert endpoints
    assert {e.get("api_key", "none") for e in endpoints} == {"none"}
    for e in endpoints:
        if "api_key_env" in e:
            assert re.fullmatch(r"[A-Z][A-Z0-9_]*", e["api_key_env"]), e["id"]


# ── handler: default chains must be built from the registry default ──

from types import SimpleNamespace  # noqa: E402

QWEN_DEFAULT = SimpleNamespace(
    id="default",
    model_name="qwen3.6:27b",
    base_url=URL,
    api_key="none",
    provider="openai",
    extra={"reasoning_effort": "none"},
)


@pytest.fixture
def agent_modules():
    """Import ``agent.*`` against agent_api/config.py, then restore sys.modules/sys.path.

    Other test modules load dataset_api (its own ``config``) at collection time, so
    whichever ``config`` is in sys.modules depends on collection order.
    """
    saved_path = list(sys.path)
    saved = {
        k: v for k, v in sys.modules.items()
        if k in ("config", "llm_factory") or k == "agent" or k.startswith("agent.")
    }
    prefer_agent_api()
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for k in [k for k in sys.modules if k in ("config", "llm_factory") or k == "agent" or k.startswith("agent.")]:
            sys.modules.pop(k, None)
        sys.modules.update(saved)


def _handler_with(monkeypatch, default_cfg, calls):
    from agent import handler as handler_mod

    monkeypatch.setattr(mod.registry, "get_default", lambda: default_cfg)
    monkeypatch.setattr(
        handler_mod,
        "create_GENA_runnables_ollama",
        lambda **kw: calls.append(kw) or "runnables",
    )
    return handler_mod.GENAHandler(handler_mod.GENAOptions(mongodb_uri="mongodb://x"))


def test_handler_default_runnables_carry_registry_default_extra(monkeypatch, agent_modules):
    """No model id → default chains; they must still get the endpoint's reasoning_effort."""
    calls = []
    handler = _handler_with(monkeypatch, QWEN_DEFAULT, calls)
    assert len(calls) == 1
    gen = calls[0]["generation_model"]
    assert gen["model_name"] == "qwen3.6:27b"
    assert gen["extra"] == {"reasoning_effort": "none"}
    assert calls[0]["validation_model"] == gen
    # unknown / absent ids fall back to those same default chains
    assert handler._get_runnables(None, None) == "runnables"
    assert len(calls) == 1


def test_handler_without_registry_default_builds_bare_chains(monkeypatch, agent_modules):
    calls = []
    _handler_with(monkeypatch, None, calls)
    assert calls == [{"generation_model": None, "validation_model": None}]


def test_handler_rephrase_uses_default_model_kwargs(monkeypatch, agent_modules):
    """Transform without a model id must also reach the LLM with reasoning disabled."""
    from agent.nodes.dynamic_implementation import rephrase_question as rq

    seen = {}

    def fake_rephrase(questions, **kwargs):
        seen.update(kwargs)
        return questions

    monkeypatch.setattr(rq, "rephrase_questions_dicts", fake_rephrase)
    handler = _handler_with(monkeypatch, QWEN_DEFAULT, [])
    handler.ahandle_rephrase_questions([{"task": "Что такое семейный кодекс?"}], model_id=None)
    assert seen["model_name"] == "qwen3.6:27b"
    assert seen["reasoning_effort"] == "none"
    assert "scope" not in seen


# ── repo defaults: MODEL_ENDPOINTS[0] is the UI default and must use LLM_API_KEY ──


def _defaults_env() -> dict:
    out = {}
    for line in (_REPO / "config" / "llm_defaults.env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'")
    return out


def test_repo_first_endpoint_is_the_default_model_with_llm_api_key():
    env = _defaults_env()
    first = json.loads(env["MODEL_ENDPOINTS"])[0]
    assert first["api_key_env"] == "LLM_API_KEY"
    assert first["base_url"] == env["LLM_URL_MODEL"]
    assert first["model_name"] == env["LLM_MODEL_NAME"]


# ── "default" de-duplication ──

KEYED = {
    "id": "main",
    "name": "Main",
    "base_url": "http://1.2.3.6:8000/v1",
    "model_name": "qwen3.8-27b",
    "api_key_env": "LLM_API_KEY",
}


def _registry_default(monkeypatch, endpoints, *, url, model_name):
    monkeypatch.setenv("MODEL_ENDPOINTS", json.dumps(endpoints))
    monkeypatch.setattr(mod, "LLM_URL_MODEL", url)
    monkeypatch.setattr(mod, "LLM_MODEL_NAME", model_name)
    monkeypatch.setattr(mod, "GIGACHAT_CREDENTIALS", "")
    monkeypatch.setattr(mod, "YANDEX_CLOUD_API_KEY", "")
    with patch.object(mod.ModelsRegistry, "_start_probe_poller", lambda self: None):
        return mod.ModelsRegistry()


def test_default_hidden_from_list_and_health_when_twin_exists(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-main")
    registry = _registry_default(
        monkeypatch, [KEYED], url=KEYED["base_url"] + "/", model_name=KEYED["model_name"]
    )
    assert [m.id for m in registry.list_models()] == ["main"]
    assert [h["id"] for h in registry.get_health()] == ["main"]
    # still resolvable for tasks that carry no model id
    assert registry.get_model("default") is not None
    assert registry.get_default().id == "default"


def test_default_hidden_when_twin_has_no_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    registry = _registry_default(
        monkeypatch, [KEYED], url=KEYED["base_url"], model_name=KEYED["model_name"]
    )
    assert "default" not in [m.id for m in registry.list_models()]
    assert "default" not in [h["id"] for h in registry.get_health()]
    assert registry.get_model("default") is not None


def test_default_listed_when_no_twin(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-main")
    registry = _registry_default(
        monkeypatch, [KEYED], url=KEYED["base_url"], model_name="another-model"
    )
    assert "default" in [m.id for m in registry.list_models()]
    assert "default" in [h["id"] for h in registry.get_health()]


# ── endpoint without its key variable ──


def test_missing_key_entry_is_not_callable_but_visible_in_health(monkeypatch):
    monkeypatch.delenv("GEMMA_VLLM_API_KEY", raising=False)
    registry = _registry_with(monkeypatch, ENDPOINTS + [GEMMA])
    assert "gemma4-31b-ws1" not in [m.id for m in registry.list_models()]
    assert registry.get_model("gemma4-31b-ws1") is None
    entry = next(h for h in registry.get_health() if h["id"] == "gemma4-31b-ws1")
    assert entry["available"] is False
    assert "GEMMA_VLLM_API_KEY" in entry["reason"]
    assert entry["name"] == GEMMA["name"]
    assert all("reason" not in h for h in registry.get_health() if h["id"] != "gemma4-31b-ws1")


def test_missing_key_on_first_entry_logs_error(monkeypatch, caplog):
    monkeypatch.delenv("GEMMA_VLLM_API_KEY", raising=False)
    with caplog.at_level("DEBUG", logger=mod.logger.name):
        _registry_with(monkeypatch, [GEMMA] + ENDPOINTS)
    recs = [r for r in caplog.records if "GEMMA_VLLM_API_KEY" in r.getMessage()]
    assert recs and all(r.levelname == "ERROR" for r in recs)


def test_missing_key_on_other_entry_logs_warning(monkeypatch, caplog):
    monkeypatch.delenv("GEMMA_VLLM_API_KEY", raising=False)
    with caplog.at_level("DEBUG", logger=mod.logger.name):
        _registry_with(monkeypatch, ENDPOINTS + [GEMMA])
    recs = [r for r in caplog.records if "GEMMA_VLLM_API_KEY" in r.getMessage()]
    assert recs and all(r.levelname == "WARNING" for r in recs)


# ── ArgoCD + LiteLLM discovery ──

ARGO = "http://1.2.3.7:8080"
LITELLM = "http://1.2.3.8:4000/v1"


def _app(name, served, enabled="true", health="Healthy", sync="Synced", node="gpu-1", sources=False):
    params = [
        {"name": "model.servedName", "value": served},
        {"name": "model.enabled", "value": enabled},
        {"name": "model.nodeName", "value": node},
    ]
    helm = {"helm": {"parameters": params}}
    spec = {"sources": [{"repoURL": "x"}, helm]} if sources else {"source": helm}
    return {
        "metadata": {"name": name},
        "spec": spec,
        "status": {"health": {"status": health}, "sync": {"status": sync}},
    }


class _FakeHttp:
    """Scripted httpx.Client: ArgoCD session/applications + LiteLLM /models."""

    def __init__(self, selector_items, all_items, litellm_ids, litellm_status=200):
        self.selector_items = selector_items
        self.all_items = all_items
        self.litellm_ids = litellm_ids
        self.litellm_status = litellm_status
        self.calls = []

    def client_cls(self):
        outer = self

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None, **kw):
                outer.calls.append(("POST", url, json, None))
                if url == f"{ARGO}/api/v1/session":
                    return SimpleNamespace(status_code=200, json=lambda: {"token": "tok"})
                return SimpleNamespace(status_code=404, json=lambda: {})

            def get(self, url, params=None, headers=None):
                outer.calls.append(("GET", url, params, headers))
                if url == f"{ARGO}/api/v1/applications":
                    items = outer.selector_items if params else outer.all_items
                    return SimpleNamespace(status_code=200, json=lambda: {"items": items})
                if url == f"{LITELLM}/models":
                    data = {"data": [{"id": i} for i in outer.litellm_ids]}
                    return SimpleNamespace(status_code=outer.litellm_status, json=lambda: data)
                return SimpleNamespace(status_code=404, json=lambda: {})

        return _Client


def _discovery_registry(monkeypatch, fake, *, litellm_key="sk-lt", argo_password="pw"):
    registry = _registry_with(monkeypatch, [])
    monkeypatch.setattr(mod, "ARGOCD_URL", ARGO)
    monkeypatch.setattr(mod, "ARGOCD_USERNAME", "admin")
    monkeypatch.setattr(mod, "ARGOCD_PASSWORD", argo_password)
    monkeypatch.setattr(mod, "LITELLM_BASE_URL", LITELLM)
    monkeypatch.setattr(mod, "LITELLM_API_KEY", litellm_key)
    monkeypatch.setattr(mod.httpx, "Client", fake.client_cls())
    return registry


def test_discovery_registers_healthy_enabled_model_with_litellm_alias(monkeypatch):
    items = [_app("vllm-a", "alpha"), _app("vllm-b", "beta", enabled="false")]
    fake = _FakeHttp(items, [], ["alpha", "beta"])
    registry = _discovery_registry(monkeypatch, fake)
    registry._do_probe()

    model = registry.get_model("litellm-alpha")
    assert model is not None
    assert model.base_url == LITELLM and model.model_name == "alpha"
    assert model.api_key == "sk-lt"
    assert registry.get_model("litellm-beta") is None
    health = {h["id"]: h for h in registry.get_health()}
    assert health["litellm-alpha"]["available"] is True
    assert "litellm-beta" not in health


def test_discovery_login_and_selector(monkeypatch):
    fake = _FakeHttp([_app("vllm-a", "alpha")], [], ["alpha"])
    registry = _discovery_registry(monkeypatch, fake)
    registry._do_probe()
    login = next(c for c in fake.calls if c[0] == "POST")
    assert login[1] == f"{ARGO}/api/v1/session"
    assert login[2] == {"username": "admin", "password": "pw"}
    listing = [c for c in fake.calls if c[1] == f"{ARGO}/api/v1/applications"]
    assert listing[0][2] == {"selector": "app.kubernetes.io/managed-by=llm-gitops"}
    assert listing[0][3]["Authorization"] == "Bearer tok"
    assert len(listing) == 1  # selector answered, no fallback


def test_discovery_falls_back_to_vllm_prefix_on_empty_selector_result(monkeypatch):
    all_items = [_app("vllm-a", "alpha"), _app("other-app", "ignored")]
    fake = _FakeHttp([], all_items, ["alpha", "ignored"])
    registry = _discovery_registry(monkeypatch, fake)
    registry._do_probe()
    ids = {h["id"] for h in registry.get_health()}
    # "ignored" is not an ArgoCD vllm-* app, but it is a chat alias of LiteLLM
    assert ids == {"litellm-alpha", "litellm-ignored"}
    listing = [c for c in fake.calls if c[1] == f"{ARGO}/api/v1/applications"]
    assert len(listing) == 2 and listing[1][2] is None


def test_discovery_reads_helm_parameters_from_sources_list(monkeypatch):
    fake = _FakeHttp([_app("vllm-a", "alpha", sources=True)], [], ["alpha"])
    registry = _discovery_registry(monkeypatch, fake)
    registry._do_probe()
    assert registry.get_model("litellm-alpha") is not None


def test_discovery_unhealthy_or_unsynced_is_unavailable_with_reason(monkeypatch):
    items = [_app("vllm-a", "alpha", health="Progressing"), _app("vllm-b", "beta", sync="OutOfSync")]
    fake = _FakeHttp(items, [], ["alpha", "beta"])
    registry = _discovery_registry(monkeypatch, fake)
    registry._do_probe()
    assert registry.list_models() == []
    health = {h["id"]: h for h in registry.get_health()}
    for mid in ("litellm-alpha", "litellm-beta"):
        assert health[mid]["available"] is False
        assert "ArgoCD" in health[mid]["reason"]


def test_discovery_alias_missing_in_litellm_is_unavailable(monkeypatch):
    fake = _FakeHttp([_app("vllm-a", "alpha")], [], ["something-else"])
    registry = _discovery_registry(monkeypatch, fake)
    registry._do_probe()
    assert registry.get_model("litellm-alpha") is None
    entry = next(h for h in registry.get_health() if h["id"] == "litellm-alpha")
    assert entry["available"] is False and "alpha" in entry["reason"]


def test_discovery_without_litellm_key_only_in_health_with_reason(monkeypatch):
    fake = _FakeHttp([_app("vllm-a", "alpha")], [], ["alpha"])
    registry = _discovery_registry(monkeypatch, fake, litellm_key="")
    registry._do_probe()
    assert registry.get_model("litellm-alpha") is None
    entry = next(h for h in registry.get_health() if h["id"] == "litellm-alpha")
    assert entry["available"] is False
    assert "LITELLM_API_KEY" in entry["reason"]
    # no LiteLLM request without a key
    assert not any(c[1] == f"{LITELLM}/models" for c in fake.calls)


def test_discovery_without_litellm_url_only_in_health_with_reason(monkeypatch):
    fake = _FakeHttp([_app("vllm-a", "alpha")], [], ["alpha"])
    registry = _discovery_registry(monkeypatch, fake)
    monkeypatch.setattr(mod, "LITELLM_BASE_URL", "")
    registry._do_probe()
    assert registry.get_model("litellm-alpha") is None
    entry = next(h for h in registry.get_health() if h["id"] == "litellm-alpha")
    assert entry["available"] is False
    assert "LITELLM_BASE_URL не задан" in entry["reason"]
    assert not any(c[1].endswith("/models") for c in fake.calls)


def test_discovery_without_argocd_password_still_uses_litellm_aliases(monkeypatch):
    fake = _FakeHttp([_app("vllm-a", "alpha")], [], ["alpha"])
    registry = _discovery_registry(monkeypatch, fake, argo_password="")
    registry._do_probe()
    assert [m.id for m in registry.list_models()] == ["litellm-alpha"]
    assert not any(c[1].startswith(ARGO) for c in fake.calls)
    assert [c[1] for c in fake.calls] == [f"{LITELLM}/models"]


def test_discovery_skipped_without_argocd_password_and_litellm_key(monkeypatch):
    fake = _FakeHttp([_app("vllm-a", "alpha")], [], ["alpha"])
    registry = _discovery_registry(monkeypatch, fake, argo_password="", litellm_key="")
    registry._do_probe()
    assert registry.list_models() == []
    assert registry.get_health() == []
    assert fake.calls == []


# ── LiteLLM-only discovery (aliases not covered by ArgoCD) ──


def test_litellm_only_alias_is_registered_without_argocd(monkeypatch):
    fake = _FakeHttp([], [], ["gpu27-chat"])
    registry = _discovery_registry(monkeypatch, fake, argo_password="")
    registry._do_probe()
    model = registry.get_model("litellm-gpu27-chat")
    assert model is not None
    assert model.name == "gpu27-chat (LiteLLM)"
    assert model.base_url == LITELLM
    assert model.model_name == "gpu27-chat"
    assert model.api_key == "sk-lt"
    health = {h["id"]: h for h in registry.get_health()}
    assert health["litellm-gpu27-chat"]["available"] is True
    listing = [c for c in fake.calls if c[1] == f"{LITELLM}/models"]
    assert listing and listing[0][3]["Authorization"] == "Bearer sk-lt"


def test_litellm_only_alias_with_argocd_configured_but_no_apps(monkeypatch):
    fake = _FakeHttp([], [], ["gpu27-chat"])
    registry = _discovery_registry(monkeypatch, fake)
    registry._do_probe()
    assert registry.get_model("litellm-gpu27-chat") is not None


def test_litellm_without_key_makes_no_request_and_registers_nothing(monkeypatch):
    fake = _FakeHttp([], [], ["gpu27-chat"])
    registry = _discovery_registry(monkeypatch, fake, argo_password="", litellm_key="")
    registry._do_probe()
    assert registry.list_models() == []
    assert not any(c[1] == f"{LITELLM}/models" for c in fake.calls)
    assert not any(h["id"].startswith("litellm-") for h in registry.get_health())


def test_litellm_alias_is_not_duplicated_for_argocd_app(monkeypatch):
    fake = _FakeHttp([_app("vllm-a", "alpha")], [], ["alpha", "gpu27-chat"])
    registry = _discovery_registry(monkeypatch, fake)
    registry._do_probe()
    ids = [m.id for m in registry.list_models()]
    assert sorted(ids) == ["litellm-alpha", "litellm-gpu27-chat"]
    assert [h["id"] for h in registry.get_health()].count("litellm-alpha") == 1


def test_litellm_alias_covered_by_static_endpoint_is_not_duplicated(monkeypatch):
    fake = _FakeHttp([], [], ["gpu27-chat", "other-chat"])
    static = {
        "id": "static-chat",
        "name": "Static",
        "base_url": LITELLM,
        "model_name": "gpu27-chat",
        "api_key": "none",
    }
    registry = _discovery_registry(monkeypatch, fake, argo_password="")
    monkeypatch.setenv("MODEL_ENDPOINTS", json.dumps([static]))
    with patch.object(mod.ModelsRegistry, "_start_probe_poller", lambda self: None):
        registry = mod.ModelsRegistry()
    registry._do_probe()
    ids = {m.id for m in registry.list_models()}
    assert "static-chat" in ids
    assert "litellm-other-chat" in ids
    assert "litellm-gpu27-chat" not in ids


def test_argocd_app_covered_by_keyless_static_endpoint_is_not_duplicated(monkeypatch):
    """Без LITELLM_API_KEY статичная запись LiteLLM недоступна, но в health — одна строка."""
    fake = _FakeHttp([_app("vllm-g", "gemma4-31b-it")], [], ["gemma4-31b-it"])
    static = {
        "id": "gemma4-31b-ws1",
        "name": "Gemma",
        "base_url": LITELLM + "/",
        "model_name": "gemma4-31b-it",
        "api_key_env": "LITELLM_API_KEY",
    }
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    registry = _discovery_registry(monkeypatch, fake, litellm_key="")
    monkeypatch.setenv("MODEL_ENDPOINTS", json.dumps([static]))
    with patch.object(mod.ModelsRegistry, "_start_probe_poller", lambda self: None):
        registry = mod.ModelsRegistry()
    registry._do_probe()
    ids = [h["id"] for h in registry.get_health()]
    assert "gemma4-31b-ws1" in ids
    assert "litellm-gemma4-31b-it" not in ids


def test_litellm_alias_of_app_disabled_in_argocd_is_not_registered(monkeypatch):
    fake = _FakeHttp([_app("vllm-b", "beta", enabled="false")], [], ["beta", "gamma"])
    registry = _discovery_registry(monkeypatch, fake)
    registry._do_probe()
    assert registry.get_model("litellm-beta") is None
    assert "litellm-beta" not in {h["id"] for h in registry.get_health()}
    assert registry.get_model("litellm-gamma") is not None


@pytest.mark.parametrize(
    "alias",
    [
        "bge-m3",
        "multilingual-e5-large",
        "qwen3-embedding-8b",
        "bge-reranker-v2",
        "whisper-large-v3",
        "gigaam-v2",
        "asr-ru",
        "tts-muradin",
        "silero-v4",
    ],
)
def test_non_chat_aliases_are_filtered(monkeypatch, alias):
    assert mod._is_non_chat_alias(alias) is True
    fake = _FakeHttp([], [], [alias])
    registry = _discovery_registry(monkeypatch, fake, argo_password="")
    registry._do_probe()
    assert registry.list_models() == []


@pytest.mark.parametrize("alias", ["qwen3.8-27b", "gemma4-31b-it", "t-pro-32b", "basement-chat"])
def test_chat_aliases_pass_the_filter(monkeypatch, alias):
    assert mod._is_non_chat_alias(alias) is False
    fake = _FakeHttp([], [], [alias])
    registry = _discovery_registry(monkeypatch, fake, argo_password="")
    registry._do_probe()
    assert registry.get_model(f"litellm-{alias}") is not None


# Что LiteLLM отдаёт по ключу GenA на 2026-11-05.
PLATFORM_ALIASES = [
    "tts-silero-ru", "tts-muradin", "e5", "qwen3-embedding-4b", "bge-m3",
    "bge-reranker-v2-m3", "asr-gigaam", "asr-whisper", "proof-mistral-nemo-instruct-2407",
    "pentabasis-value-filter-qwen-awq", "gemma4-31b-it", "qwen3.6-35b-a3b-awq",
]


def test_platform_aliases_register_only_chat_models_without_static_twin(monkeypatch):
    fake = _FakeHttp([], [], PLATFORM_ALIASES)
    static = {
        "id": "gemma4-31b-ws1",
        "name": "Gemma",
        "base_url": LITELLM,
        "model_name": "gemma4-31b-it",
        "api_key_env": "LITELLM_API_KEY",
    }
    monkeypatch.setenv("LITELLM_API_KEY", "sk-lt")
    _discovery_registry(monkeypatch, fake, argo_password="")
    monkeypatch.setenv("MODEL_ENDPOINTS", json.dumps([static]))
    with patch.object(mod.ModelsRegistry, "_start_probe_poller", lambda self: None):
        registry = mod.ModelsRegistry()
    registry._do_probe()
    assert {m.id for m in registry.list_models()} == {
        "gemma4-31b-ws1",
        "litellm-proof-mistral-nemo-instruct-2407",
        "litellm-pentabasis-value-filter-qwen-awq",
        "litellm-qwen3.6-35b-a3b-awq",
    }


def test_litellm_id_resolves_before_first_probe(monkeypatch):
    """Задача воркера с litellm-<alias> не должна молча уйти на default до первого probe."""
    fake = _FakeHttp([], [], [])
    registry = _discovery_registry(monkeypatch, fake, argo_password="")
    model = registry.get_model("litellm-qwen3.6-35b-a3b-awq")
    assert model is not None
    assert (model.base_url, model.model_name) == (LITELLM, "qwen3.6-35b-a3b-awq")
    assert model.api_key == "sk-lt"
    assert registry.get_model("litellm-bge-m3") is None
    assert registry.get_model("litellm-") is None
    assert registry.get_model("unknown") is None
    assert registry.list_models() == []  # в выбор модели — только после discovery
    assert fake.calls == []


def test_litellm_id_resolves_while_litellm_listing_fails_but_not_after_it_succeeds(monkeypatch):
    fake = _FakeHttp([], [], ["alpha"])
    fake.litellm_status = 503
    registry = _discovery_registry(monkeypatch, fake, argo_password="")
    registry._do_probe()
    assert registry.get_model("litellm-beta") is not None
    fake.litellm_status = 200
    registry._do_probe()
    assert registry.get_model("litellm-alpha") is not None
    assert registry.get_model("litellm-beta") is None


def test_litellm_id_without_key_is_not_resolved(monkeypatch):
    fake = _FakeHttp([], [], [])
    registry = _discovery_registry(monkeypatch, fake, litellm_key="", argo_password="")
    assert registry.get_model("litellm-qwen3.6-35b-a3b-awq") is None


def test_non_chat_hints_match_segments_by_prefix_with_all_separators():
    assert mod._is_non_chat_alias("org/BGE_M3") is True
    assert mod._is_non_chat_alias("vendor:whisper v3") is True
    assert mod._is_non_chat_alias("x.rerank") is True
    assert mod._is_non_chat_alias("mistral-small3.2:24b") is False


def test_litellm_non_200_registers_nothing(monkeypatch):
    fake = _FakeHttp([], [], ["gpu27-chat"], litellm_status=500)
    registry = _discovery_registry(monkeypatch, fake, argo_password="")
    registry._do_probe()
    assert registry.list_models() == []
    assert not any(h["id"].startswith("litellm-") and h["available"] for h in registry.get_health())


# ── is_safe_outbound_url: OUTBOUND_ALLOWED_CIDRS ──


LAB_CIDR = "10.20.30.0/24"


@pytest.fixture
def agent_config(monkeypatch):
    """Fresh copy of agent_api/config.py with a lab subnet in OUTBOUND_ALLOWED_CIDRS."""
    monkeypatch.setenv("OUTBOUND_ALLOWED_CIDRS", LAB_CIDR)
    return _load_config("_t_agent_config_ssrf", _REPO / "agent_api" / "config.py")


def _with_cidrs(config, monkeypatch, raw):
    monkeypatch.setattr(config, "_OUTBOUND_ALLOWED_NETWORKS", config._parse_allowed_cidrs(raw))


def test_default_allowlist_is_empty(monkeypatch):
    monkeypatch.delenv("OUTBOUND_ALLOWED_CIDRS", raising=False)
    config = _load_config("_t_agent_config_ssrf_default", _REPO / "agent_api" / "config.py")
    assert config.OUTBOUND_ALLOWED_CIDRS == ""
    assert config._OUTBOUND_ALLOWED_NETWORKS == []
    assert config.is_safe_outbound_url("http://10.20.30.63:27465/v1") is False


def test_allowlist_comes_from_env(agent_config):
    assert agent_config.OUTBOUND_ALLOWED_CIDRS == LAB_CIDR
    assert [str(n) for n in agent_config._OUTBOUND_ALLOWED_NETWORKS] == [LAB_CIDR]


def test_discovery_urls_default_to_empty(monkeypatch):
    monkeypatch.delenv("ARGOCD_URL", raising=False)
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    config = _load_config("_t_agent_config_discovery_default", _REPO / "agent_api" / "config.py")
    assert config.ARGOCD_URL == ""
    assert config.LITELLM_BASE_URL == ""


def test_env_var_overrides_default_allowlist(monkeypatch):
    monkeypatch.setenv("OUTBOUND_ALLOWED_CIDRS", "172.16.0.0/12")
    config = _load_config("_t_agent_config_ssrf_env", _REPO / "agent_api" / "config.py")
    assert config.is_safe_outbound_url("http://172.16.5.5/v1") is True
    assert config.is_safe_outbound_url("http://10.20.30.63:27465/v1") is False


@pytest.mark.parametrize(
    "url",
    [
        "http://10.20.30.63:27465/v1",
        "http://10.20.30.1/",
        "https://10.20.30.255/x",
    ],
)
def test_configured_allowlist_allows_lab_subnet(agent_config, url):
    assert agent_config.is_safe_outbound_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://192.168.1.1/",
        "http://10.1.0.1/",
        "http://[::ffff:10.20.30.63]:27465/v1",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data",
        "ftp://10.20.30.63/",
    ],
)
def test_allowlist_still_blocks_other_private_targets(agent_config, url):
    assert agent_config.is_safe_outbound_url(url) is False


def test_public_ip_still_allowed(agent_config):
    assert agent_config.is_safe_outbound_url("http://1.2.3.6:8000/v1") is True


def test_configured_model_hostnames_are_allowed(monkeypatch):
    """Endpoints the operator configured by name (a vLLM or Ollama container on
    the compose network) are trusted like the LiteLLM and ArgoCD hosts; any
    other name is still blocked."""
    monkeypatch.setenv(
        "MODEL_ENDPOINTS",
        json.dumps([
            {"id": "v", "name": "v", "base_url": "http://vllm:8000/v1", "model_name": "m"},
            {"id": "o", "name": "o", "base_url": "http://ollama:11434/v1", "model_name": "m"},
            {"id": "ip", "name": "ip", "base_url": "http://10.20.30.1:8000/v1", "model_name": "m"},
        ]),
    )
    monkeypatch.setenv("LLM_URL_MODEL", "http://llm-gateway:4000/v1")
    monkeypatch.delenv("OUTBOUND_ALLOWED_CIDRS", raising=False)
    config = _load_config("_t_agent_config_model_hosts", _REPO / "agent_api" / "config.py")
    assert config.is_safe_outbound_url("http://vllm:8000/v1") is True
    assert config.is_safe_outbound_url("http://ollama:11434/v1/models") is True
    assert config.is_safe_outbound_url("http://llm-gateway:4000/v1") is True
    assert config.is_safe_outbound_url("http://other-host:8000/v1") is False
    # an IP in MODEL_ENDPOINTS still needs OUTBOUND_ALLOWED_CIDRS
    assert config.is_safe_outbound_url("http://10.20.30.1:8000/v1") is False


def test_malformed_model_endpoints_allow_no_hostnames(monkeypatch):
    monkeypatch.setenv("MODEL_ENDPOINTS", "[{not json")
    monkeypatch.setenv("LLM_URL_MODEL", "http://203.0.113.5:8000/v1")
    config = _load_config("_t_agent_config_model_hosts_bad", _REPO / "agent_api" / "config.py")
    assert config.is_safe_outbound_url("http://vllm:8000/v1") is False


def test_empty_allowlist_blocks_lab_subnet(agent_config, monkeypatch):
    _with_cidrs(agent_config, monkeypatch, "")
    assert agent_config._OUTBOUND_ALLOWED_NETWORKS == []
    assert agent_config.is_safe_outbound_url("http://10.20.30.63:27465/v1") is False
    assert agent_config.is_safe_outbound_url("http://1.2.3.6/") is True


def test_empty_env_value_gives_empty_allowlist(monkeypatch):
    monkeypatch.setenv("OUTBOUND_ALLOWED_CIDRS", "")
    config = _load_config("_t_agent_config_ssrf_empty", _REPO / "agent_api" / "config.py")
    assert config._OUTBOUND_ALLOWED_NETWORKS == []
    assert config.is_safe_outbound_url("http://10.20.30.63/") is False


def test_several_networks_via_comma(agent_config, monkeypatch):
    _with_cidrs(agent_config, monkeypatch, "10.20.30.0/24, 192.168.1.0/24")
    assert agent_config.is_safe_outbound_url("http://10.20.30.63/") is True
    assert agent_config.is_safe_outbound_url("http://192.168.1.10/") is True
    assert agent_config.is_safe_outbound_url("http://192.168.2.10/") is False
    assert agent_config.is_safe_outbound_url("http://10.1.0.1/") is False


def test_non_strict_network_with_host_bits(agent_config, monkeypatch):
    _with_cidrs(agent_config, monkeypatch, "10.20.30.63/24")
    assert agent_config.is_safe_outbound_url("http://10.20.30.5/") is True


def test_invalid_cidr_warns_and_is_skipped(agent_config, caplog):
    with caplog.at_level("WARNING", logger=agent_config.logger.name):
        nets = agent_config._parse_allowed_cidrs("10.20.30.0/24,not-a-net,192.168.9.0/24")
    assert [str(n) for n in nets] == ["10.20.30.0/24", "192.168.9.0/24"]
    warns = [r for r in caplog.records if r.levelname == "WARNING" and "not-a-net" in r.getMessage()]
    assert len(warns) == 1


def test_valid_networks_work_next_to_invalid_one(agent_config, monkeypatch):
    _with_cidrs(agent_config, monkeypatch, "garbage,10.20.30.0/24")
    assert agent_config.is_safe_outbound_url("http://10.20.30.63/") is True
    assert agent_config.is_safe_outbound_url("http://10.5.0.63/") is False


def test_allowlisted_hostname_still_passes_and_other_names_do_not(agent_config):
    assert agent_config.is_safe_outbound_url("https://ai.api.cloud.yandex.net/v1") is True
    assert agent_config.is_safe_outbound_url("https://evil.example.com/") is False


def test_cidr_allowlist_does_not_open_hostnames(agent_config, monkeypatch):
    _with_cidrs(agent_config, monkeypatch, "172.16.0.0/12")
    assert agent_config.is_safe_outbound_url("http://internal.lab/") is False


# ── build_mongo_uri (agent_api and dataset_api keep identical copies) ──


def _load_config(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mongo_builders():
    return {
        "agent_api": _load_config("_t_agent_config", _REPO / "agent_api" / "config.py").build_mongo_uri,
        "dataset_api": _load_config("_t_dataset_config", _REPO / "dataset_api" / "config.py").build_mongo_uri,
    }


MONGO_CASES = [
    (("u@x", "p@ss:w/rd%", "h", 27017), {}, "mongodb://u%40x:p%40ss%3Aw%2Frd%25@h:27017/"),
    (("", "secret", "h", 27017), {}, "mongodb://h:27017/"),
    (("u", "", "h", "27017"), {}, "mongodb://u:@h:27017/"),
    (("", "", "h", 1), {"authSource": "admin", "replicaSet": ""}, "mongodb://h:1/?authSource=admin"),
    (("u", "p", "h", 1), {"authSource": "admin", "x": None}, "mongodb://u:p@h:1/?authSource=admin"),
]


@pytest.mark.parametrize("args,params,expected", MONGO_CASES)
def test_build_mongo_uri_both_copies(mongo_builders, args, params, expected):
    for name, build in mongo_builders.items():
        assert build(*args, **params) == expected, name


# ── Честный health: наличие модели в /models и пробный запрос (smoke) ──

import ssl  # noqa: E402

import httpx  # noqa: E402
from openai import (  # noqa: E402
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
)

OLLAMA = "http://1.2.3.4:11434/v1"
VLLM = "http://1.2.3.5:8000/v1"
HEALTH_ENDPOINTS = [
    {"id": "qwen", "name": "Qwen", "base_url": OLLAMA, "model_name": "qwen3.6:27b", "api_key": "none"},
    {"id": "mistral", "name": "Mistral", "base_url": OLLAMA, "model_name": "mistral-small3.2:24b", "api_key": "none"},
    {"id": "gemma", "name": "Gemma", "base_url": VLLM, "model_name": "gemma4-31b-it", "api_key": "sk-g"},
]


class _SyncExecutor:
    """Smoke в тестах — сразу в вызывающем потоке."""

    def submit(self, fn, *args):
        fn(*args)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _ListingHttp:
    """httpx.Client: GET {base}/models → id из ``served[base]`` (int — HTTP-статус ошибки, dict — тело
    как есть); прочие base — ConnectError."""

    def __init__(self, served):
        self.served = served
        self.calls = []

    def client_cls(self):
        outer = self

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None, headers=None):
                outer.calls.append(url)
                base = url[: -len("/models")]
                if base not in outer.served:
                    raise httpx.ConnectError("refused")
                served = outer.served[base]
                if isinstance(served, int):
                    return SimpleNamespace(status_code=served, json=lambda: {})
                data = served if isinstance(served, (dict, str)) else {"data": [{"id": i} for i in served]}
                return SimpleNamespace(status_code=200, json=lambda: data)

        return _Client


class _FakeChat:
    """create_chat_llm: запоминает параметры, invoke бросает ``errors[model_name]``."""

    def __init__(self, errors=None):
        self.errors = errors or {}
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        error = self.errors.get(kw.get("model_name"))

        def invoke(_prompt):
            if error is not None:
                raise error
            return SimpleNamespace(content="o")

        return SimpleNamespace(invoke=invoke)


def _health_registry(monkeypatch, endpoints, served, *, interval=0, chat=None, clock=None, cloud=False):
    monkeypatch.setenv("MODEL_ENDPOINTS", json.dumps(endpoints))
    monkeypatch.setattr(mod, "LLM_URL_MODEL", "")
    monkeypatch.setattr(mod, "LLM_MODEL_NAME", "")
    monkeypatch.setattr(mod, "GIGACHAT_CREDENTIALS", "giga-creds" if cloud else "")
    monkeypatch.setattr(mod, "YANDEX_CLOUD_API_KEY", "ya-key" if cloud else "")
    monkeypatch.setattr(mod, "YANDEX_CLOUD_FOLDER", "folder" if cloud else "")
    monkeypatch.setattr(mod, "ARGOCD_PASSWORD", "")
    monkeypatch.setattr(mod, "LITELLM_API_KEY", "")
    http = _ListingHttp(served)
    monkeypatch.setattr(mod.httpx, "Client", http.client_cls())
    chat = chat or _FakeChat()
    monkeypatch.setattr(mod.model_smoke, "create_chat_llm", chat)
    with patch.object(mod.ModelsRegistry, "_start_probe_poller", lambda self: None):
        registry = mod.ModelsRegistry(
            smoke_interval=interval, smoke_executor=_SyncExecutor(), clock=clock or _Clock()
        )
    return registry, http, chat


def _health_by_id(registry):
    return {h["id"]: h for h in registry.get_health()}


def test_model_missing_from_models_listing_is_unavailable_with_reason(monkeypatch):
    registry, _, _ = _health_registry(monkeypatch, HEALTH_ENDPOINTS, {OLLAMA: ["gemma3:4b"], VLLM: ["gemma4-31b-it"]})
    registry._do_probe()
    health = _health_by_id(registry)
    assert health["qwen"]["available"] is False
    assert health["qwen"]["reason"] == "модели qwen3.6:27b нет на сервере"
    assert health["gemma"] == {"id": "gemma", "name": "Gemma", "available": True}
    # недоступная модель остаётся в /models/
    assert {m.id for m in registry.list_models()} == {"qwen", "mistral", "gemma"}


def test_model_present_in_listing_is_available(monkeypatch):
    registry, _, _ = _health_registry(
        monkeypatch, HEALTH_ENDPOINTS, {OLLAMA: ["qwen3.6:27b", "mistral-small3.2:24b"], VLLM: ["gemma4-31b-it"]}
    )
    registry._do_probe()
    assert all(h["available"] for h in registry.get_health())


def test_model_name_match_is_exact(monkeypatch):
    registry, _, _ = _health_registry(monkeypatch, HEALTH_ENDPOINTS[:1], {OLLAMA: ["qwen3.6:27b-q8", "QWEN3.6:27B"]})
    registry._do_probe()
    assert _health_by_id(registry)["qwen"]["available"] is False


def test_unreachable_listing_marks_model_unavailable(monkeypatch):
    registry, _, _ = _health_registry(monkeypatch, HEALTH_ENDPOINTS[2:], {})
    registry._do_probe()
    assert _health_by_id(registry)["gemma"] == {
        "id": "gemma", "name": "Gemma", "available": False, "reason": "сервер недоступен",
    }


@pytest.mark.parametrize(
    "served,reason",
    [
        (401, "/models ответил HTTP 401"),
        ({"models": [{"name": "gemma4-31b-it"}]}, ""),
        ("not json object", "модели gemma4-31b-it нет на сервере"),
    ],
)
def test_listing_status_and_formats(monkeypatch, served, reason):
    registry, _, _ = _health_registry(monkeypatch, HEALTH_ENDPOINTS[2:], {VLLM: served})
    registry._do_probe()
    assert _health_by_id(registry)["gemma"].get("reason", "") == reason


def test_one_models_request_per_base_url_per_probe(monkeypatch):
    registry, http, _ = _health_registry(
        monkeypatch, HEALTH_ENDPOINTS, {OLLAMA: ["qwen3.6:27b", "mistral-small3.2:24b"], VLLM: ["gemma4-31b-it"]}
    )
    registry._do_probe()
    assert sorted(http.calls) == sorted([f"{OLLAMA}/models", f"{VLLM}/models"])
    registry._do_probe()
    assert len(http.calls) == 4  # кэш живёт один probe


def _ssl_connection_error():
    err = APIConnectionError(request=httpx.Request("POST", f"{VLLM}/chat/completions"))
    err.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")
    return err


def _status(cls, code):
    req = httpx.Request("POST", f"{VLLM}/chat/completions")
    return cls("x", response=httpx.Response(code, request=req), body=None)


@pytest.mark.parametrize(
    "error,reason",
    [
        (_status(AuthenticationError, 401), "ключ отклонён (401/403)"),
        (_status(NotFoundError, 404), "модель не найдена (404)"),
        (_ssl_connection_error(), "ошибка SSL"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), "ошибка SSL"),
        (_status(InternalServerError, 500), "ошибка бэкенда (5xx)"),
        (APITimeoutError(request=httpx.Request("POST", VLLM)), "таймаут"),
        (APIConnectionError(request=httpx.Request("POST", VLLM)), "сервер недоступен"),
        (_status(BadRequestError, 400), "ошибка запроса (HTTP 400)"),
        (ValueError("Unsafe LLM base_url blocked"), "ошибка запроса (ValueError)"),
    ],
)
def test_failed_smoke_marks_model_unavailable_with_reason(monkeypatch, error, reason):
    chat = _FakeChat({"gemma4-31b-it": error})
    registry, _, _ = _health_registry(
        monkeypatch, HEALTH_ENDPOINTS[2:], {VLLM: ["gemma4-31b-it"]}, interval=900, chat=chat
    )
    registry._do_probe()
    assert _health_by_id(registry)["gemma"] == {
        "id": "gemma", "name": "Gemma", "available": False, "reason": reason,
    }


def test_rate_limited_smoke_keeps_model_available(monkeypatch):
    """429: модель жива, просто занята."""
    chat = _FakeChat({"gemma4-31b-it": _status(RateLimitError, 429)})
    registry, _, _ = _health_registry(
        monkeypatch, HEALTH_ENDPOINTS[2:], {VLLM: ["gemma4-31b-it"]}, interval=900, chat=chat
    )
    registry._do_probe()
    assert _health_by_id(registry)["gemma"]["available"] is True


def test_successful_smoke_uses_record_params_with_one_token_and_no_retries(monkeypatch):
    endpoint = dict(HEALTH_ENDPOINTS[2], extra={"reasoning_effort": "none"})
    registry, _, chat = _health_registry(monkeypatch, [endpoint], {VLLM: ["gemma4-31b-it"]}, interval=900)
    registry._do_probe()
    assert _health_by_id(registry)["gemma"]["available"] is True
    assert chat.calls == [{
        "provider": "openai", "model_name": "gemma4-31b-it", "base_url": VLLM, "api_key": "sk-g",
        "max_tokens": 1, "timeout": 20, "max_retries": 0, "reasoning_effort": "none",
    }]


def test_smoke_is_not_repeated_before_interval(monkeypatch):
    clock = _Clock()
    chat = _FakeChat({"gemma4-31b-it": _status(InternalServerError, 500)})
    registry, _, _ = _health_registry(
        monkeypatch, HEALTH_ENDPOINTS[2:], {VLLM: ["gemma4-31b-it"]}, interval=900, chat=chat, clock=clock
    )
    registry._do_probe()
    clock.now += 899
    registry._do_probe()
    assert len(chat.calls) == 1
    # результат последнего smoke держится до следующего
    assert _health_by_id(registry)["gemma"]["reason"] == "ошибка бэкенда (5xx)"
    chat.errors.clear()
    clock.now += 1
    registry._do_probe()
    assert len(chat.calls) == 2
    assert _health_by_id(registry)["gemma"]["available"] is True


def test_smoke_interval_zero_disables_smoke(monkeypatch):
    registry, _, chat = _health_registry(
        monkeypatch, HEALTH_ENDPOINTS[2:], {VLLM: ["gemma4-31b-it"]}, interval=0, cloud=True
    )
    registry._do_probe()
    assert chat.calls == []
    assert all(h["available"] for h in registry.get_health())


def test_cloud_models_are_checked_only_by_smoke(monkeypatch):
    chat = _FakeChat({
        "GigaChat-2": httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]"),
        mod.YANDEX_CLOUD_MODEL: _status(AuthenticationError, 401),
    })
    monkeypatch.setattr(mod, "GIGACHAT_MODEL", "GigaChat-2")
    registry, http, _ = _health_registry(monkeypatch, [], {}, interval=900, chat=chat, cloud=True)
    registry._do_probe()
    assert http.calls == []
    health = _health_by_id(registry)
    assert health["gigachat"]["reason"] == "ошибка SSL"
    assert health["yandexgpt"]["reason"] == "ключ отклонён (401/403)"
    assert {c["provider"] for c in chat.calls} == {"gigachat", "yandex"}


def test_discovered_litellm_alias_is_smoked(monkeypatch):
    """alias есть в /models LiteLLM, но бэкенд не запущен → 500 на chat."""
    fake = _FakeHttp([], [], ["mistral-nemo"])
    registry = _discovery_registry(monkeypatch, fake, argo_password="")
    chat = _FakeChat({"mistral-nemo": _status(InternalServerError, 500)})
    monkeypatch.setattr(mod.model_smoke, "create_chat_llm", chat)
    with patch.object(mod.ModelsRegistry, "_start_probe_poller", lambda self: None):
        registry = mod.ModelsRegistry(smoke_interval=900, smoke_executor=_SyncExecutor(), clock=_Clock())
    registry._do_probe()
    entry = _health_by_id(registry)["litellm-mistral-nemo"]
    assert entry["available"] is False and entry["reason"] == "ошибка бэкенда (5xx)"
    assert [c[1] for c in fake.calls] == [f"{LITELLM}/models"]


# ── /models/discover/: модель доступна, только если её отдаёт /models ──


class _AsyncListing:
    """httpx.AsyncClient: GET {base}/models → ``served[base]``; прочие base — ConnectError."""

    def __init__(self, served):
        self.served = served
        self.headers = []

    def client_cls(self):
        outer = self

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                outer.headers.append(headers)
                base = url[: -len("/models")]
                if base not in outer.served:
                    raise httpx.ConnectError("refused")
                data = outer.served[base]
                return SimpleNamespace(status_code=200, json=lambda: data)

        return _Client


def _discover(monkeypatch, registry, served):
    import asyncio

    fake = _AsyncListing(served)
    monkeypatch.setattr(mod.httpx, "AsyncClient", fake.client_cls())
    return {r["id"]: r for r in asyncio.run(registry.discover_models())}, fake


def test_discover_requires_model_name_in_served_list(monkeypatch):
    registry, _, _ = _health_registry(monkeypatch, HEALTH_ENDPOINTS, {})
    rows, fake = _discover(monkeypatch, registry, {
        OLLAMA: {"data": [{"id": "qwen3.6:27b"}]},
        VLLM: {"models": [{"name": "gemma4-31b-it"}]},
    })
    assert rows["qwen"]["available"] is True
    assert rows["mistral"] == {
        "id": "mistral", "name": "Mistral", "base_url": OLLAMA, "model_name": "mistral-small3.2:24b",
        "provider": "openai", "available": False, "served_models": ["qwen3.6:27b"],
    }
    assert rows["gemma"]["available"] is True and rows["gemma"]["served_models"] == ["gemma4-31b-it"]
    assert {"Authorization": "Bearer sk-g"} in fake.headers and {} in fake.headers


def test_discover_unreachable_or_unsafe_endpoint_is_unavailable(monkeypatch):
    blocked = {"id": "blocked", "name": "B", "base_url": VLLM, "model_name": "x", "api_key": "none"}
    registry, _, _ = _health_registry(monkeypatch, HEALTH_ENDPOINTS[:1] + [blocked], {})
    monkeypatch.setattr(mod, "is_safe_outbound_url", lambda url: not url.startswith(VLLM))
    rows, fake = _discover(monkeypatch, registry, {})
    assert rows["qwen"]["available"] is False and rows["qwen"]["served_models"] == []
    assert rows["blocked"]["available"] is False
    assert len(fake.headers) == 1  # заблокированный URL не запрашивается


def test_discover_cloud_model_follows_last_smoke(monkeypatch):
    chat = _FakeChat({mod.YANDEX_CLOUD_MODEL: _status(AuthenticationError, 401)})
    registry, _, _ = _health_registry(monkeypatch, [], {}, interval=900, chat=chat, cloud=True)
    registry._do_probe()
    rows, fake = _discover(monkeypatch, registry, {})
    assert rows["yandexgpt"]["available"] is False
    assert rows["gigachat"]["available"] is True
    assert rows["gigachat"]["served_models"] == [mod.GIGACHAT_MODEL]
    assert fake.headers == []
