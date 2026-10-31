# The API image (design §11). The pipeline package ships in the same wheel but
# is never run here: ingestion and migrations run from a dev machine against
# the prod DATABASE_URL.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Bake bge-small-en-v1.5 into the layer. Left to itself fastembed downloads it
# to a temp dir on the first /ask after every cold start (deployment.md §2.1).
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
# ...and never phones home at runtime: a CDN hiccup must not fail an answer.
ENV HF_HUB_OFFLINE=1

RUN useradd --create-home app && chown -R app /opt/fastembed
USER app

EXPOSE 8000
# One worker on purpose: api/limits.py keeps its counters in process.
# --host 0.0.0.0 because uvicorn's default 127.0.0.1 refuses traffic from
# outside the container.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
