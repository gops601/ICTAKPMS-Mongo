FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FLASK_APP=run.py
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# strip Windows CRLF endings: a "\r" after #!/bin/sh makes exec fail with "no such file or directory"
RUN sed -i 's/\r$//' entrypoint.sh \
    && chmod +x entrypoint.sh \
    && useradd --create-home appuser \
    && mkdir -p /app/uploads \
    && chown -R appuser /app
USER appuser

EXPOSE 5000
ENTRYPOINT ["./entrypoint.sh"]
