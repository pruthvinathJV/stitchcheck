# StitchCheck operations runbook

How the hosted backend runs, how to deploy and roll back, and what to check during judging (Nov 5 to 8).

Live URL: https://jvpnath--stitchcheck-web-web.us-east.modal.run

## What runs where

Modal app `stitchcheck-web`, defined in `scripts/cloud/serve_modal.py`:

| Function | What it does | Schedule |
|---|---|---|
| `web` | The API and the phone app (served from `app/` at `/`). 1 CPU, 1536 MB (measured peak 907 MB), at most one container (SQLite has a single writer). | always on when `STITCHCHECK_WARM=1`, otherwise scales to zero after 5 idle minutes |
| `uptime` | GET `/health` and `/version` (30 s timeout), one JSON line in `/vol/uptime.jsonl` | every 10 minutes (`*/10 * * * *` UTC) |
| `synthetic` | The no-AI part of the smoke test: POST `/demo/session`, GET its dashboard, GET `/demo/gallery`; same log | hourly at minute 5 |
| `backup` | sqlite3 backup of `/vol/stitchcheck.db` to `/backups/stitchcheck-YYYYMMDD.db`, newest 7 kept, log in `/backups/backup.jsonl` | daily at 03:00 UTC |

Volumes:

| Volume | Mounted at | Holds |
|---|---|---|
| `stitchcheck-models` | `/models` | `demo_ensemble.onnx` |
| `stitchcheck-app` | `/vol` | `stitchcheck.db`, `photos/`, `ref_tiles/`, `uptime.jsonl` |
| `stitchcheck-gallery` | `/gallery` | demo gallery photos (`STITCHCHECK_GALLERY_DIR`) |
| `stitchcheck-backups` | `/backups` | nightly database copies and `backup.jsonl` |

The `web` container commits the app volume every 10 seconds, so the database and photos survive a restart.

Note on cost: when the app is not kept warm, the uptime check every 10 minutes wakes the container, so it is up about half the time. That is a few cents an hour at 1 CPU and 1.5 GB.

## Secrets (names only)

Never put values in the repo, in docs or in shell history. Keep them in a file outside the repo and load it with `--from-dotenv`.

- `stitchcheck-meta` (required): `MODEL_API_KEY`, `MODAL_PROXY_TOKEN_ID`, `MODAL_PROXY_TOKEN_SECRET`
- `stitchcheck-app-config` (required; the app does not start without it): `STITCHCHECK_SAFE_MODE`, `STITCHCHECK_UNIT_PIN`, `STITCHCHECK_ADMIN_TOKEN`, `STITCHCHECK_SECRET`

If `stitchcheck-app-config` does not exist, the deploy prints a warning and goes ahead without it (safe mode, unit PIN and admin token stay off). After creating it, redeploy so the function picks it up:

```
modal secret create stitchcheck-app-config --from-dotenv ~/stitchcheck-app-config.env
```

`modal secret create --force` replaces the whole secret, so the file must always list every key.

## Budget caps

- Muse Spark: $10 a day (`DAILY_CAP_USD` in `src/stitchcheck/backend/brains.py`).
- Muse Spark Open: $5 a day.
- 60 model calls (photos, voice, reports) per visitor IP per hour (`PER_IP_PER_HOUR` in `src/stitchcheck/backend/app.py`).
- When a cap is reached the brain falls back to the other one; when both are capped, photos still return the classifier result and voice and reports answer 503.
- Modal bills Muse Spark Open tokens to the card on file, not to the Modal credit. Watch the Modal billing page during judging.
- Today's spend per brain: `curl -s $URL/brains`.

## Deploy from a tag

