FROM ghcr.io/astral-sh/uv:0.11.19 AS uv

FROM python:3.13.9-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/freqtrade \
    VIRTUAL_ENV=/opt/freqtrade/alpha_lab_service/.venv \
    PATH=/opt/freqtrade/alpha_lab_service/.venv/bin:$PATH
WORKDIR /opt/freqtrade

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libatlas3-base \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
COPY . /opt/freqtrade/
RUN uv sync --locked --project /opt/freqtrade/alpha_lab_service --no-dev --no-install-project \
    && mkdir -p /etc/alpha-lab \
    && cp /opt/freqtrade/deploy/alpha-lab-local/runtime.json /etc/alpha-lab/runtime.json \
    && chmod 0444 /etc/alpha-lab/runtime.json

USER 1000:1000
EXPOSE 8000
ENTRYPOINT ["python", "-m", "alpha_lab_service.server"]
CMD ["--host", "127.0.0.1", "--port", "8000"]
