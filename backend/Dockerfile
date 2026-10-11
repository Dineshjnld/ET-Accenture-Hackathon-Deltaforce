FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/backend

# System deps for PDF parsing (pdfplumber/pypdf are pure python; poppler only
# needed for optional OCR which is NOT installed by default).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
RUN pip install --upgrade pip && pip install -e .

COPY backend/app ./app
COPY backend/scripts ./scripts

# Non-root runtime user; upload volume mounted at /data
RUN useradd -m -u 10001 regulens && mkdir -p /data/uploads /data/samples \
  && chown -R regulens:regulens /srv/backend /data
USER regulens

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
