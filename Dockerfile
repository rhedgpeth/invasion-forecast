# One image for everywhere: your laptop and a Runpod Pod run exactly the same thing.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/srv/data

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app
COPY web web
COPY packs packs

# Run as an unprivileged user; generated games are written to /srv/data.
RUN useradd --create-home game && mkdir -p data && chown game data
USER game

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
