FROM python:3.11-slim
WORKDIR /srv
COPY api-python/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 agent
COPY api-python/app ./app
COPY api-python/platform_runtime ./platform_runtime
COPY packs ./packs
RUN mkdir -p /srv/data && chown -R agent:agent /srv/data
ENV PACKS_DIR=/srv/packs
USER agent
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "131072"]
