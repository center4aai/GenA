# GenA rollback runbook

## Before rollout

1. Run backfill dry-run: `python3 scripts/backfill_data.py`
2. Apply backfill: `python3 scripts/backfill_data.py --apply`
3. Regenerate env: `./gen_env.sh dev` or `./gen_env.sh prod`
4. Deploy dataset_api first (task claiming goes through `POST /tasks/claim`)
5. Deploy task_worker (uses claim endpoint)
6. Deploy agent_api + chunker (or `chunker_gpu` with `--profile gpu --scale chunker=0`)
7. Deploy gena_frontend last
8. Enable auth only after all services support `AUTH_DISABLED=false`, `WORKER_API_KEY` is set, and `VITE_AUTH_MODE=enabled` at build time

## Rollback by phase

| Phase | Rollback action |
|-------|-----------------|
| Frontend | Revert gena_frontend image; rebuild with `VITE_AUTH_MODE=disabled` |
| Auth | Set `AUTH_DISABLED=true` on dataset_api and regenerate `.env` |
| Schema | Backfill is additive; no down-migration required |

## Graceful shutdown

```bash
docker compose -p "$COMPOSE_PROJECT" stop task_worker   # drain in-flight tasks
sleep 30
docker compose -p "$COMPOSE_PROJECT" down
```

CI now does this on every deploy: `deploy_services` / `deploy_services_main` run `docker compose stop task_worker` (stop_grace_period 120s: the worker finishes its current agent call and returns unstarted tasks to `pending`) before `down`.

Worker respects SIGTERM; in-flight agent calls may take up to `WORKER_AGENT_TIMEOUT` seconds.

## Verify health

Backend ports are not published to the host; check from inside the stack:

```bash
docker compose -p "$COMPOSE_PROJECT" exec -T dataset_api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8789/health').read().decode())"
docker compose -p "$COMPOSE_PROJECT" exec -T agent_api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8790/health/').read().decode())"
docker compose -p "$COMPOSE_PROJECT" exec -T chunker python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8517/health').read().decode())"
docker compose -p "$COMPOSE_PROJECT" exec -T task_worker python -c "import os; os.kill(1, 0); print('task_worker alive')"
docker compose -p "$COMPOSE_PROJECT" exec -T gena_frontend curl -fk https://127.0.0.1/ >/dev/null && echo "gena_frontend OK"
```

On GPU hosts using `chunker_gpu`, use `docker compose exec chunker_gpu` for in-container checks (the service registers DNS alias `chunker` for other containers).

Automated smoke (CI): `bash scripts/ci_smoke_endpoints.sh` with `PROJECT_DIR` and `COMPOSE_PROJECT` set.
