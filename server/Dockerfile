# Backend API (Flask)
# Build context: project root (docker build -f backend/Dockerfile .)
FROM python:3.14-slim

WORKDIR /app

# Split OS packages into smaller layers so Zot behind Cloudflare does not reject uploads over ~100 MB.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    sshpass \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
    ansible-core \
    && rm -rf /var/lib/apt/lists/*

COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY server /app

# Ship the IaC tree (modules + env templates) so cloud_provisioning._sync_iac_assets
# can refresh per-project stack workspaces with the latest module code on every run.
COPY IaC /app/IaC

RUN mkdir -p /app/data

EXPOSE 5000

CMD ["python", "app.py"]
