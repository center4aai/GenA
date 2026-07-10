# Streamlit → TypeScript migration notes

## Source restored from
- Base: git commit `c94b31c` (move2typescript)
- Nginx same-origin proxy: commit `47409af`
- Auth gating: commit `60cfaac`

## Parity map
| Streamlit view | TS route | Status |
|---|---|---|
| `views/bot.py` | `/data_preprocessing` | Full parity (queue + direct, gate, models, ablation) |
| `views/dataset_editor.py` | `/dataset_editor` | Parity + bar charts, version diff, pipeline CSV export |
| `views/queue_manager.py` | `/queue_manager` | Parity + polling, model health, progress bars |
| `views/statistics.py` | `/statistics` | Parity + metric cards, bar charts, CSV export |
| `views/dynamic_implementation.py` | `/dynamic_implementation` | Existing TS page |
| `views/home.py` + `docs.py` | `/`, `/docs` | Existing TS pages |

## API wiring
- Production: nginx proxies `/api/dataset`, `/api/agent`, `/api/chunker` to backends
- Dev: Vite dev server proxies same paths to `localhost:8789/8790/8517`
- Env: `VITE_DATASET_API_URL`, `VITE_AGENT_API_URL`, `VITE_CHUNKER_URL` (default `/api/*`)

## Design system
- Inter font, slate/blue palette, `card` / `btn-primary` / `input-field` utilities
- Shared UI: `PageHeader`, `MetricCard`, `BarChart`, `StatusBanner`, `LoadingState`, `EmptyState`
- Mobile: collapsible sidebar on small screens

## Deploy
```bash
docker compose up -d gena_frontend dataset_api agent_api chunker task_worker
# SPA: https://localhost:${WEB_PORT:-27371}
# Legacy Streamlit (optional): docker compose --profile streamlit up -d gena_web
```

## Remaining optional enhancements
- Native XLSX export (currently CSV; Streamlit used openpyxl)
- Plotly-level interactive charts (CSS bar charts used for zero-deps visuals)
- Playwright E2E against live backend in CI
