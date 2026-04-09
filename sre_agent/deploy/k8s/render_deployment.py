#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


def main() -> int:
    deploy_dir = Path(__file__).resolve().parent
    docker_dir = deploy_dir.parents[1] / "docker"
    image_env_path = docker_dir / "image.env"
    template_path = deploy_dir / "deployment.template.yaml"
    output_path = deploy_dir / "deployment.yaml"

    image_env: dict[str, str] = {}
    if image_env_path.exists():
        for line in image_env_path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            image_env[key.strip()] = value.strip()

    image_registry = str(os.environ.get("IMAGE_REGISTRY", image_env.get("IMAGE_REGISTRY", ""))).strip()
    image_namespace = str(os.environ.get("IMAGE_NAMESPACE", image_env.get("IMAGE_NAMESPACE", "cube-studio"))).strip() or "cube-studio"
    app_name = str(os.environ.get("APP_NAME", image_env.get("APP_NAME", "sre-agent-web"))).strip() or "sre-agent-web"
    image_tag = str(os.environ.get("IMAGE_TAG", image_env.get("IMAGE_TIMESTAMP", ""))).strip()

    if not image_tag:
        raise SystemExit("IMAGE_TAG is required. Run sre_agent/docker/build_images.sh first or export IMAGE_TAG.")

    if image_registry:
        image_ref = f"{image_registry}/{image_namespace}/{app_name}:{image_tag}"
    else:
        image_ref = f"{image_namespace}/{app_name}:{image_tag}"

    rendered = template_path.read_text(encoding="utf-8").replace("${IMAGE_REF}", image_ref)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"rendered {output_path} with image {image_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
