# GitLab CI For `feature/sre-c-core-infra`

This document summarizes the branch-scoped GitLab CI assets for the `sre_agent` web stack.

Primary usage guide:

- [PIPELINE_RUNBOOK.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md)

## Files At A Glance

| File | Purpose |
| --- | --- |
| `ci/common.sh` | Shared helpers for logging, registry auth, image naming, and timestamp parsing. |
| `ci/generate_metadata.sh` | Generates the timestamped image tags and writes `dist/pipeline.env`. |
| `ci/resolve_latest_base_tag.sh` | Queries Nexus and resolves the newest published `base-YYYYMMDDHHMM` tag. |
| `ci/build_base_image.sh` | Builds the base image and exports it as a tar artifact. |
| `ci/build_web_image.sh` | Builds the web image from the newest available base image and exports it as a tar artifact. |
| `ci/run_preview_container.sh` | Starts a preview container with unique ports and a unique name, then waits for health checks to pass. |
| `ci/validate_runtime_smoke.sh` | Runs container startup, health, backend OpenAPI, and frontend index smoke validation. |
| `ci/validate_business_suite.sh` | Runs selected backend and fault injection business tests inside the base image environment. |
| `ci/push_images.sh` | Pushes the generated image tar artifacts to Nexus and prints `docker pull` addresses. |
| `ci/promote_release.sh` | Manually promotes a validated build to a formal release tag using a version plus timestamp. |
| `ci/notify_feishu.sh` | Sends Feishu interactive card notifications when failure notification conditions are met. |
| `ci/cleanup_docker_state.sh` | Removes preview containers and managed images older than 7 days while preserving the newest ones. |

## Pipeline Scope

The jobs in the root `.gitlab-ci.yml` are intentionally limited to the branch:

`feature/sre-c-core-infra`

Other branches are not matched by the new SRE Agent CI rules.

## Required CI Variables

| Variable | Purpose |
| --- | --- |
| `NEXUS_REGISTRY` | Nexus Docker registry host, for example `nexus.example.com:5001`. |
| `NEXUS_USERNAME` | Username used for Docker login and tag lookup API calls. Store it as a protected CI variable. |
| `NEXUS_PASSWORD` | Password or token used for Docker login and tag lookup API calls. Store it as a masked protected CI variable. |
| `PREVIEW_DOCKER_HOST` | Optional remote Docker host for persistent preview containers, for example `tcp://10.10.10.20:2375`. |
| `PREVIEW_PUBLIC_HOST` | Optional public host or IP printed in preview URLs. |
| `SRE_OPENAI_API_KEY` | Optional preview-time runtime key; if omitted, the container falls back to the placeholder startup key. |
| `FORCE_BASE_BUILD` | Optional `1` to force a base image rebuild even when dependency files did not change. |
| `RELEASE_VERSION` | Optional manual release version, for example `1.0.1`; if omitted, CI reads `sre_agent/VERSION`. |
| `FEISHU_WEBHOOK_URL` | Optional Feishu bot webhook URL used for failure notifications. Store it as a masked protected CI variable. |
| `FEISHU_NOTIFY_ON_COMMIT_FAILURE` | Optional `1` to also notify normal commit pipeline failures; default behavior only notifies non-commit flows. |

## Schedule Variables

GitLab cron schedules must be created in the GitLab UI for the same branch.

| Schedule | Branch | Variable |
| --- | --- | --- |
| Weekly build every Friday 17:00 | `feature/sre-c-core-infra` | `PIPELINE_KIND=weekly_build` |
| Monthly or bi-weekly base refresh | `feature/sre-c-core-infra` | `PIPELINE_KIND=base_refresh` |
| Weekly cleanup at your preferred time | `feature/sre-c-core-infra` | `PIPELINE_KIND=cleanup` |

## Tag Rules

| Image Type | Tag Format |
| --- | --- |
| Base image | `base-YYYYMMDDHHMM` |
| Commit web image | `CI_COMMIT_SHORT_SHA-YYYYMMDDHHMM` |
| Weekly web image | `weekly-YYYYMMDDHHMM` |
| Manual release image | `VERSION-YYYYMMDDHHMM` |

## Release Strategy

The pipeline now separates snapshot publication from formal release promotion:

- `publish_sre_agent_snapshot` automatically pushes validated commit or weekly images to Nexus.
- `promote_sre_agent_release` is a manual job that re-tags the validated web image as `VERSION-YYYYMMDDHHMM`.
- If `RELEASE_VERSION` is not provided at trigger time, the job reads the default version from `sre_agent/VERSION`.

## Validation Coverage

The release gate is split into two jobs:

- Environment validation:
  container startup, Docker health, backend `/openapi.json`, and frontend `/`
- Business validation:
  `sre_agent/tests/test_start_frontend_backend.py`, `fault_injector/tests/unit/features/scenarios/test_scenarios.py`, and `python -m fault_injector list-scenarios`

GitLab predefined variable reference for the short commit ID:

`CI_COMMIT_SHORT_SHA` is documented by GitLab as the first 8 characters of `CI_COMMIT_SHA`.
