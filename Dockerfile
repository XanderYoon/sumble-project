FROM python:3.14-slim@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt         && useradd --create-home --uid 10001 app         && mkdir -p /var/log/sumble && chown app:app /var/log/sumble
COPY --chown=app:app src/ ./src/
USER app
EXPOSE 8000
CMD ["uvicorn", "sumble_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
