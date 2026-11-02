FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY static ./static
COPY data ./data

# Jalankan sebagai pengguna biasa, bukan root.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
# Platform hosting (Render, Railway, dsb.) biasanya mengisi variabel PORT.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
