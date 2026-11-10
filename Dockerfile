FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
RUN useradd --system app \
    && mkdir _logs_ \
    && chown app:app _logs_
USER app

EXPOSE 8000
CMD ["python", "src/main.py"]
