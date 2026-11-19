"""Worker-only API routes must be blocked at the edge.

`verify_worker_key` is a no-op while AUTH_DISABLED (the current default), so the
nginx 403 rules in gena_frontend/nginx.conf are what actually keeps browsers away
from worker endpoints. Those rules live in a different directory from the routes
they protect and drift silently: /gate-progress shipped unblocked exactly this
way. This test derives the worker-route list from dataset_api.py itself, so a new
worker endpoint fails CI until it gets an nginx rule.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_API_SRC = (_REPO / "dataset_api" / "dataset_api.py").read_text()
_NGINX = (_REPO / "gena_frontend" / "nginx.conf").read_text()

_ROUTE_RE = re.compile(
    r"@app\.(?:get|post|put|patch|delete)\(\s*\"(?P<path>[^\"]+)\"", re.MULTILINE
)


def _worker_routes() -> list[str]:
    """Paths of every route whose handler depends on verify_worker_key."""
    routes = []
    matches = list(_ROUTE_RE.finditer(_API_SRC))
    for i, m in enumerate(matches):
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(_API_SRC)
        body = _API_SRC[m.end():body_end]
        if "verify_worker_key" in body:
            routes.append(m.group("path"))
    return routes


def test_worker_routes_exist():
    # The claim/status pair is the worker's lifeline; if the regex ever stops
    # matching, this guard would silently pass on an empty list.
    routes = _worker_routes()
    assert "/tasks/claim" in routes
    assert len(routes) >= 5


def test_every_worker_route_is_blocked_in_nginx():
    unblocked = []
    for path in _worker_routes():
        # "/datasets/{id}/gate-progress" -> "/api/dataset/datasets/.+/gate-progress",
        # which matches both exact (`location = ...`) and regex nginx rules.
        normalized = "/api/dataset" + re.sub(r"\{[^}]+\}", ".+", path)
        if normalized not in _NGINX:
            unblocked.append(path)
    assert not unblocked, (
        f"Worker-only routes reachable from the browser: {unblocked}. "
        "Add a `return 403` location for each to gena_frontend/nginx.conf."
    )


# --- agent_api: no auth, so only the paths the UI calls may be proxied ------

_AGENT_API_SRC = (_REPO / "agent_api" / "agent_api.py").read_text()
_AGENT_CLIENT_SRC = (
    _REPO / "gena_frontend" / "src" / "shared" / "api" / "agent.ts"
).read_text()
# Generic may nest (`agentJson<AgentResponse<Question[]>>(`), so eat up to `(`.
_AGENT_CLIENT_PATH_RE = re.compile(r"agentJson(?:<[^(]*>)?\(\s*'(?P<path>/[^']*)'")
# Directives only: comments may legitimately mention blocked paths.
_NGINX_CODE = re.sub(r"#[^\n]*", "", _NGINX)


def _location_blocks() -> list[tuple[str, str]]:
    """(header, body) for every `location ... { ... }` in nginx.conf."""
    blocks = []
    for m in re.finditer(r"^\s*location\s+(?P<header>[^{]+?)\s*\{", _NGINX, re.MULTILINE):
        depth, i = 1, m.end()
        while depth and i < len(_NGINX):
            depth += {"{": 1, "}": -1}.get(_NGINX[i], 0)
            i += 1
        blocks.append((m.group("header").strip(), _NGINX[m.end():i - 1]))
    return blocks


def _agent_routes() -> list[str]:
    return [m.group("path") for m in _ROUTE_RE.finditer(_AGENT_API_SRC)]


def _agent_ui_paths() -> set[str]:
    return {m.group("path") for m in _AGENT_CLIENT_PATH_RE.finditer(_AGENT_CLIENT_SRC)}


def _proxied_exact_paths() -> set[str]:
    return {
        header[len("= "):].strip()
        for header, body in _location_blocks()
        if header.startswith("=") and "proxy_pass" in body
    }


def test_agent_route_lists_are_sane():
    assert "/process_prompt/" in _agent_routes()
    assert "/chunk_gate/" in _agent_routes()
    assert {"/models/", "/rephrase_questions/"} <= _agent_ui_paths()


def test_agent_catch_all_returns_403():
    catch_all = [
        body for header, body in _location_blocks() if header == "^~ /api/agent/"
    ]
    assert catch_all and "return 403" in catch_all[0], (
        "Expected `location ^~ /api/agent/ { return 403; }` in nginx.conf"
    )


def test_ui_agent_paths_are_proxied():
    missing = {
        "/api/agent" + p for p in _agent_ui_paths()
    } - _proxied_exact_paths()
    assert not missing, f"UI calls agent paths nginx does not proxy: {missing}"


def test_internal_agent_routes_are_not_proxied():
    internal = {"/api/agent" + p for p in _agent_routes()} - {
        "/api/agent" + p for p in _agent_ui_paths()
    }
    exposed = internal & _proxied_exact_paths()
    assert not exposed, f"agent_api routes unused by the UI are reachable: {exposed}"
    for p in ("/process_prompt/", "/chunk_gate/"):
        assert "/api/agent" + p not in _NGINX_CODE


def test_chunker_is_not_proxied():
    assert "chunker" not in _NGINX_CODE


# --- caching ------------------------------------------------------------------

def test_index_html_is_no_cache():
    index = [body for header, body in _location_blocks() if header == "= /index.html"]
    assert index, "Expected `location = /index.html` block"
    assert re.search(r"Cache-Control\s+\"?no-cache", index[0])


def test_immutable_only_for_hashed_assets():
    blocks = _location_blocks()
    assert "^~ /assets/" in {header for header, _ in blocks}
    for header, body in blocks:
        if "immutable" in body or "expires" in body:
            assert header == "^~ /assets/", (
                f"Long-lived caching outside /assets/: `location {header}`"
            )
    # No regex location may claim static files by extension: it would
    # shadow /api/ prefixes and cache un-hashed files like /logo.png.
    assert not re.search(r"location\s+~\*?\s+\\?\.\(", _NGINX_CODE)
