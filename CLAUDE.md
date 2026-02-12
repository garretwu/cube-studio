# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Cube Studio is an open-source, cloud-native MLOps platform (tencentmusic/cube-studio) that provides end-to-end machine learning workflows: data management, model training, hyperparameter tuning, inference serving, and monitoring. It runs on Kubernetes and supports multi-cluster orchestration.

The primary language of comments and documentation is Chinese. The UI supports both Chinese and English via i18next.

## Development Environment Setup

### Docker Compose (recommended for local dev)

```bash
cd install/docker
docker compose up
```

Services: redis (port 6379), mysql (port 3306), frontend (port 80), myapp backend. The backend mounts `myapp/` as a volume for live code reloading.

### Running the Backend Directly

The backend runs inside Docker. To manually start it:

```bash
export FLASK_APP=myapp:app
python myapp/create_db.py
myapp db upgrade                    # apply database migrations
myapp fab create-admin --username admin --firstname admin --lastname admin --email admin@tencent.com --password admin
myapp init                          # create default roles, permissions, menus
python myapp/run.py                 # starts Flask dev server on 0.0.0.0:80
```

Production uses gunicorn with gevent workers (see `install/docker/entrypoint.sh`).

### Building Frontend

There are three separate frontend apps, each built independently:

```bash
# Main web UI
cd myapp/frontend && npm install && npm run build

# ML Pipeline visual editor (React Flow)
cd myapp/vision && npm install && npm run build

# Data ETL Pipeline editor
cd myapp/visionPlus && yarn && npm run build
```

`npm run start` in `myapp/frontend` starts the webpack dev server. The vision/visionPlus apps use `npm run dev` (react-app-rewired).

### Frontend Tests

```bash
cd myapp/frontend && npm test          # Jest tests
cd myapp/vision && npm test
cd myapp/visionPlus && npm test
```

### Docker Image Build

```bash
# Single platform
docker build -t ccr.ccs.tencentyun.com/cube-studio/kubeflow-dashboard:2026.01.01 -f install/docker/Dockerfile .

# Multi-platform (amd64 + arm64)
docker buildx build --platform linux/amd64,linux/arm64 -t ccr.ccs.tencentyun.com/cube-studio/kubeflow-dashboard:2026.01.01 -f install/docker/Dockerfile . --push
```

### Database Migrations

```bash
export FLASK_APP=myapp:app
myapp db migrate    # generate new migration in myapp/migrations/versions/
myapp db upgrade    # apply migrations to MySQL
```

### Celery Workers (async tasks)

```bash
# Beat scheduler
celery --app=myapp.tasks.celery_app:celery_app beat --loglevel=info

# Worker
celery --app=myapp.tasks.celery_app:celery_app worker --loglevel=info --pool=prefork -Ofair -c 2
```

## Architecture

### Backend (`myapp/`)

Flask application built on Flask-AppBuilder with built-in RBAC, using SQLAlchemy ORM with MySQL.

- **`myapp/__init__.py`** — Flask app creation, middleware setup (CORS, CSRF, compression, Talisman), `before_request` auth check, AppBuilder initialization. This is where `app`, `db`, `appbuilder`, `security_manager`, and `cache` are created and exported.
- **`myapp/config.py`** — Configuration loaded via `MYAPP_CONFIG` env var. Overridden in Docker by mounting `install/docker/config.py`.
- **`myapp/security.py`** — Custom security manager (`MyappSecurityManager`) extending FAB's security. Handles JWT, SSO, LDAP, WeChat auth.
- **`myapp/cli.py`** — Flask CLI commands registered via `@app.cli.command()`. The `myapp init` command seeds default projects, job templates, images, and pipelines from JSON files in `myapp/init/`.
- **`myapp/models/`** — SQLAlchemy models. Each `model_*.py` corresponds to a domain: `model_job.py` (Repository, Images, Job_Template, Pipeline, Task), `model_serving.py` (InferenceService), `model_notebook.py`, `model_dataset.py`, `model_team.py` (Project, Project_User), `model_nni.py`, etc. Models extend `MyappModelBase` (in `base.py`) which provides bilingual label columns.
- **`myapp/views/`** — Flask views and REST APIs. `baseApi.py` and `baseModelFormApi.py` extend Flask-AppBuilder's `ModelRestApi` with custom CRUD, permissions, and fieldset handling. Each `view_*.py` registers endpoints for a domain (pipelines, notebooks, serving, k8s resources, etc.). Views are imported via `views/__init__.py`.
- **`myapp/tasks/`** — Celery async task definitions (`celery_app.py`).
- **`myapp/utils/core.py`** — Large utility module (~96KB) with helpers for K8s operations, caching, and general-purpose functions.
- **`myapp/migrations/`** — Alembic database migration versions.

### Frontend (`myapp/frontend/`)

React 17 + TypeScript SPA using Ant Design, Webpack 5, MobX state management. Key areas:
- `src/pages/` — Page components for each platform feature
- `src/components/` — Reusable UI components
- `src/api/` — Axios API client
- `src/store/` — MobX stores
- `src/locales/` — i18n translation files (zh/en)
- `src/routerConfig.tsx` — Route definitions

### Visual Pipeline Editors

- **`myapp/vision/`** — ML pipeline drag-and-drop editor using React Flow + Redux Toolkit + Ant Design. Builds to `myapp/static/appbuilder/frontend/`
- **`myapp/visionPlus/`** — ETL pipeline editor, same tech stack. Uses `yarn` for dependency installation.

### Job Templates (`job-template/`)

Pre-built ML pipeline operator templates (PyTorch, TensorFlow, Ray, Spark, XGBoost, DataX, etc.). Each template has its own Dockerfile and launcher script. Templates are loaded into the platform via `myapp init` from JSON configs in `myapp/init/`.

### Docker Images (`images/`)

Dockerfiles for GPU base images, Jupyter notebooks (deeplearning/machinelearning/bigdata variants), model serving (TFServing, Triton, TorchServe), and multi-CUDA images.

### Deployment (`install/`)

- `install/docker/` — Docker Compose local dev setup, backend Dockerfile, entrypoint, and config overrides
- `install/kubernetes/` — K8s deployment manifests for production (Prometheus monitoring, various platform components)

## Key Environment Variables

| Variable | Purpose |
|----------|---------|
| `STAGE` | `dev` (Flask debug), `prod` (gunicorn), `build` (build frontends) |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` | Redis connection |
| `MYSQL_SERVICE` | SQLAlchemy DB URI, e.g. `mysql+pymysql://root:admin@mysql:3306/kubeflow?charset=utf8mb4` |
| `ENVIRONMENT` | `DEV` or `PROD` |
| `FLASK_APP` | Must be set to `myapp:app` |
| `MYAPP_CONFIG` | Python config module path (default: `myapp.config`) |

## Conventions

- Backend views follow the pattern: model in `myapp/models/model_X.py`, view in `myapp/views/view_X.py`, registered via import in `myapp/views/__init__.py`.
- Model labels use Flask-Babel's `lazy_gettext` (`_()`) for internationalization.
- The `myapp` CLI command is installed via `myapp/bin/myapp` and wraps Flask CLI with `FLASK_APP=myapp:app`.
- Image tags follow `YYYY.MM.DD` version format (e.g., `2026.01.01`).
- Python 3.9, Node 17+ for frontend builds.
