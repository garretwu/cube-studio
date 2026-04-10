ARG BASE_IMAGE=sre-agent-base:latest
FROM ${BASE_IMAGE}

ENV PATH=/usr/local/bin:${PATH} \
    PYTHONPATH=/app \
    HOME=/app \
    BACKEND_PORT=8000 \
    FRONTEND_PORT=8080 \
    STARTUP_CONFIG_PATH=/app/sre_agent/conf/config.yaml \
    STARTUP_BACKEND_HOST=0.0.0.0 \
    STARTUP_BACKEND_URL_HOST=127.0.0.1 \
    SRE_KUBECONFIG=/app/sre_agent/conf/kube.conf

WORKDIR /app

COPY lib /app/lib
COPY fault_injector /app/fault_injector
COPY sre_agent /app/sre_agent

RUN mkdir -p /app/data/checkpoints /app/data/llm_logs /app/data/memory /app/data/knowledge_db /app/data/wal \
    && chmod +x /app/sre_agent/docker/container_entrypoint.sh

EXPOSE 8000 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
  CMD ["python", "/app/sre_agent/docker/healthcheck.py"]

CMD ["bash", "/app/sre_agent/docker/container_entrypoint.sh"]
