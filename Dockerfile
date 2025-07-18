FROM python:3.11-slim

WORKDIR /app

COPY . /app

RUN pip install poetry

ENV POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_CACHE_DIR=/tmp/poetry_cache

RUN poetry install --only main --no-interaction --no-ansi && rm -rf $POETRY_CACHE_DIR

EXPOSE 8080

CMD ["poetry", "run", "uvicorn", "src.invo_service.service:app", \
     "--host", "0.0.0.0", "--port", "8080", "--reload"]
