# SRE Agent Pipeline Runbook

This document describes the GitLab CI workflow for the `sre_agent` branch pipeline on:

`feature/sre-c-core-infra`

It focuses on pipeline stages, jobs, usage, and the GitLab-side configuration required to keep the pipeline working.

## 1. Scope

This pipeline is intentionally branch-scoped.

- Branch: `feature/sre-c-core-infra`
- Registry format: `10.11.4.5:5000/<namespace>/<image-name>:<tag>`
- Main goal:
  build the `sre_agent` web image, provide a preview container, validate runtime and business behavior, publish snapshot images to Nexus, and support manual release promotion

Other branches are excluded by `workflow: rules` in [`.gitlab-ci.yml`](/home/kevin/project/cube-studio/.gitlab-ci.yml).

## 2. Workflow Overview

The pipeline has four stages:

1. `build`
2. `preview`
3. `release`
4. `cleanup`

Normal commit flow:

1. Generate image metadata and tags
2. Rebuild the base image only when dependency inputs change
3. Build the web image from the newest available base image
4. Start a preview container with unique name and ports
5. Run environment validation and business validation
6. Push validated snapshot image to Nexus
7. Optionally promote the validated image to a formal release tag by manual trigger

Scheduled flows:

- `weekly_build`: builds a weekly image and publishes it after validation
- `base_refresh`: forces a new base image build, rebuilds the web image, validates it, and publishes it
- `cleanup`: removes expired preview containers and stale managed Docker state

## 3. Stage And Job Guide

### 3.1 Build Stage

| Job | Purpose | Trigger |
| --- | --- | --- |
| `prepare_sre_agent_metadata` | Generates common metadata, timestamps, and image tags. | Commit, `weekly_build`, `base_refresh` |
| `build_sre_agent_base` | Builds the base image only when dependency inputs change or a base rebuild is forced. | Dependency change, `FORCE_BASE_BUILD=1`, `base_refresh` |
| `build_sre_agent_web` | Builds the commit snapshot web image. | Normal commit |
| `weekly_build_sre_agent_web` | Builds the weekly web image. | `weekly_build` schedule |
| `refresh_build_sre_agent_web` | Builds the web image during base refresh. | `base_refresh` schedule |

Dependency inputs that trigger a base rebuild:

- [fault_injector/requirements.txt](/home/kevin/project/cube-studio/fault_injector/requirements.txt)
- [sre_agent/requirements.txt](/home/kevin/project/cube-studio/sre_agent/requirements.txt)
- [sre_agent/docker/Dockerfile.base](/home/kevin/project/cube-studio/sre_agent/docker/Dockerfile.base)
- [sre_agent/frontend/package.json](/home/kevin/project/cube-studio/sre_agent/frontend/package.json)
- [sre_agent/frontend/package-lock.json](/home/kevin/project/cube-studio/sre_agent/frontend/package-lock.json)

### 3.2 Preview Stage

| Job | Purpose | Trigger |
| --- | --- | --- |
| `preview_sre_agent_web` | Starts a preview container, avoids name and port conflicts, waits for health checks, and prints preview URLs. | Normal commit |

Preview policy:

- container name is unique per pipeline/job
- backend and frontend host ports are chosen from configurable random ranges
- preview TTL defaults to 72 hours
- TTL is enforced by cleanup

### 3.3 Release Stage

| Job | Purpose | Trigger |
| --- | --- | --- |
| `validate_sre_agent_runtime` | Validates environment readiness by starting the container and checking backend/frontend availability. | Commit, `weekly_build`, `base_refresh` |
| `validate_sre_agent_business` | Runs selected backend and fault injection business tests in the base-image environment. | Commit, `weekly_build`, `base_refresh` |
| `publish_sre_agent_snapshot` | Pushes validated snapshot images to Nexus and prints `docker pull` addresses. | After both validations pass |
| `promote_sre_agent_release` | Manual release promotion job; re-tags the validated web image as a formal release image. | Manual |

Validation coverage is intentionally split into two layers:

- Environment validation:
  container health, backend `/openapi.json`, frontend `/`
- Business validation:
  backend startup behavior and fault-injector scenario behavior

### 3.4 Cleanup Stage

| Job | Purpose | Trigger |
| --- | --- | --- |
| `cleanup_sre_agent_runtime` | Removes expired preview containers, images older than 7 days, and old Docker cache while preserving the newest protected images. | `cleanup` schedule |

Cleanup retention policy:

- remove preview containers once TTL expires
- remove managed Docker state older than 7 days
- preserve the newest base image
- preserve the newest commit snapshot image
- preserve the newest weekly image
- preserve the newest formal release image

