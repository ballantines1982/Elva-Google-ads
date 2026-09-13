FROM python:3.11-slim

WORKDIR /app

# Installera beroenden separat för bättre layer-caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway/Render sätter PORT vid deploy - servern läser den i config.py.
# Vi exponerar 8000 som ett rimligt default för lokal `docker run`.
EXPOSE 8000

# Skriv inte .pyc-filer, och flusha stdout direkt (viktigt för loggning i containers).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["python", "server.py"]
