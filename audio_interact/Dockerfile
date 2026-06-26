ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
WORKDIR /app
ARG PIP_INDEX_URL=
COPY requirements.txt .
RUN if [ -n "$PIP_INDEX_URL" ]; then python -m pip config set global.index-url "$PIP_INDEX_URL"; fi \
    && pip install --no-cache-dir -r requirements.txt
COPY server.py wake_state.py ./
EXPOSE 8097
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8097"]