## 4. Tag Strategy

| Image Type | Tag Rule | Example |
| --- | --- | --- |
| Base image | `base-YYYYMMDDHHMM` | `base-202604101730` |
| Commit snapshot web image | `CI_COMMIT_SHORT_SHA-YYYYMMDDHHMM` | `a1b2c3d4-202604101735` |
| Weekly web image | `weekly-YYYYMMDDHHMM` | `weekly-202604101700` |
| Manual release image | `VERSION-YYYYMMDDHHMM` | `1.0.1-202604101800` |

Short commit ID uses GitLab predefined variable `CI_COMMIT_SHORT_SHA`.

Default release version source:

- [sre_agent/VERSION](/home/kevin/project/cube-studio/sre_agent/VERSION)

If `RELEASE_VERSION` is provided manually at trigger time, it overrides the version file.

## 5. GitLab Configuration Checklist

### 5.1 Project Variables

Create these in GitLab `Settings -> CI/CD -> Variables`.

| Variable | Required | Recommended Setting | Purpose |
| --- | --- | --- | --- |
| `NEXUS_REGISTRY` | Yes | `10.11.4.5:5000` | Nexus Docker registry address |
| `NEXUS_USERNAME` | Yes | `kevin` | Nexus login username |
| `NEXUS_PASSWORD` | Yes | masked + protected | Nexus login password or token |
| `PREVIEW_DOCKER_HOST` | Recommended | runner-accessible Docker host | Lets preview containers survive beyond the job container |
| `PREVIEW_PUBLIC_HOST` | Recommended | preview host IP | Printed in preview access URLs |
| `SRE_OPENAI_API_KEY` | Optional | masked | Runtime key used by the app if you want real LLM capability in preview |
| `FORCE_BASE_BUILD` | Optional | `1` only when needed | Forces base rebuild even without dependency changes |
| `RELEASE_VERSION` | Optional | manual input | Used by manual release promotion |
| `FEISHU_WEBHOOK_URL` | Recommended | masked + protected | Feishu bot webhook URL used for pipeline failure notifications |
| `FEISHU_NOTIFY_ON_COMMIT_FAILURE` | Optional | `0` | Set to `1` if normal commit pipeline failures should also notify the Feishu group |

Security notes:

- do not write `NEXUS_PASSWORD` in the repository
- store `NEXUS_PASSWORD` as a masked protected variable
- if you later replace the password with a token, keep the same variable name so scripts do not change

### 5.2 Runner Requirements

The pipeline assumes the runner can support:

- Docker-in-Docker or equivalent Docker build capability
- access to `10.11.4.5:5000`
- access to the optional preview Docker host if `PREVIEW_DOCKER_HOST` is used

If preview needs to remain accessible after the job finishes, use a persistent Docker host instead of only the default dind service.

## 6. Schedule Checklist

Create schedules in GitLab `Build -> Pipeline schedules`, all targeting:

`feature/sre-c-core-infra`

| Schedule Name | Recommended Time | Variable |
| --- | --- | --- |
| `weekly_build` | Every Friday 17:00 | `PIPELINE_KIND=weekly_build` |
| `base_refresh` | Monthly or every two weeks | `PIPELINE_KIND=base_refresh` |
| `cleanup` | Weekly during off-hours | `PIPELINE_KIND=cleanup` |

Recommended practice:

- keep `weekly_build` at a fixed business-visible time
- run `cleanup` when preview usage is low
- run `base_refresh` outside peak hours because it rebuilds more layers

## 7. Failure Notification

The pipeline supports Feishu failure notifications through a custom bot webhook.

Recommended first-step policy:

- always notify for `weekly_build`
- always notify for `base_refresh`
- always notify for `cleanup`
- always notify for release validation, snapshot publish, and manual release promotion failures
- do not notify normal commit failures unless `FEISHU_NOTIFY_ON_COMMIT_FAILURE=1`

Notification payload includes:

- project
- branch
- pipeline kind
- stage
- job
- status
- short commit SHA
- trigger user
- pipeline URL
- job URL

Notification format:

- Feishu interactive card
- red failure header
- structured fields for project, branch, pipeline kind, stage, job, commit, user, and time
- direct buttons for pipeline and job links

Configuration:

1. Create a Feishu group bot webhook
2. Store the webhook URL in GitLab variable `FEISHU_WEBHOOK_URL`
3. Keep the variable masked and protected
4. Optionally set `FEISHU_NOTIFY_ON_COMMIT_FAILURE=1` if the team also wants commit-failure alerts

### 7.1 Feishu Bot Setup Checklist

Recommended maintenance steps:

