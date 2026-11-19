"""gen_env.sh writes a .env that survives `. ./.env`, python-dotenv and compose parsing."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _bash_major() -> int:
    try:
        out = subprocess.run(
            ["bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, check=True
        ).stdout.strip()
        return int(out)
    except Exception:
        return 0


pytestmark = pytest.mark.skipif(
    _bash_major() < 4, reason="gen_env.sh needs bash >= 4 (${var,,})"
)

NASTY = {
    "LLM_API_KEY": "abc)d e f!g$h",
    "EXPERT_PASSWORD": 'p@ss)w"ord',
    "CORS_ORIGINS": "http://a, http://b",
    "CHUNKER_TEXTBOOK_DROP_HEADINGS": "Вопросы и задания, Контрольные вопросы",
    "TEI_API_KEY": "",
}

BASE_ENV = {
    "MONGO_USERNAME": "mongo",
    "MONGO_PASSWORD": "m0ng0 (pw)",
    "MONGO_HOST": "mongo",
    "MONGO_PORT": "27017",
    "MILVUS_HOST": "milvus",
    "MILVUS_PORT": "19530",
    "EXPERT_USERNAME": "expert",
    "JWT_SECRET": "jwt secret)",
}


@pytest.fixture
def workdir(tmp_path):
    shutil.copy(_REPO / "gen_env.sh", tmp_path / "gen_env.sh")
    (tmp_path / "config").mkdir()
    shutil.copy(_REPO / "config" / "llm_defaults.env", tmp_path / "config" / "llm_defaults.env")
    return tmp_path


def _run(workdir: Path, extra: dict, mode: str = "dev"):
    env = {"PATH": os.environ["PATH"], "HOME": str(workdir), **BASE_ENV, **extra}
    return subprocess.run(
        ["bash", "./gen_env.sh", mode], cwd=workdir, env=env, capture_output=True, text=True
    )


def test_nasty_values_roundtrip_through_bash_and_dotenv(workdir):
    from dotenv import dotenv_values

    res = _run(workdir, NASTY)
    assert res.returncode == 0, res.stderr
    env_file = workdir / ".env"
    assert env_file.exists()

    names = list(NASTY)
    script = "set -a; . ./.env; set +a; " + "".join(f'printf "%s\\0" "${n}"; ' for n in names)
    sourced = subprocess.run(
        ["bash", "-c", script], cwd=workdir, env={"PATH": os.environ["PATH"]},
        capture_output=True, text=True,
    )
    assert sourced.returncode == 0, sourced.stderr
    assert sourced.stdout.split("\0")[:-1] == [NASTY[n] for n in names]

    parsed = dotenv_values(env_file)
    for n in names:
        assert (parsed[n] or "") == NASTY[n], n

    endpoints = json.loads(parsed["MODEL_ENDPOINTS"])
    assert endpoints[0]["api_key_env"] == "LLM_API_KEY"


def test_env_file_has_no_bare_spaces_or_parentheses(workdir):
    assert _run(workdir, NASTY).returncode == 0
    for line in (workdir / ".env").read_text(encoding="utf-8").splitlines():
        assert not re.match(r"^[A-Z_]+=[^'\"]*[ ()]", line), line


def test_prod_mode_also_generates(workdir):
    res = _run(workdir, NASTY, mode="prod")
    assert res.returncode == 0, res.stderr
    assert (workdir / ".env").exists()


def test_single_quote_in_value_fails_and_removes_env(workdir):
    res = _run(workdir, {"EXPERT_PASSWORD": "it's"})
    assert res.returncode == 1
    assert not (workdir / ".env").exists()
    assert "EXPERT_PASSWORD" in res.stderr


def test_removed_traefik_variables_are_not_written(workdir):
    assert _run(workdir, {"TRAEFIK_HOST": "x", "TRAEFIK_BASIC_AUTH_USER": "u"}).returncode == 0
    text = (workdir / ".env").read_text(encoding="utf-8")
    assert "TRAEFIK" not in text


ADDRESS_KEYS = ("ARGOCD_URL", "LITELLM_BASE_URL", "OUTBOUND_ALLOWED_CIDRS")


def test_address_defaults_are_empty_without_deployment_config(workdir):
    from dotenv import dotenv_values

    (workdir / "config" / "llm_defaults.env").write_text("LLM_PROVIDER=openai\n", encoding="utf-8")
    assert _run(workdir, {}).returncode == 0
    parsed = dotenv_values(workdir / ".env")
    for key in ADDRESS_KEYS:
        assert (parsed[key] or "") == "", key
    assert parsed["ARGOCD_USERNAME"] == "admin"
    assert (parsed["LITELLM_API_KEY"] or "") == ""


def test_address_values_come_from_llm_defaults_and_beat_variables(workdir):
    from dotenv import dotenv_values

    (workdir / "config" / "llm_defaults.env").write_text(
        "ARGOCD_URL=http://argocd.example:8080\n"
        "LITELLM_BASE_URL=http://litellm:4000/v1\n"
        "OUTBOUND_ALLOWED_CIDRS=10.20.30.0/24\n",
        encoding="utf-8",
    )
    assert _run(workdir, {"ARGOCD_URL": "http://other", "LITELLM_BASE_URL_DEV": "http://x"}).returncode == 0
    parsed = dotenv_values(workdir / ".env")
    assert parsed["ARGOCD_URL"] == "http://argocd.example:8080"
    assert parsed["LITELLM_BASE_URL"] == "http://litellm:4000/v1"
    assert parsed["OUTBOUND_ALLOWED_CIDRS"] == "10.20.30.0/24"
