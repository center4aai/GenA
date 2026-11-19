"""
Контракт переменных окружения между кодом сервисов и .env-инфраструктурой.

(a) Каждая переменная, которую читает код сервисов (agent_api, dataset_api,
    task_worker, chunker), должна быть объявлена хотя бы в одном из:
    gen_env.sh (heredoc, который пишет .env), env.example, docker-compose.yaml
    (секции ``environment:``). Иначе CI-деплой молча получит дефолт из кода
    вместо значения из GitLab Variables.
(b) Каждая переменная, которую gen_env.sh пишет в .env, должна где-то
    читаться: кодом (сервисы + scripts/), docker-compose.yaml (``${X}``),
    Dockerfile'ами, gena_frontend (vite / nginx), .gitlab-ci.yml или scripts/*.sh.
    Иначе это мёртвая переменная.

Чистый python без сторонних зависимостей: файлы разбираются регулярками.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SERVICE_DIRS = ("agent_api", "dataset_api", "task_worker", "chunker")

# Переменные, которые задаёт рантайм/CI/оркестратор, а не .env.
RUNTIME_ALLOWLIST = {
    "HOSTNAME",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "HOME",
    "PATH",
    "PWD",
    "USER",
    "WORKER_ID",  # docker-compose.yaml environment: + случайный fallback в коде
}
RUNTIME_ALLOWLIST_PREFIXES = ("CI_", "GITLAB_")

# Python-паттерны чтения окружения (см. docstring задачи + _safe_int_env в
# agent_api, _env_int в chunker).
_PY_READ = re.compile(
    r"""(?:os\.getenv|os\.environ\.get|_env_llm|_env_llm_int|_safe_int_env|_env_int|_env_float)\(\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]"""
    r"""|["']([A-Z][A-Z0-9_]*)["']\s+in\s+os\.environ"""
)
_ENV_KEY = re.compile(r"^([A-Z][A-Z0-9_]*)=")
_SHELL_REF = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)")
_COMPOSE_ENV_KEY = re.compile(r"^\s*(?:-\s*)?([A-Z][A-Z0-9_]*)\s*[:=]")
_DOCKERFILE_DECL = re.compile(r"^(?:ARG|ENV)\s+([A-Z][A-Z0-9_]*)", re.MULTILINE)
_VITE_REF = re.compile(r"(?:import\.meta\.env|process\.env)\.([A-Z][A-Z0-9_]*)")


