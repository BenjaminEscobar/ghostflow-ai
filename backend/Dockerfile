# Playwright's image ships Chromium and its system dependencies. Its tag must match the
# playwright version pinned in pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

WORKDIR /app
COPY pyproject.toml ./
COPY ghostflow ./ghostflow
RUN pip install --no-cache-dir .

USER pwuser
EXPOSE 8000
CMD ["uvicorn", "ghostflow.main:app", "--host", "0.0.0.0", "--port", "8000"]
