FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
# Vigia: se o app parar de responder (loop de eventos travado), o Swarm troca o
# container sozinho em ~2 min, em vez de ficar fora do ar até alguém perceber.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=4 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