1. Create or select the Feishu group used for CI notifications
2. Add a custom bot in Feishu and copy the webhook URL
3. Open GitLab project `Settings -> CI/CD -> Variables`
4. Create variable `FEISHU_WEBHOOK_URL`
5. Paste the webhook URL as the variable value
6. Mark the variable as `Masked`
7. Mark the variable as `Protected` if your protected-branch policy allows it
8. Save the variable and run a test pipeline

Recommended related variables:

| Variable | Suggested Value | Purpose |
| --- | --- | --- |
| `FEISHU_WEBHOOK_URL` | real webhook URL | Sends pipeline failure messages to the Feishu group |
| `FEISHU_NOTIFY_ON_COMMIT_FAILURE` | `0` or `1` | Controls whether normal commit pipeline failures also notify Feishu |

Maintenance notes:

- do not commit the webhook URL into the repository
- if the bot is rotated or recreated, only update the GitLab variable and the pipeline code does not need to change
- if the group reports too much noise, keep `FEISHU_NOTIFY_ON_COMMIT_FAILURE=0`
- if the team wants broader visibility later, change `FEISHU_NOTIFY_ON_COMMIT_FAILURE` to `1`

## 8. How To Use The Pipeline

### 8.1 Normal Commit Pipeline

Use this for daily development on `feature/sre-c-core-infra`.

Expected behavior:

1. commit to the branch
2. pipeline starts automatically
3. web snapshot image is built
4. preview container is created
5. validations run
6. validated snapshot image is pushed to Nexus

Snapshot image pull form:

```bash
docker pull 10.11.4.5:5000/cube-studio/sre-agent-web:<shortsha-yyyymmddhhmm>
```

### 8.2 Weekly Build

Use this as a stable weekly snapshot.

Expected behavior:

1. GitLab schedule triggers `PIPELINE_KIND=weekly_build`
2. weekly image is built with `weekly-YYYYMMDDHHMM`
3. validations run
4. image is pushed to Nexus

Weekly image pull form:

```bash
docker pull 10.11.4.5:5000/cube-studio/sre-agent-web:weekly-<yyyymmddhhmm>
```

### 8.3 Base Refresh

Use this when you want to refresh system and dependency layers even if repository files did not change.

Expected behavior:

1. GitLab schedule triggers `PIPELINE_KIND=base_refresh`
2. base image is rebuilt
3. web image is rebuilt on top of the new base
4. validations run
5. images are published to Nexus

### 8.4 Manual Release Promotion

Use this when a validated snapshot needs to become a formal release image.

How to use:

1. open a successful pipeline on `feature/sre-c-core-infra`
2. run manual job `promote_sre_agent_release`
3. optionally provide `RELEASE_VERSION`
4. if not provided, CI reads [sre_agent/VERSION](/home/kevin/project/cube-studio/sre_agent/VERSION)

Release image pull form:

```bash
docker pull 10.11.4.5:5000/cube-studio/sre-agent-web:<version-yyyymmddhhmm>
```

## 9. Job Output You Should Expect

In normal successful runs, the logs should show:

- generated image tags
- preview backend and frontend URLs
- validation success logs
- `docker pull` address for each published image

If release promotion succeeds, the logs should also show the formal release pull command.

If Feishu notification is enabled and a watched job fails, the group bot message should also include the pipeline and job links.

## 10. Common Operating Notes

- If preview does not need to persist after the job ends, `PREVIEW_DOCKER_HOST` can be omitted.
- If preview must be shared with others, configure `PREVIEW_DOCKER_HOST` and `PREVIEW_PUBLIC_HOST`.
- If base dependencies change frequently, keep using change-based rebuilds and reserve `base_refresh` for periodic hygiene.
- If release strategy changes later, the safest extension is to add environment-specific deploy jobs after `publish_sre_agent_snapshot`, not to mix deployment logic into build jobs.

## 11. Related Files

| File | Purpose |
| --- | --- |
| [`.gitlab-ci.yml`](/home/kevin/project/cube-studio/.gitlab-ci.yml) | Main branch-scoped GitLab pipeline definition |
| [`README.md`](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/README.md) | Compact CI reference for scripts, tags, and variables |
| [`common.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/common.sh) | Shared CI helper functions |
| [`run_preview_container.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/run_preview_container.sh) | Preview container creation logic |
| [`validate_runtime_smoke.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/validate_runtime_smoke.sh) | Runtime smoke validation |
| [`validate_business_suite.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/validate_business_suite.sh) | Business validation |
| [`promote_release.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/promote_release.sh) | Manual release promotion |
| [`notify_feishu.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/notify_feishu.sh) | Feishu failure notification |
