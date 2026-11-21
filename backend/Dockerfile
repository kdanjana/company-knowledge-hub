# Backend image (used by both the "backend" and "worker" services).
# Build context is the project root, so the test data in data/ can be copied too.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend

# Install packages first, so code changes do not reinstall everything.
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ /app/backend/
COPY data/ /app/data/

# Windows editors may save the script with CRLF line endings, which breaks `sh`.
# /app/uploads is created here so the "uploads" volume is owned by appuser (otherwise it is not writable).
RUN sed -i 's/\r$//' /app/backend/entrypoint.sh \
    && mkdir -p /app/uploads \
    && useradd --create-home appuser \
    && chown -R appuser /app

USER appuser
EXPOSE 8000

ENTRYPOINT ["sh", "/app/backend/entrypoint.sh"]
CMD ["web"]