def _is_test_file(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py" or "tests" in path.parts


def _py_files(root: Path, *, include_tests: bool):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts or "node_modules" in path.parts:
            continue
        if not include_tests and _is_test_file(path.relative_to(REPO)):
            continue
        yield path


def _py_reads(paths) -> dict[str, set[str]]:
    """{VAR: {file:line, ...}}"""
    found: dict[str, set[str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in _PY_READ.finditer(line):
                name = next(g for g in m.groups() if g)
                found.setdefault(name, set()).add(f"{path.relative_to(REPO)}:{lineno}")
    return found


def service_env_reads() -> dict[str, set[str]]:
    paths = []
    for d in SERVICE_DIRS:
        paths.extend(_py_files(REPO / d, include_tests=False))
    return _py_reads(paths)


def gen_env_written_keys() -> set[str]:
    """Ключи внутри `cat > .env << EOF ... EOF` в gen_env.sh."""
    text = (REPO / "gen_env.sh").read_text(encoding="utf-8")
    m = re.search(r"cat > \.env << EOF\n(.*?)\nEOF", text, re.S)
    assert m, "gen_env.sh: не найден heredoc `cat > .env << EOF ... EOF`"
    keys = set()
    for line in m.group(1).splitlines():
        km = _ENV_KEY.match(line)
        if km:
            keys.add(km.group(1))
    assert keys, "gen_env.sh: heredoc не содержит ни одного KEY="
    return keys


def env_example_keys() -> set[str]:
    keys = set()
    for line in (REPO / "env.example").read_text(encoding="utf-8").splitlines():
        km = _ENV_KEY.match(line)
        if km:
            keys.add(km.group(1))
    return keys


def _compose_text() -> str:
    return (REPO / "docker-compose.yaml").read_text(encoding="utf-8")


def compose_environment_keys() -> set[str]:
    """Ключи из блоков ``environment:`` (map или list) без YAML-парсера."""
    keys = set()
    env_indent = None
    for raw in _compose_text().splitlines():
        line = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if env_indent is not None:
            if indent > env_indent:
                km = _COMPOSE_ENV_KEY.match(line)
                if km:
                    keys.add(km.group(1))
                continue
            env_indent = None
        if line.strip() == "environment:":
            env_indent = indent
    return keys


def compose_substitutions() -> set[str]:
    return set(_SHELL_REF.findall(_compose_text()))


def dockerfile_refs() -> set[str]:
    refs = set()
    for path in REPO.rglob("Dockerfile*"):
        if "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        refs.update(_DOCKERFILE_DECL.findall(text))
        refs.update(_SHELL_REF.findall(text))
    return refs


def frontend_refs() -> set[str]:
    refs = set()
    fe = REPO / "gena_frontend"
    candidates = list((fe / "src").rglob("*.ts")) + list((fe / "src").rglob("*.tsx"))
    candidates += list(fe.glob("vite.config.*")) + list(fe.glob("playwright.config.*"))
    for path in candidates:
        refs.update(_VITE_REF.findall(path.read_text(encoding="utf-8", errors="replace")))
    for name in ("nginx.conf", "index.html"):
        p = fe / name
        if p.is_file():
            refs.update(_SHELL_REF.findall(p.read_text(encoding="utf-8", errors="replace")))
    return refs


def shell_refs() -> set[str]:
    refs = set()
    # .gitlab-ci.yml is absent from the public copy of the repository.
    files = [REPO / ".gitlab-ci.yml", *sorted((REPO / "scripts").glob("*.sh"))]
    for path in (p for p in files if p.is_file()):
        refs.update(_SHELL_REF.findall(path.read_text(encoding="utf-8", errors="replace")))
    return refs


def model_endpoint_key_refs() -> set[str]:
    """Ключи моделей по ссылке: MODEL_ENDPOINTS[].api_key_env читает models_registry."""
    text = (REPO / "config" / "llm_defaults.env").read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r'"api_key_env"\s*:\s*"([A-Z][A-Z0-9_]*)"', text))


def _fmt(missing: dict[str, set[str]] | set[str]) -> str:
    if isinstance(missing, dict):
        return "\n".join(f"  {k}: {', '.join(sorted(v))}" for k, v in sorted(missing.items()))
    return "\n".join(f"  {k}" for k in sorted(missing))


def _allowlisted(name: str) -> bool:
    return name in RUNTIME_ALLOWLIST or name.startswith(RUNTIME_ALLOWLIST_PREFIXES)


def test_every_variable_read_by_services_is_declared():
    reads = service_env_reads()
    assert reads, "не найдено ни одного чтения окружения — сломался regex?"
    declared = gen_env_written_keys() | env_example_keys() | compose_environment_keys()
    missing = {
        name: where for name, where in reads.items() if not _allowlisted(name) and name not in declared
    }
    assert not missing, (
        "Переменные читаются кодом, но не объявлены ни в gen_env.sh, ни в env.example, "
        "ни в docker-compose.yaml environment:\n" + _fmt(missing)
    )


def test_every_variable_written_by_gen_env_is_read_somewhere():
    written = gen_env_written_keys()
    code_reads = set(_py_reads(
        [p for d in (*SERVICE_DIRS, "scripts") for p in _py_files(REPO / d, include_tests=True)]
    ))
    readers = (
        code_reads | compose_substitutions() | dockerfile_refs() | frontend_refs() | shell_refs()
        | model_endpoint_key_refs()
    )
    dead = written - readers
    assert not dead, (
        "gen_env.sh пишет переменные, которые никто не читает (код, compose, Dockerfile, "
        "gena_frontend, CI, scripts):\n" + _fmt(dead)
    )


def test_env_example_covers_gen_env():
    """env.example — документация контракта: всё, что пишет gen_env.sh, должно быть в нём."""
    missing = gen_env_written_keys() - env_example_keys()
    assert not missing, "gen_env.sh пишет переменные, которых нет в env.example:\n" + _fmt(missing)
