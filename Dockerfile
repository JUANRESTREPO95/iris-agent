# Iris Agent — imagen única para los servicios core, bridge y telegram.
FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NODE_ENV=production \
    HOME=/home/iris \
    PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip ffmpeg curl ca-certificates git openssl tini \
 && rm -rf /var/lib/apt/lists/*

# Claude Code (el motor que ejecuta Iris).
RUN npm install -g --no-audit --no-fund @anthropic-ai/claude-code

RUN useradd --create-home --home-dir /home/iris --uid 1001 --user-group --shell /bin/bash iris

WORKDIR /opt/iris

# Dependencias de Python.
COPY core/requirements.txt /opt/iris/core/requirements.txt
RUN python3 -m venv /opt/iris/core/venv \
 && /opt/iris/core/venv/bin/pip install --no-cache-dir --upgrade pip wheel \
 && /opt/iris/core/venv/bin/pip install --no-cache-dir -r /opt/iris/core/requirements.txt

# Dependencias de Node del puente de WhatsApp.
COPY bridge/package.json /opt/iris/bridge/package.json
RUN cd /opt/iris/bridge && npm install --omit=dev --no-audit --no-fund

COPY . /opt/iris

# Carpetas de datos y onboarding de Claude Code. Los volúmenes heredan el dueño.
RUN mkdir -p /home/iris/workspace /home/iris/.claude /var/lib/iris/media /var/lib/iris/logs \
 && printf '{"hasCompletedOnboarding": true}\n' > /home/iris/.claude.json \
 && chown -R iris:iris /home/iris /var/lib/iris \
 && chown -R root:root /opt/iris \
 && chmod -R a+rX /opt/iris

USER iris
WORKDIR /opt/iris/core

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/iris/core/venv/bin/python", "-m", "iris_core"]
