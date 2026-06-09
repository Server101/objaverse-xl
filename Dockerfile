FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OBJAVERSE_API_HOST=0.0.0.0 \
    OBJAVERSE_API_PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt setup.py README.md ./
COPY objaverse ./objaverse
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn objaverse.api:app --host ${OBJAVERSE_API_HOST} --port ${OBJAVERSE_API_PORT}"]
