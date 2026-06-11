ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./audio_recognition

EXPOSE 8095
ENV PYTHONPATH=/app
CMD ["uvicorn", "audio_recognition.web.server:app", "--host", "0.0.0.0", "--port", "8095"]
