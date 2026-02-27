# Repository Guidelines

## Project Structure & Module Organization
This repository combines platform artifacts and new Auto-SRE tooling.
- `load_simulator/`: Python package for the load simulator (CLI, agents, orchestrator, config, metrics).
- `myapp/`: main Cube Studio backend code and DB migrations.
- `images/`: Dockerfiles and image build contexts (serving, notebook, base images).
- `install/`: Kubernetes install manifests and ops scripts.
- `job-template/`: runnable job templates (e.g., `datax`, `yolov8`, deploy-service).
- `reqs/` and root `*.md`: requirements, design, and review docs.

Prefer keeping new implementation work inside the nearest existing module (for example, add simulator features under `load_simulator/agents` or `load_simulator/metrics`).

## Build, Test, and Development Commands
No single root build system is enforced; use module-specific commands.
- `python -m load_simulator list-scenarios`: list supported simulator workloads.
- `python -m load_simulator validate-config <config.yaml>`: validate YAML config before runs.
- `python -m load_simulator run --config <config.yaml> --output-format json`: execute a load session.
- `python -m load_simulator run --duration 60 --concurrency 8`: quick override-based smoke run.
- `bash images/<subdir>/build.sh` or `bash job-template/job/<name>/build.sh`: build image/template artifacts.

## Coding Style & Naming Conventions
Python code should follow existing style:
- 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes.
- Keep type hints on public APIs and config models (see `load_simulator/config/schema.py`).
- Keep modules focused; shared abstractions belong in `base.py` files.
- Favor small, composable async functions in simulator agents.

## Testing Guidelines
There is currently no unified root `tests/` suite. For simulator changes, treat these as minimum checks:
- `python -m load_simulator validate-config ...`
- one representative `python -m load_simulator run ...` smoke execution
- include failure-path checks when modifying agent error handling

When adding tests, use `pytest` with files named `test_<feature>.py` and place them in a local `tests/` directory near the module being changed.

## Commit & Pull Request Guidelines
Recent history follows conventional prefixes such as `feat:`, `docs:`, and `fix:`. Keep commits scoped and descriptive (e.g., `feat(load_simulator): add notebook latency metric`).

PRs should include:
- clear problem statement and scope
- linked issue/design doc when applicable
- verification evidence (commands run, key output, screenshots for UI/docs changes)
- notes on backward compatibility or migration impact
