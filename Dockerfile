FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY plurapack ./plurapack
COPY main.py ./
COPY index.html login.html viewer.html app.js dashboard_helpers.js voice_playback.js styles.css ./

# Which chat platforms to install: stoat, fluxer, or stoat,fluxer (the default).
# On Railway, set a PLURAPACK_PLATFORMS service variable to change this.
ARG PLURAPACK_PLATFORMS=stoat,fluxer

RUN python -m pip install --no-cache-dir ".[${PLURAPACK_PLATFORMS}]"

CMD python -m plurapack