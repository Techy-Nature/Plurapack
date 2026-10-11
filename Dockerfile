FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY plurapack ./plurapack
COPY main.py ./
COPY index.html login.html app.js dashboard_helpers.js voice_playback.js styles.css ./

RUN python -m pip install --no-cache-dir .

CMD python -m plurapack