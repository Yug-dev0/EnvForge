FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
RUN uv pip install --system 'uv>=0.11.1,<0.12'
WORKDIR /opt/envforge
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv export --project apps/api --frozen --no-dev --no-emit-project --no-emit-package verifiers --no-hashes --output-file /tmp/runtime-constraints.txt \
    && uv build --wheel --out-dir /tmp/wheels && uv pip install --system --no-config --no-sources --constraint /tmp/runtime-constraints.txt /tmp/wheels/*.whl ./apps/api \
    && useradd --uid 10001 --create-home envforge && mkdir /data && chown envforge /data
USER envforge
ENV ENVFORGE_DB_PATH=/data/envforge.sqlite3
ENV UV_CACHE_DIR=/tmp/uv-cache PYTHONDONTWRITEBYTECODE=1
RUN uv run --no-project --no-sync python -c "import json; from envforge.provenance import implementation_digest; r=json.load(open('deploy/qa-report.json')); assert r['status']=='PASS' and r['implementation_digest']==implementation_digest(), 'Regenerate deploy/qa-report.json before building'" \
    && cp deploy/qa-report.json /data/qa-report.json
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
