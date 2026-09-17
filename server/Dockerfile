# Backend API (Flask)
# Build context: repo root  (docker build -f server/Dockerfile .)
FROM python:3.14-alpine3.24

# ansible-core comes from pip, not the distro package manager, so it runs on the image's
# own interpreter instead of dragging in a second, distro-owned Python.
ARG ANSIBLE_CORE_VERSION=2.21.3

WORKDIR /app

# Binaries the API shells out to:
#   git      -> services/git_source_manager.py, api/templates_routes.py
#   sshpass  -> api/ansible_run_routes.py, api/host_checks_routes.py (via shutil.which,
#               so a missing binary degrades silently rather than erroring)
#   ssh      -> ansible transport
RUN apk add --no-cache git openssh-client sshpass ca-certificates

COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
        "ansible-core==${ANSIBLE_CORE_VERSION}"

COPY server /app

# Ship the IaC tree (modules + env templates) so cloud_provisioning._sync_iac_assets
# can refresh per-project stack workspaces with the latest module code on every run.
COPY IaC /app/IaC

RUN mkdir -p /app/data

EXPOSE 5000

CMD ["python", "app.py"]