Always deploy a tagged commit, from the root of the main checkout (the virtualenv's editable install points at its `src/`, and the locked reference image lives under its `data/raw/`).

```
git fetch --tags
git tag -a judging-YYYYMMDD-N -m "deploy for judging"     # on the commit to ship, then git push --tags
git checkout judging-YYYYMMDD-N
STITCHCHECK_WARM=1 modal deploy scripts/cloud/serve_modal.py
git checkout main
```

The deploy bakes `git rev-parse --short HEAD` into the image as `STITCHCHECK_COMMIT`; `GET /version` reports it. It refuses to deploy when `git status --porcelain` shows changes outside `app/`; `STITCHCHECK_ALLOW_DIRTY=1` overrides that (avoid it: `/version` would no longer describe what runs).

## Pre-deploy checklist

1. Tests pass: `PYTHONPATH=$PWD/src python -m pytest -q`.
2. The local load test passes: `python scripts/ops/load_test.py` (20 users x 5 garments x 2 photos; must print PASS).
3. Deploy from the tag (above).
4. `/version` shows the tag's commit: `curl -s $URL/version` and compare with `git rev-parse --short judging-YYYYMMDD-N`.
5. The smoke test passes against the new deploy:
   `python scripts/ops/smoke_e2e.py $URL --expect-commit $(git rev-parse --short judging-YYYYMMDD-N)`
   (add `--pin PIN` when the unit PIN is on and demo mode is not deployed; `--no-ai` for a check without model calls or spend).
6. Open the URL on a phone and run one garment by hand.

## Rollback

Redeploy the previous tag:

```
git checkout judging-PREVIOUS
STITCHCHECK_WARM=1 modal deploy scripts/cloud/serve_modal.py
git checkout main
```

Then run the smoke test with `--expect-commit` for that tag. `modal app rollback stitchcheck-web` also redeploys the previous version, where the plan allows it. A rollback does not touch the database. So far the schema has only gained tables and columns, which an older build ignores; check `src/stitchcheck/backend/db.py` between the two tags before rolling back across a schema change.

## Warm schedule

- Nov 5 (before judging starts): deploy with `STITCHCHECK_WARM=1` so one container stays up and nobody waits for a cold start (about 7 s for the first request).
- After Nov 8: redeploy the same tag without it (`modal deploy scripts/cloud/serve_modal.py`) to scale to zero again.
- Check: `modal container list` shows one `stitchcheck-web` container while warm.

## Safe mode

Safe mode is read from `STITCHCHECK_SAFE_MODE` in the `stitchcheck-app-config` secret.

1. Edit your local secrets file so it has `STITCHCHECK_SAFE_MODE=1` and every other key, then
   `modal secret create stitchcheck-app-config --from-dotenv ~/stitchcheck-app-config.env --force`
2. Redeploy the current tag (about a minute; this always works):
   `git checkout <current tag> && STITCHCHECK_WARM=1 modal deploy scripts/cloud/serve_modal.py`
3. Faster but not guaranteed: secrets are read when a container starts, so stopping the running container
   (`modal container list`, then `modal container stop <id>`) may pick up the new value without a redeploy. Confirm
   with `GET /admin/stats` (header `X-Admin-Token`) or by redeploying if unsure.

Turn it off the same way with `STITCHCHECK_SAFE_MODE=0`.

## Checking uptime and synthetic logs

```
modal volume get stitchcheck-app uptime.jsonl - | tail -20
modal volume get stitchcheck-app uptime.jsonl - | grep '"ok": false'
```

Each line is `{ts, kind, health_ms, ok, status, version}` (`kind` is `uptime` or `synthetic`; synthetic lines add `steps` with the status and ms of each call). `version` reads "not deployed yet" until `/version` exists. Function output and errors: `modal app logs stitchcheck-web`. Run a check now: `modal run scripts/cloud/serve_modal.py::uptime` (or `::synthetic`, `::backup`).

## Checking backups

```
modal volume ls stitchcheck-backups
modal volume get stitchcheck-backups backup.jsonl - | tail -7
```

Each line is `{ts, ok, file, bytes, sessions, deleted}` or `{ts, ok: false, error}`. The backup copies the database to a private file first, runs `PRAGMA integrity_check` on the copy and only then writes it to the volume.

## Restoring a backup

1. Download the backup and check it:
   ```
   modal volume get stitchcheck-backups stitchcheck-20261106.db ./restore.db
   sqlite3 restore.db "PRAGMA integrity_check; SELECT COUNT(*) FROM session;"
   ```
2. Keep the current database: `modal volume get stitchcheck-app stitchcheck.db ./pre-restore.db`
3. Stop the app, so the running container cannot commit its open database over the restored file:
   `modal app stop stitchcheck-web`
4. Upload the backup over the live file: `modal volume put stitchcheck-app restore.db stitchcheck.db --force`
5. Redeploy the current tag (deploy from a tag, above) and run the smoke test.

Photos are not in the backup; they stay in `stitchcheck-app/photos` and the restored rows point at them.

## Demo gallery

Gallery photos live in the `stitchcheck-gallery` volume, mounted at `/gallery`. After building the folder with `scripts/ops/build_gallery.py`, upload it with `modal volume put stitchcheck-gallery <folder> / --force`, then check `curl -s $URL/demo/gallery`.

## Tools

- `scripts/ops/smoke_e2e.py URL [--photo PATH] [--no-ai] [--pin PIN] [--expect-commit SHA]`: the full checker journey against a deployed URL; a table of step, result and ms; exit 1 on any failed required step. Endpoints that answer 404 print "not deployed yet". A run with AI makes two model calls.
- `scripts/ops/load_test.py [--users N] [--garments N] [--photos N]`: local only, fake models, temporary database; p50/p95 per endpoint; exit 1 on any 5xx, dropped connection, stuck review or "database is locked".
