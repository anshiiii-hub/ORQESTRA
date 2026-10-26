FROM python:3.12-slim

LABEL org.orqestra.sandbox="checkpoint-10" \
      org.orqestra.sandbox.version="0.1.0"

ENV PIP_NO_CACHE_DIR=1 \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

WORKDIR /workspace
COPY requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin sandbox

USER 10001:10001
CMD ["python", "-c", "import time; time.sleep(60)"]
