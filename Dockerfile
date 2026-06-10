FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m playwright install chromium

COPY config ./config
COPY examples ./examples
COPY src ./src
COPY docker/entrypoint.sh ./docker/entrypoint.sh

RUN chmod +x ./docker/entrypoint.sh \
    && mkdir -p /app/data

EXPOSE 8000

ENTRYPOINT ["./docker/entrypoint.sh"]
CMD ["python", "-m", "airmoney", "web", "--host", "0.0.0.0", "--port", "8000"]
