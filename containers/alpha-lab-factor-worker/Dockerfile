FROM python:3.11.10-slim-bookworm@sha256:840e180ebcc6e5c8efab209c43f5e40fd2af98cb49db5c7103c90539c56bb30e

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN groupadd --gid 65532 worker && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin worker \
    && mkdir -p /opt/factor-worker \
    && chown -R root:root /opt/factor-worker \
    && chmod 0555 /opt/factor-worker
COPY requirements.lock /tmp/requirements.lock
RUN PIP_NO_INDEX=0 python -m pip install --index-url=${PIP_INDEX_URL} --no-cache-dir --no-compile --require-hashes -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock
COPY worker.py /opt/factor-worker/worker.py
RUN chmod 0444 /opt/factor-worker/worker.py
USER 65532:65532
WORKDIR /opt/factor-worker
ENTRYPOINT ["python", "-I", "/opt/factor-worker/worker.py"]
