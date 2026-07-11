# Application image. Note: this is the WEB app only. The Paper MCQ module
# (a later phase) shells out to Auto Multiple Choice as a SEPARATE process/
# service and must NOT be linked into this image's Python process — see
# docs/architecture.md for that boundary.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN DJANGO_SECRET_KEY=build-time python manage.py collectstatic --no-input

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --no-input && gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 3"]
