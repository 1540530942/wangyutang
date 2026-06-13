ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG PIP_INDEX_URL=

WORKDIR /app

COPY requirements.txt .
RUN if [ -n "$PIP_INDEX_URL" ]; then python -m pip config set global.index-url "$PIP_INDEX_URL"; fi \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8093

CMD ["python", "-m", "uvicorn", "services.webui.app:app", "--host", "0.0.0.0", "--port", "8093"]
