ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app

ARG PIP_INDEX_URL=

COPY requirements.txt .
RUN if [ -n "$PIP_INDEX_URL" ]; then python -m pip config set global.index-url "$PIP_INDEX_URL"; fi \
    && pip install --no-cache-dir -r requirements.txt
COPY . ./audio_recognition

EXPOSE 8095
ENV PYTHONPATH=/app
CMD ["uvicorn", "audio_recognition.web.server:app", "--host", "0.0.0.0", "--port", "8095"]
