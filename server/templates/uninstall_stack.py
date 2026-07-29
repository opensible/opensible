"""Template: Uninstall / cleanup a previously-installed stack from target hosts.

Supports:
  - kubeadm    Kubernetes installed via k8s_cluster template (kubeadm)
  - k3s        k3s cluster (server + agent)
  - docker     Docker Engine + containerd
  - kafka      Apache Kafka KRaft (systemd) from kafka_cluster template
  - argocd     Argo CD installed via Helm on any cluster

Each stack has a hardened purge routine: stop services, run official
uninstallers when present, remove data directories, flush iptables/ipvs,
remove kubeconfigs, and free known ports. Safe to re-run.
"""
from __future__ import annotations

from typing import Any, Dict

from ._common import (
    render_hosts,
    VAULT_FILES_VARIABLE,
    parse_vault_files,
    vars_files_lines,
    yaml_str,
)


TEMPLATE = {
    "id": "uninstall-stack",
    "name": "Uninstall / Cleanup Stack",
    "category": "Maintenance",
    "icon": "trash-2",
    "description": (
        "Cleanly uninstall a previously-deployed stack (kubeadm, k3s, Docker, "
        "Kafka KRaft or Argo CD Helm release) from the selected hosts. Runs "
        "official uninstallers where available and hardens the cleanup by "
        "purging data dirs, kubeconfigs, systemd units and firewall/iptables "
        "state. Safe to re-run."
    ),
    "tags": ["cleanup", "uninstall", "maintenance", "purge"],
    "variables": [
        {
            "name": "stack",
            "label": "Stack to uninstall",
            "type": "select",
            "options": [
                {"label": "Kubernetes (kubeadm)", "value": "kubeadm"},
                {"label": "k3s Cluster", "value": "k3s"},
                {"label": "Docker Engine", "value": "docker"},
                {"label": "Apache Kafka (KRaft)", "value": "kafka"},
                {"label": "Redpanda + Console", "value": "redpanda"},
                {"label": "Redis + Sentinel", "value": "redis"},
                {"label": "Valkey + Sentinel", "value": "valkey"},
                {"label": "OpenBao (systemd)", "value": "openbao"},
                {"label": "PostgreSQL HA (Patroni + etcd)", "value": "patroni"},
                {"label": "RabbitMQ Cluster", "value": "rabbitmq"},
                {"label": "HAProxy Load Balancer", "value": "haproxy"},
                {"label": "Traefik Reverse Proxy / LB", "value": "traefik"},
                {"label": "Grafana OSS", "value": "grafana"},
                {"label": "Prometheus (systemd)", "value": "prometheus"},
                {"label": "VictoriaMetrics (systemd)", "value": "victoriametrics"},
                {"label": "Prometheus node_exporter", "value": "node-exporter"},
                {"label": "Argo CD (Helm release)", "value": "argocd"},
                {"label": "Elasticsearch (Docker cluster)", "value": "elasticsearch"},
                {"label": "Logstash (Docker cluster)", "value": "logstash"},
                {"label": "Kibana (Docker cluster)", "value": "kibana"},
                {"label": "HashiCorp Vault (Docker cluster)", "value": "vault-cluster"},



            ],
            "default": "kubeadm",
        },
        {
            "name": "purge_data",
            "label": "Purge data directories (irreversible)",
            "type": "boolean",
            "default": True,
            "help": "Removes /var/lib/etcd, /var/lib/kafka, /var/lib/docker, /var/lib/rancher, etc.",
        },
        {
            "name": "remove_packages",
            "label": "Uninstall OS packages",
            "type": "boolean",
            "default": True,
            "help": "apt-get purge / dnf remove kubelet, kubeadm, docker-ce, etc.",
        },
        {
            "name": "flush_iptables",
            "label": "Flush iptables / ipvs (Kubernetes only)",
            "type": "boolean",
            "default": True,
        },
        {
            "name": "reboot_after",
            "label": "Reboot host after cleanup",
            "type": "boolean",
            "default": False,
        },
        # Argo CD Helm-only options
        {
            "name": "argocd_namespace",
            "label": "Argo CD namespace (Argo CD only)",
            "type": "string",
            "default": "argocd",
        },
        {
            "name": "argocd_release",
            "label": "Helm release name (Argo CD only)",
            "type": "string",
            "default": "argocd",
        },
        {
            "name": "kubeconfig",
            "label": "kubeconfig path on target (Argo CD only)",
            "type": "string",
            "default": "/etc/rancher/k3s/k3s.yaml",
        },
        {
            "name": "delete_namespace",
            "label": "Delete Argo CD namespace after uninstall",
            "type": "boolean",
            "default": False,
        },
        {
            "name": "become",
            "label": "Run as sudo (become)",
            "type": "boolean",
            "default": True,
        },
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stack = str(values.get("stack") or "kubeadm").lower()
    return f"tmpl-uninstall-{stack}.yml"


# --------------------------------------------------------------------------- #
# Shell purge scripts (heredoc-safe: keep apostrophes out of comments/strings)
# --------------------------------------------------------------------------- #

_KUBEADM_SCRIPT = r"""set +e
echo "==> kubeadm reset"
kubeadm reset -f --cri-socket=unix:///run/containerd/containerd.sock 2>/dev/null || kubeadm reset -f 2>/dev/null || true
echo "==> stop services"
systemctl stop kubelet 2>/dev/null || true
systemctl disable kubelet 2>/dev/null || true
systemctl stop containerd 2>/dev/null || true
echo "==> remove kube dirs"
rm -rf /etc/kubernetes /var/lib/kubelet /var/lib/etcd /etc/cni/net.d /opt/cni/bin /var/lib/cni /run/flannel /var/lib/calico /var/lib/weave 2>/dev/null || true
rm -rf /root/.kube /home/*/.kube 2>/dev/null || true
rm -f /etc/kubernetes/admin.conf 2>/dev/null || true
echo "==> remove CNI interfaces"
for i in cni0 flannel.1 kube-ipvs0 tunl0 vxlan.calico weave datapath docker0; do
  ip link show $i >/dev/null 2>&1 && ip link delete $i 2>/dev/null || true
done
if [ "{{ flush_iptables | ternary('yes','no') }}" = "yes" ]; then
  echo "==> flush iptables + ipvs"
  iptables -F 2>/dev/null || true
  iptables -t nat -F 2>/dev/null || true
  iptables -t mangle -F 2>/dev/null || true
  iptables -X 2>/dev/null || true
  ipvsadm --clear 2>/dev/null || true
fi
if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge packages"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y kubeadm kubectl kubelet kubernetes-cni cri-tools 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
    rm -f /etc/apt/sources.list.d/kubernetes.list /etc/apt/keyrings/kubernetes-apt-keyring.gpg 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y kubeadm kubectl kubelet cri-tools 2>/dev/null || true
    rm -f /etc/yum.repos.d/kubernetes.repo 2>/dev/null || true
  fi
fi
echo "==> always remove kubectl/helm binaries and stale kubeconfigs"
rm -f /usr/bin/kubectl /usr/local/bin/kubectl /snap/bin/kubectl /usr/local/bin/helm /usr/bin/helm 2>/dev/null || true
snap remove kubectl 2>/dev/null || true
rm -rf /root/.kube /root/.helm /root/.config/helm /home/*/.kube /home/*/.helm /home/*/.config/helm 2>/dev/null || true
hash -r 2>/dev/null || true
echo "==> kubeadm cleanup complete"
exit 0
"""

_K3S_SCRIPT = r"""set +e
echo "==> run official k3s uninstallers"
[ -x /usr/local/bin/k3s-uninstall.sh ] && /usr/local/bin/k3s-uninstall.sh || true
[ -x /usr/local/bin/k3s-agent-uninstall.sh ] && /usr/local/bin/k3s-agent-uninstall.sh || true
[ -x /usr/local/bin/k3s-killall.sh ] && /usr/local/bin/k3s-killall.sh || true
echo "==> stop residual services"
systemctl stop k3s 2>/dev/null || true
systemctl stop k3s-agent 2>/dev/null || true
systemctl disable k3s 2>/dev/null || true
systemctl disable k3s-agent 2>/dev/null || true
echo "==> remove k3s dirs and kubeconfigs"
rm -rf /var/lib/rancher /etc/rancher /var/lib/kubelet /var/lib/cni /etc/cni/net.d /opt/cni/bin 2>/dev/null || true
rm -rf /var/lib/longhorn /var/openebs 2>/dev/null || true
rm -rf /root/.kube /home/*/.kube 2>/dev/null || true
rm -f /usr/local/bin/k3s /usr/local/bin/kubectl /usr/local/bin/crictl /usr/local/bin/ctr 2>/dev/null || true
rm -f /etc/systemd/system/k3s.service /etc/systemd/system/k3s-agent.service 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true
echo "==> remove CNI interfaces"
for i in cni0 flannel.1 kube-ipvs0 tunl0 vxlan.calico; do
  ip link show $i >/dev/null 2>&1 && ip link delete $i 2>/dev/null || true
done
if [ "{{ flush_iptables | ternary('yes','no') }}" = "yes" ]; then
  echo "==> flush iptables + ipvs"
  iptables -F 2>/dev/null || true
  iptables -t nat -F 2>/dev/null || true
  iptables -t mangle -F 2>/dev/null || true
  iptables -X 2>/dev/null || true
  ipvsadm --clear 2>/dev/null || true
fi
echo "==> always remove kubectl/helm binaries and stale kubeconfigs"
rm -f /usr/bin/kubectl /usr/local/bin/kubectl /snap/bin/kubectl /usr/local/bin/helm /usr/bin/helm 2>/dev/null || true
snap remove kubectl 2>/dev/null || true
rm -rf /root/.kube /root/.helm /root/.config/helm /home/*/.kube /home/*/.helm /home/*/.config/helm 2>/dev/null || true
hash -r 2>/dev/null || true
echo "==> k3s cleanup complete"
exit 0
"""

_DOCKER_SCRIPT = r"""set +e
echo "==> stop docker + containerd"
systemctl stop docker docker.socket containerd 2>/dev/null || true
systemctl disable docker docker.socket containerd 2>/dev/null || true
echo "==> stop residual containers"
docker ps -aq 2>/dev/null | xargs -r docker rm -f 2>/dev/null || true
if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge docker packages"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker.io 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
    rm -f /etc/apt/sources.list.d/docker.list /etc/apt/keyrings/docker.gpg /etc/apt/keyrings/docker.asc 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null || true
    rm -f /etc/yum.repos.d/docker-ce.repo 2>/dev/null || true
  fi
fi
if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> remove docker data dirs"
  rm -rf /var/lib/docker /var/lib/containerd /etc/docker /run/docker /run/containerd 2>/dev/null || true
fi
groupdel docker 2>/dev/null || true
echo "==> docker cleanup complete"
exit 0
"""

_KAFKA_SCRIPT = r"""set +e
echo "==> stop kafka + kafka-connect + zookeeper + health services"
for svc in kafka kafka-server kafka-connect kafka-schema-registry zookeeper confluent-kafka opensible-kafka-health; do
  systemctl stop  "$svc" 2>/dev/null || true
  systemctl disable "$svc" 2>/dev/null || true
  rm -f "/etc/systemd/system/${svc}.service" 2>/dev/null || true
  rm -rf "/etc/systemd/system/${svc}.service.d" 2>/dev/null || true
done
systemctl daemon-reload 2>/dev/null || true

echo "==> kill leftover kafka/java/health processes"
# Exclude this shell (and its parents) so pkill -f does not match the cleanup
# script itself (whose argv contains these very patterns).
_self_pids="$$ $PPID"
_kill_pat() {
  pat="$1"
  for pid in $(pgrep -f "$pat" 2>/dev/null); do
    skip=0
    for s in $_self_pids; do [ "$pid" = "$s" ] && skip=1; done
    [ "$skip" = "1" ] || kill -9 "$pid" 2>/dev/null || true
  done
}
_kill_pat 'kafka\.Kafka'
_kill_pat 'kafka-server-start'
_kill_pat 'kafka-storage'
_kill_pat 'org\.apache\.zookeeper'
_kill_pat 'opensible-kafka-health'

echo "==> remove health dashboard script"
rm -f /usr/local/bin/opensible-kafka-health.py 2>/dev/null || true

echo "==> free kafka + health ports (9092/9093/9094/8080)"
for p in 9092 9093 9094 8080; do
  fuser -k -TERM "${p}/tcp" 2>/dev/null || true
done

if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge kafka packages + repos"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y kafka confluent-kafka 'confluent-*' 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
    rm -f /etc/apt/sources.list.d/*kafka* /etc/apt/sources.list.d/confluent*.list 2>/dev/null || true
    rm -f /etc/apt/keyrings/*kafka* /usr/share/keyrings/*kafka* /usr/share/keyrings/confluent* 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y kafka confluent-kafka 'confluent-*' 2>/dev/null || true
    rm -f /etc/yum.repos.d/*kafka*.repo /etc/yum.repos.d/confluent*.repo 2>/dev/null || true
  fi
fi

echo "==> remove kafka install dirs (tarball)"
rm -rf /opt/kafka /opt/kafka_* /usr/local/kafka /usr/local/kafka_* 2>/dev/null || true
rm -f  /usr/local/bin/kafka-* /usr/bin/kafka-* 2>/dev/null || true
rm -f  /etc/profile.d/kafka.sh 2>/dev/null || true

if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge kafka data, logs and config"
  rm -rf /var/lib/kafka /var/lib/zookeeper /var/log/kafka /var/log/zookeeper /etc/kafka /etc/zookeeper 2>/dev/null || true
  rm -rf /tmp/kafka-logs /tmp/zookeeper /tmp/kraft-combined-logs 2>/dev/null || true
  rm -rf /home/kafka 2>/dev/null || true
fi

echo "==> remove kafka system user + group"
pkill -9 -u kafka 2>/dev/null || true
userdel  -r kafka 2>/dev/null || userdel kafka 2>/dev/null || true
groupdel kafka 2>/dev/null || true

echo "==> kafka cleanup complete"
exit 0
"""

_ARGOCD_SCRIPT = r"""set +e
export KUBECONFIG={{ kubeconfig }}
NS={{ argocd_namespace }}
REL={{ argocd_release }}
echo "==> helm uninstall $REL in namespace $NS"
if command -v helm >/dev/null 2>&1; then
  helm uninstall "$REL" -n "$NS" --wait 2>/dev/null || true
else
  echo "helm not found on target; skipping helm uninstall"
fi
echo "==> remove Argo CD CRDs"
kubectl get crd -o name 2>/dev/null | grep -E 'argoproj\.io$' | xargs -r kubectl delete --ignore-not-found 2>/dev/null || true
if [ "{{ delete_namespace | ternary('yes','no') }}" = "yes" ]; then
  echo "==> delete namespace $NS"
  kubectl delete namespace "$NS" --ignore-not-found --wait=false 2>/dev/null || true
fi
echo "==> argocd cleanup complete"
exit 0
"""

_REDIS_SCRIPT = r"""set +e
FLAVOR={{ redis_flavor | default('redis') }}
if [ "$FLAVOR" = "valkey" ]; then
  SVC=valkey-server
  SENTINEL=valkey-sentinel
  USR=valkey
  BIN_GLOB="/usr/bin/valkey* /usr/local/bin/valkey*"
  ETC=/etc/valkey
  LIB=/var/lib/valkey
  LOG=/var/log/valkey
  RUN=/var/run/valkey
  PKGS="valkey valkey-server valkey-sentinel valkey-tools"
else
  SVC=redis-server
  SENTINEL=redis-sentinel
  USR=redis
  BIN_GLOB="/usr/bin/redis* /usr/local/bin/redis*"
  ETC=/etc/redis
  LIB=/var/lib/redis
  LOG=/var/log/redis
  RUN=/var/run/redis
  PKGS="redis redis-server redis-sentinel redis-tools"
fi
echo "==> stop opensible-redis-health"
systemctl stop opensible-redis-health.service 2>/dev/null || true
systemctl disable opensible-redis-health.service 2>/dev/null || true
rm -f /etc/systemd/system/opensible-redis-health.service /usr/local/bin/opensible-redis-health.py 2>/dev/null || true
_self_pids_h="$$ $PPID"
for pid in $(pgrep -f 'opensible-redis-health' 2>/dev/null); do
  skip=0; for s in $_self_pids_h; do [ "$pid" = "$s" ] && skip=1; done
  [ "$skip" = "1" ] || kill -9 "$pid" 2>/dev/null || true
done
for p in 8080; do fuser -k -TERM "${p}/tcp" 2>/dev/null || true; done
echo "==> stop $SVC and $SENTINEL"
systemctl stop "$SENTINEL" 2>/dev/null || true
systemctl stop "$SVC" 2>/dev/null || true
systemctl disable "$SENTINEL" 2>/dev/null || true
systemctl disable "$SVC" 2>/dev/null || true
rm -rf /etc/systemd/system/${SVC}.service.d /etc/systemd/system/${SENTINEL}.service.d 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true
if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge packages"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y $PKGS 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y $PKGS 2>/dev/null || true
  fi
fi
echo "==> remove binaries left behind"
rm -f $BIN_GLOB 2>/dev/null || true
if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge data + config"
  rm -rf "$ETC" "$LIB" "$LOG" "$RUN" 2>/dev/null || true
fi
userdel "$USR" 2>/dev/null || true
groupdel "$USR" 2>/dev/null || true
echo "==> $FLAVOR cleanup complete"
exit 0
"""


_REDPANDA_SCRIPT = r"""set +e
echo "==> stop redpanda-console + redpanda"
systemctl stop redpanda-console 2>/dev/null || true
systemctl stop redpanda 2>/dev/null || true
systemctl disable redpanda-console 2>/dev/null || true
systemctl disable redpanda 2>/dev/null || true
rm -f /etc/systemd/system/redpanda-console.service 2>/dev/null || true
rm -rf /etc/systemd/system/redpanda.service.d 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true
if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge packages + repo"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y redpanda redpanda-console 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
    rm -f /etc/apt/sources.list.d/redpanda*.list /etc/apt/sources.list.d/*redpanda* 2>/dev/null || true
    rm -f /usr/share/keyrings/redpanda*.gpg /etc/apt/trusted.gpg.d/redpanda*.gpg 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y redpanda redpanda-console 2>/dev/null || true
    rm -f /etc/yum.repos.d/redpanda*.repo 2>/dev/null || true
  fi
fi
if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge redpanda data + config"
  rm -rf /etc/redpanda /var/lib/redpanda /var/log/redpanda 2>/dev/null || true
fi
userdel redpanda 2>/dev/null || true
groupdel redpanda 2>/dev/null || true
echo "==> redpanda cleanup complete"
exit 0
"""


_OPENBAO_SCRIPT = r"""set +e
echo "==> stop openbao services"
systemctl stop openbao-autounseal.timer 2>/dev/null || true
systemctl disable openbao-autounseal.timer 2>/dev/null || true
systemctl stop openbao-autounseal.service 2>/dev/null || true
systemctl disable openbao-autounseal.service 2>/dev/null || true
systemctl stop openbao 2>/dev/null || true
systemctl disable openbao 2>/dev/null || true
rm -f /etc/systemd/system/openbao.service 2>/dev/null || true
rm -f /etc/systemd/system/openbao-autounseal.service 2>/dev/null || true
rm -f /etc/systemd/system/openbao-autounseal.timer 2>/dev/null || true
rm -f /usr/local/sbin/openbao-autounseal.sh 2>/dev/null || true
rm -rf /etc/systemd/system/openbao.service.d 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true

echo "==> kill leftover bao processes"
# Exclude this shell (and its parents) so pkill -f does not match the cleanup
# script itself (whose argv contains these very patterns).
_self_pids="$$ $PPID"
_kill_pat() {
  pat="$1"
  for pid in $(pgrep -f "$pat" 2>/dev/null); do
    skip=0
    for s in $_self_pids; do [ "$pid" = "$s" ] && skip=1; done
    [ "$skip" = "1" ] || kill -9 "$pid" 2>/dev/null || true
  done
}
_kill_pat '/usr/bin/bao server'
_kill_pat '/usr/bin/bao'
pkill -9 -x bao 2>/dev/null || true


if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge openbao packages + repos"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y openbao 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
    rm -f /etc/apt/sources.list.d/openbao*.list 2>/dev/null || true
    rm -f /etc/apt/keyrings/openbao.asc /usr/share/keyrings/openbao* 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y openbao 2>/dev/null || true
    rm -f /etc/yum.repos.d/openbao*.repo 2>/dev/null || true
  fi
fi

echo "==> remove leftover binaries"
rm -f /usr/bin/bao /usr/local/bin/bao 2>/dev/null || true

if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge openbao data + config"
  rm -rf /etc/openbao /opt/openbao /var/lib/openbao /var/log/openbao 2>/dev/null || true
fi

echo "==> remove openbao user + group"
userdel openbao  2>/dev/null || true
groupdel openbao 2>/dev/null || true

echo "==> openbao cleanup complete"
exit 0
"""


_PATRONI_SCRIPT = r"""set +e
echo "==> stop opensible-patroni-health"
systemctl stop opensible-patroni-health.service 2>/dev/null || true
systemctl disable opensible-patroni-health.service 2>/dev/null || true
rm -f /etc/systemd/system/opensible-patroni-health.service /usr/local/bin/opensible-patroni-health.py 2>/dev/null || true
_self_pids_h="$$ $PPID"
for pid in $(pgrep -f 'opensible-patroni-health' 2>/dev/null); do
  skip=0; for s in $_self_pids_h; do [ "$pid" = "$s" ] && skip=1; done
  [ "$skip" = "1" ] || kill -9 "$pid" 2>/dev/null || true
done
for p in 8080; do fuser -k -TERM "${p}/tcp" 2>/dev/null || true; done

echo "==> stop patroni + etcd"
systemctl stop patroni 2>/dev/null || true
systemctl stop etcd 2>/dev/null || true
systemctl disable patroni 2>/dev/null || true
systemctl disable etcd 2>/dev/null || true
rm -f /etc/systemd/system/patroni.service /etc/systemd/system/etcd.service 2>/dev/null || true
rm -rf /etc/systemd/system/patroni.service.d /etc/systemd/system/etcd.service.d 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true

echo "==> kill leftover postgres/patroni/etcd processes"
_self_pids="$$ $PPID"
_kill_pat() {
  pat="$1"
  for pid in $(pgrep -f "$pat" 2>/dev/null); do
    skip=0
    for s in $_self_pids; do [ "$pid" = "$s" ] && skip=1; done
    [ "$skip" = "1" ] || kill -9 "$pid" 2>/dev/null || true
  done
}
_kill_pat '/opt/patroni/bin/patroni'
_kill_pat 'bin/postgres'
_kill_pat '/usr/local/bin/etcd'

echo "==> free postgres/patroni/etcd ports"
for p in 5432 8008 2379 2380; do
  fuser -k -TERM "${p}/tcp" 2>/dev/null || true
done

if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge PostgreSQL packages + PGDG repos"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y 'postgresql-*' 'postgresql-client-*' 'postgresql-contrib*' 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
    rm -f /etc/apt/sources.list.d/pgdg.list /etc/apt/keyrings/pgdg.asc 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y 'postgresql*' 'pgdg-*' 2>/dev/null || true
    rm -f /etc/yum.repos.d/pgdg*.repo 2>/dev/null || true
  fi
fi

echo "==> remove Patroni venv + binaries + etcd binaries"
rm -rf /opt/patroni 2>/dev/null || true
rm -f /usr/local/bin/patroni /usr/local/bin/patronictl /usr/local/bin/etcd /usr/local/bin/etcdctl 2>/dev/null || true

if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge patroni + etcd data + config + logs"
  rm -rf /etc/patroni /var/lib/patroni /var/log/patroni 2>/dev/null || true
  rm -rf /var/lib/etcd 2>/dev/null || true
  rm -rf /var/lib/postgresql /etc/postgresql /var/log/postgresql 2>/dev/null || true
fi

echo "==> patroni cleanup complete"
exit 0
"""


_RABBITMQ_SCRIPT = r"""set +e
echo "==> stop rabbitmq + epmd"
systemctl stop rabbitmq-server 2>/dev/null || true
systemctl disable rabbitmq-server 2>/dev/null || true
systemctl stop epmd.socket 2>/dev/null || true
systemctl stop epmd.service 2>/dev/null || true
rm -rf /etc/systemd/system/rabbitmq-server.service.d 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true

echo "==> kill leftover beam/epmd processes"
_self_pids="$$ $PPID"
_kill_pat() {
  pat="$1"
  for pid in $(pgrep -f "$pat" 2>/dev/null); do
    skip=0
    for s in $_self_pids; do [ "$pid" = "$s" ] && skip=1; done
    [ "$skip" = "1" ] || kill -9 "$pid" 2>/dev/null || true
  done
}
_kill_pat 'beam.smp'
_kill_pat 'rabbitmq_prelaunch'
pkill -9 -x epmd 2>/dev/null || true

echo "==> free rabbitmq ports"
for p in 5672 15672 15692 25672 4369; do
  fuser -k -TERM "${p}/tcp" 2>/dev/null || true
done

if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge rabbitmq packages"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y rabbitmq-server 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y rabbitmq-server 2>/dev/null || true
  fi
fi

if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge rabbitmq data + config + logs"
  rm -rf /etc/rabbitmq /var/lib/rabbitmq /var/log/rabbitmq 2>/dev/null || true
  rm -f /root/.erlang.cookie 2>/dev/null || true
fi

userdel rabbitmq 2>/dev/null || true
groupdel rabbitmq 2>/dev/null || true

echo "==> rabbitmq cleanup complete"
exit 0
"""


_HAPROXY_SCRIPT = r"""set +e
echo "==> stop haproxy + keepalived"
systemctl stop haproxy 2>/dev/null || true
systemctl disable haproxy 2>/dev/null || true
systemctl stop keepalived 2>/dev/null || true
systemctl disable keepalived 2>/dev/null || true

echo "==> kill leftover haproxy processes"
_self_pids="$$ $PPID"
for pid in $(pgrep -x haproxy 2>/dev/null); do
  skip=0
  for s in $_self_pids; do [ "$pid" = "$s" ] && skip=1; done
  [ "$skip" = "1" ] || kill -9 "$pid" 2>/dev/null || true
done

if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge haproxy + keepalived packages"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y haproxy keepalived 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y haproxy keepalived 2>/dev/null || true
  fi
fi

if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge haproxy + keepalived config + runtime"
  rm -rf /etc/haproxy /var/lib/haproxy /run/haproxy /var/log/haproxy* 2>/dev/null || true
  rm -rf /etc/keepalived 2>/dev/null || true
fi

echo "==> haproxy cleanup complete"
exit 0
"""


_TRAEFIK_SCRIPT = r"""set +e
echo "==> stop traefik + keepalived"
systemctl stop traefik 2>/dev/null || true
systemctl disable traefik 2>/dev/null || true
systemctl stop keepalived 2>/dev/null || true
systemctl disable keepalived 2>/dev/null || true
rm -f /etc/systemd/system/traefik.service 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true

echo "==> kill leftover traefik processes"
_self_pids="$$ $PPID"
for pid in $(pgrep -x traefik 2>/dev/null); do
  skip=0
  for s in $_self_pids; do [ "$pid" = "$s" ] && skip=1; done
  [ "$skip" = "1" ] || kill -9 "$pid" 2>/dev/null || true
done

if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> remove traefik binary + keepalived package"
  rm -f /usr/local/bin/traefik 2>/dev/null || true
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y keepalived 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y keepalived 2>/dev/null || true
  fi
fi

if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge traefik config + data + logs"
  rm -rf /etc/traefik /var/lib/traefik /var/log/traefik 2>/dev/null || true
  rm -rf /etc/keepalived 2>/dev/null || true
fi

userdel traefik 2>/dev/null || true
groupdel traefik 2>/dev/null || true

echo "==> traefik cleanup complete"
exit 0
"""


_GRAFANA_SCRIPT = r"""set +e
echo "==> stop grafana-server"
systemctl stop grafana-server 2>/dev/null || true
systemctl disable grafana-server 2>/dev/null || true
rm -rf /etc/systemd/system/grafana-server.service.d 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true
if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge grafana packages + repos"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y grafana grafana-enterprise 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
    rm -f /etc/apt/sources.list.d/grafana.list /etc/apt/keyrings/grafana.gpg 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y grafana grafana-enterprise 2>/dev/null || true
    rm -f /etc/yum.repos.d/grafana.repo 2>/dev/null || true
  fi
fi
if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge grafana data + config + logs"
  rm -rf /etc/grafana /var/lib/grafana /var/log/grafana /var/run/grafana 2>/dev/null || true
fi
userdel grafana 2>/dev/null || true
groupdel grafana 2>/dev/null || true
echo "==> grafana cleanup complete"
exit 0
"""


_METRICS_SCRIPT = r"""set +e
FLAVOR={{ metrics_flavor | default('victoriametrics') }}
echo "==> stop $FLAVOR"
systemctl stop  "$FLAVOR" 2>/dev/null || true
systemctl disable "$FLAVOR" 2>/dev/null || true
rm -f "/etc/systemd/system/${FLAVOR}.service" 2>/dev/null || true
rm -rf "/etc/systemd/system/${FLAVOR}.service.d" 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true
_self_pids="$$ $PPID"
_kill_pat() {
  pat="$1"
  for pid in $(pgrep -f "$pat" 2>/dev/null); do
    skip=0
    for s in $_self_pids; do [ "$pid" = "$s" ] && skip=1; done
    [ "$skip" = "1" ] || kill -9 "$pid" 2>/dev/null || true
  done
}
if [ "$FLAVOR" = "prometheus" ]; then
  _kill_pat '/usr/local/bin/prometheus'
  rm -f /usr/local/bin/prometheus /usr/local/bin/promtool 2>/dev/null || true
  if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
    rm -rf /etc/prometheus /srv/metrics /var/lib/prometheus 2>/dev/null || true
  fi
  userdel prometheus 2>/dev/null || true
  groupdel prometheus 2>/dev/null || true
else
  _kill_pat '/usr/local/bin/victoria-metrics-prod'
  rm -f /usr/local/bin/victoria-metrics-prod 2>/dev/null || true
  if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
    rm -rf /srv/metrics /srv/observability/vm /var/lib/victoriametrics 2>/dev/null || true
  fi
  userdel victoriametrics 2>/dev/null || true
  groupdel victoriametrics 2>/dev/null || true
fi
echo "==> $FLAVOR cleanup complete"
exit 0
"""


_NODE_EXPORTER_SCRIPT = r"""set +e
echo "==> stop node_exporter"
for u in prometheus-node-exporter node_exporter; do
  systemctl stop  "$u" 2>/dev/null || true
  systemctl disable "$u" 2>/dev/null || true
  rm -rf "/etc/systemd/system/${u}.service.d" 2>/dev/null || true
done
systemctl daemon-reload 2>/dev/null || true
if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge node_exporter packages"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y prometheus-node-exporter 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf remove -y golang-github-prometheus-node-exporter node_exporter 2>/dev/null || true
  fi
fi
if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  rm -rf /var/lib/node_exporter 2>/dev/null || true
fi
echo "==> node_exporter cleanup complete"
exit 0
"""



_ELASTIC_DOCKER_SCRIPT = r"""set +e
STACK="{{ elastic_stack | default('elasticsearch') }}"
case "$STACK" in
  elasticsearch)
    SVC=elasticsearch-docker
    HEALTH=opensible-elasticsearch-health
    NAME_PREFIX=elasticsearch-
    IMAGE_MATCH='docker.elastic.co/elasticsearch/elasticsearch'
    DATA_DIRS="/var/lib/elasticsearch-data /var/lib/elasticsearch /var/log/elasticsearch /etc/elasticsearch"
    PORTS="9200 9300"
    ;;
  logstash)
    SVC=logstash-docker
    HEALTH=opensible-logstash-health
    NAME_PREFIX=logstash-
    IMAGE_MATCH='docker.elastic.co/logstash/logstash'
    DATA_DIRS="/etc/logstash /var/lib/logstash /var/log/logstash"
    PORTS="5044 9600"
    ;;
  kibana)
    SVC=kibana-docker
    HEALTH=opensible-kibana-health
    NAME_PREFIX=kibana-
    IMAGE_MATCH='docker.elastic.co/kibana/kibana'
    DATA_DIRS="/var/lib/kibana /etc/kibana /var/log/kibana"
    PORTS="5601"
    ;;
esac

echo "==> stop $SVC + $HEALTH"
systemctl stop  "$HEALTH.service" 2>/dev/null || true
systemctl disable "$HEALTH.service" 2>/dev/null || true
systemctl stop  "${SVC}.service" 2>/dev/null || true
systemctl disable "${SVC}.service" 2>/dev/null || true
rm -f "/etc/systemd/system/${SVC}.service" "/etc/systemd/system/${HEALTH}.service" 2>/dev/null || true
rm -rf "/etc/systemd/system/${SVC}.service.d" 2>/dev/null || true
rm -f "/usr/local/bin/${HEALTH}.py" 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true

if command -v docker >/dev/null 2>&1; then
  echo "==> remove containers matching name prefix $NAME_PREFIX"
  for c in $(docker ps -aq --filter "name=^${NAME_PREFIX}" 2>/dev/null); do
    docker rm -f "$c" 2>/dev/null || true
  done
  # Fallback: match by image
  for c in $(docker ps -aq 2>/dev/null); do
    img=$(docker inspect --format '{{"{{"}}.Config.Image{{"}}"}}' "$c" 2>/dev/null)
    case "$img" in
      *"$IMAGE_MATCH"*) docker rm -f "$c" 2>/dev/null || true ;;
    esac
  done
  if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
    echo "==> remove $STACK images"
    for img in $(docker images --format '{{"{{"}}.Repository{{"}}"}}:{{"{{"}}.Tag{{"}}"}}' 2>/dev/null | grep -F "$IMAGE_MATCH"); do
      docker rmi -f "$img" 2>/dev/null || true
    done
  fi
  echo "==> repair docker bridge if stale after cleanup"
  if command -v ip >/dev/null 2>&1; then
    _needs_restart=0
    docker network inspect bridge >/dev/null 2>&1 || _needs_restart=1
    ip link show docker0 >/dev/null 2>&1 || _needs_restart=1
    if [ "$_needs_restart" = "1" ]; then
      systemctl stop docker docker.socket 2>/dev/null || true
      ip link delete docker0 2>/dev/null || true
      systemctl restart containerd 2>/dev/null || true
      systemctl start docker 2>/dev/null || systemctl restart docker 2>/dev/null || true
    fi
  fi
fi

echo "==> free $STACK ports ($PORTS 8080)"
for p in $PORTS 8080; do
  fuser -k -TERM "${p}/tcp" 2>/dev/null || true
done

if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge $STACK data + config dirs"
  for d in $DATA_DIRS; do
    rm -rf "$d" 2>/dev/null || true
  done
fi

echo "==> $STACK cleanup complete"
exit 0
"""


_VAULT_DOCKER_SCRIPT = r"""set +e
echo "==> stop vault-docker + auto-unseal + health units"
systemctl stop  opensible-vault-health.service 2>/dev/null || true
systemctl disable opensible-vault-health.service 2>/dev/null || true
systemctl stop  vault-autounseal.timer 2>/dev/null || true
systemctl disable vault-autounseal.timer 2>/dev/null || true
systemctl stop  vault-autounseal.service 2>/dev/null || true
systemctl disable vault-autounseal.service 2>/dev/null || true
systemctl stop  vault-docker.service 2>/dev/null || true
systemctl disable vault-docker.service 2>/dev/null || true
rm -f /etc/systemd/system/vault-docker.service 2>/dev/null || true
rm -f /etc/systemd/system/opensible-vault-health.service 2>/dev/null || true
rm -f /etc/systemd/system/vault-autounseal.service 2>/dev/null || true
rm -f /etc/systemd/system/vault-autounseal.timer 2>/dev/null || true
rm -rf /etc/systemd/system/vault-docker.service.d 2>/dev/null || true
rm -f /usr/local/sbin/vault-autounseal.sh 2>/dev/null || true
rm -f /usr/local/bin/opensible-vault-health.py 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true

if command -v docker >/dev/null 2>&1; then
  echo "==> remove Vault containers matching name prefix vault-"
  for c in $(docker ps -aq --filter 'name=^/vault-' 2>/dev/null); do
    docker rm -f "$c" 2>/dev/null || true
  done
  for c in $(docker ps -aq 2>/dev/null); do
    img=$(docker inspect --format '{{"{{"}}.Config.Image{{"}}"}}' "$c" 2>/dev/null)
    case "$img" in
      *hashicorp/vault*) docker rm -f "$c" 2>/dev/null || true ;;
    esac
  done
  if [ "{{ remove_packages | ternary('yes','no') }}" = "yes" ]; then
    echo "==> remove hashicorp/vault images"
    for img in $(docker images --format '{{"{{"}}.Repository{{"}}"}}:{{"{{"}}.Tag{{"}}"}}' 2>/dev/null | grep -F 'hashicorp/vault'); do
      docker rmi -f "$img" 2>/dev/null || true
    done
  fi
  echo "==> repair docker bridge if stale after cleanup"
  if command -v ip >/dev/null 2>&1; then
    _needs_restart=0
    docker network inspect bridge >/dev/null 2>&1 || _needs_restart=1
    ip link show docker0 >/dev/null 2>&1 || _needs_restart=1
    if [ "$_needs_restart" = "1" ]; then
      systemctl stop docker docker.socket 2>/dev/null || true
      ip link delete docker0 2>/dev/null || true
      systemctl restart containerd 2>/dev/null || true
      systemctl start docker 2>/dev/null || systemctl restart docker 2>/dev/null || true
    fi
  fi
fi

echo "==> free Vault ports (8200 8201 8280)"
for p in 8200 8201 8280; do
  fuser -k -TERM "${p}/tcp" 2>/dev/null || true
done

if [ "{{ purge_data | ternary('yes','no') }}" = "yes" ]; then
  echo "==> purge Vault data + config dirs"
  rm -rf /etc/vault /opt/vault /var/lib/vault-data /var/log/vault 2>/dev/null || true
fi

echo "==> vault-cluster cleanup complete"
exit 0
"""


_SCRIPTS = {
    "kubeadm": _KUBEADM_SCRIPT,
    "k3s": _K3S_SCRIPT,
    "docker": _DOCKER_SCRIPT,
    "kafka": _KAFKA_SCRIPT,
    "redpanda": _REDPANDA_SCRIPT,
    "redis": _REDIS_SCRIPT,
    "valkey": _REDIS_SCRIPT,
    "openbao": _OPENBAO_SCRIPT,
    "patroni": _PATRONI_SCRIPT,
    "rabbitmq": _RABBITMQ_SCRIPT,
    "haproxy": _HAPROXY_SCRIPT,
    "traefik": _TRAEFIK_SCRIPT,
    "grafana": _GRAFANA_SCRIPT,
    "prometheus": _METRICS_SCRIPT,
    "victoriametrics": _METRICS_SCRIPT,
    "node-exporter": _NODE_EXPORTER_SCRIPT,
    "argocd": _ARGOCD_SCRIPT,
    "elasticsearch": _ELASTIC_DOCKER_SCRIPT,
    "logstash": _ELASTIC_DOCKER_SCRIPT,
    "kibana": _ELASTIC_DOCKER_SCRIPT,
    "vault-cluster": _VAULT_DOCKER_SCRIPT,
}

_STACK_LABELS = {
    "kubeadm": "Kubernetes (kubeadm)",
    "k3s": "k3s Cluster",
    "docker": "Docker Engine",
    "kafka": "Apache Kafka (KRaft)",
    "redpanda": "Redpanda + Console",
    "redis": "Redis + Sentinel",
    "valkey": "Valkey + Sentinel",
    "openbao": "OpenBao (systemd)",
    "patroni": "PostgreSQL HA (Patroni + etcd)",
    "rabbitmq": "RabbitMQ Cluster",
    "haproxy": "HAProxy Load Balancer",
    "traefik": "Traefik Reverse Proxy / LB",
    "grafana": "Grafana OSS",
    "prometheus": "Prometheus (systemd)",
    "victoriametrics": "VictoriaMetrics (systemd)",
    "node-exporter": "Prometheus node_exporter",
    "argocd": "Argo CD (Helm)",
    "elasticsearch": "Elasticsearch (Docker cluster)",
    "logstash": "Logstash (Docker cluster)",
    "kibana": "Kibana (Docker cluster)",
    "vault-cluster": "HashiCorp Vault (Docker cluster)",
}




def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + ln for ln in text.splitlines())


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    stack = str(values.get("stack") or "kubeadm").lower()
    if stack not in _SCRIPTS:
        stack = "kubeadm"
    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"
    label = _STACK_LABELS[stack]
    script = _SCRIPTS[stack]

    parts = [
        "---",
        f"# Rendered from template: {TEMPLATE['name']}",
        f"# Stack: {label}",
        f"- name: Uninstall {label}",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    purge_data: {'true' if values.get('purge_data', True) else 'false'}",
        f"    remove_packages: {'true' if values.get('remove_packages', True) else 'false'}",
        f"    flush_iptables: {'true' if values.get('flush_iptables', True) else 'false'}",
        f"    delete_namespace: {'true' if values.get('delete_namespace', False) else 'false'}",
        f"    argocd_namespace: {yaml_str(values.get('argocd_namespace') or 'argocd')}",
        f"    argocd_release: {yaml_str(values.get('argocd_release') or 'argocd')}",
        f"    kubeconfig: {yaml_str(values.get('kubeconfig') or '/etc/rancher/k3s/k3s.yaml')}",
        f"    redis_flavor: {yaml_str('valkey' if stack == 'valkey' else 'redis')}",
        f"    metrics_flavor: {yaml_str('prometheus' if stack == 'prometheus' else 'victoriametrics')}",
        f"    elastic_stack: {yaml_str(stack if stack in ('elasticsearch','logstash','kibana') else 'elasticsearch')}",


        "  tasks:",
        f"    - name: Run cleanup script for {label}",
        "      ansible.builtin.shell: |",
        _indent(script.rstrip(), "        "),
        "      args:",
        "        executable: /bin/bash",
        "      register: cleanup_out",
        "      changed_when: true",
        "      failed_when: cleanup_out.rc != 0",
        "    - name: Show cleanup output",
        "      ansible.builtin.debug:",
        "        var: cleanup_out.stdout_lines",
    ]

    if values.get("reboot_after"):
        parts += [
            "    - name: Reboot host after cleanup",
            "      ansible.builtin.reboot:",
            "        msg: Reboot after stack uninstall",
            "        reboot_timeout: 600",
        ]

    parts.append("")
    return "\n".join(parts)
