# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python library that integrates FastAPI with Google Cloud Tasks and Cloud Scheduler, providing a Celery-like `.delay()` / `.scheduler()` API backed by GCP services. Published on PyPI as `fastapi-cloud-tasks`.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r test-requirements.txt

# Run tests
pytest test_delayer.py -v

# Run a single test
pytest test_delayer.py::test_delayer_init_with_retry_config -v

# Pre-commit hooks (isort, black, flake8)
pre-commit install
pre-commit run --all-files

# Run example locally (requires cloud-tasks-emulator running on :8123)
uvicorn examples.simple.main:app --reload --port 8000
```

## Architecture

The library provides two main entry points via builder functions that return custom `APIRoute` subclasses:

- **`DelayedRouteBuilder`** (`delayed_route.py`) — Returns a route class that adds `.delay()` and `.options()` to FastAPI endpoints. Creates Cloud Tasks via the async client (`CloudTasksAsyncClient`). Auto-creates queues on first use.
- **`ScheduledRouteBuilder`** (`scheduled_route.py`) — Returns a route class that adds `.scheduler()` to endpoints. Creates Cloud Scheduler jobs (sync client). Uses delete-and-recreate with change detection to update jobs.

Both builders produce `APIRoute` mixins meant to be passed as `route_class` to `APIRouter`.

**Core classes:**
- `Requester` (`requester.py`) — Base class shared by `Delayer` and `Scheduler`. Handles URL construction, body serialization, header extraction, and query params by introspecting FastAPI's `route.dependant` and `route.param_convertors`. Uses `ujson` if available, falls back to stdlib `json`.
- `Delayer` (`delayer.py`) — Builds `CreateTaskRequest` protobuf, supports countdown scheduling and task_id deduplication. Has built-in retry with exponential backoff via `google.api_core.retry`.
- `Scheduler` (`scheduler.py`) — Builds `CreateJobRequest` protobuf, supports cron schedules and time zones. Compares existing jobs via protobuf equality to avoid unnecessary recreation.

**Hooks system** (`hooks.py`) — Pre-create hooks transform the request protobuf before sending to GCP. Used for auth (OIDC/OAuth tokens), deadlines, etc. Composable via `chained_hook`.

**Dependencies** (`dependencies.py`) — FastAPI `Depends`-compatible utilities: `CloudTasksHeaders` extracts Cloud Tasks HTTP headers, `max_retries(n)` stops retrying by returning HTTP 200.

## Key Design Decisions

- Delayed tasks use the **async** Cloud Tasks client; scheduled tasks use the **sync** Scheduler client.
- The `@task_default_options` decorator stores kwargs on `fn._delayOptions`, which the route mixin reads at delay time.
- `Requester._url()` uses `route.path_format` with path param convertors — not `url_path_for` — to build the target URL.
- Version is read from `version.txt` at build time.
