# Stack Blueprints

Community-contributable catalog of ready-to-run OpenSible stacks. Each blueprint
is a self-contained folder that ships:

```
<group>/<blueprint-id>/
├── blueprint.yaml     # metadata, defaults, form schema, template wiring
├── playbook.yml       # ready-to-run Ansible playbook
├── vars.example.yml   # example variable overrides
└── README.md          # long-form docs
```

## Layout

```
IaC/blueprints/
├── groups.yaml                      # group metadata (order shown in console)
├── kubernetes/
│   ├── kubernetes-ha-kubeadm/        ✅ available
│   └── k3s-ha-etcd/                  ✅ available
└── docker/
    └── docker-engine/                ✅ available
```

The console reads these YAML files directly (Vite inlines them at build time via
`import.meta.glob`), so there is no generated snapshot and no TypeScript
registry. To update the catalog, edit a `blueprint.yaml` - nothing else.

## Adding a blueprint

1. Pick or add a group in `IaC/blueprints/groups.yaml`.
2. `cp -r kubernetes/kubernetes-ha-kubeadm <group>/<my-blueprint>`
3. Edit `blueprint.yaml`:
   - `id`, `name`, `description`, `tags`, `author`, `source`
   - `available: true` when a real `playbook.yml` is present
   - `templateId` and `filenameStem` to wire it to a backend template
   - `defaults` for pre-filled variables
   - `formSchema` for schema-driven configuration UI
4. Replace `playbook.yml` with your idempotent playbook.
5. Document variables in `vars.example.yml` and `README.md`.
6. Restart `bun run dev` (or rebuild) - the new blueprint is picked up
   automatically from its `blueprint.yaml`.

## `blueprint.yaml` reference

```yaml
id: my-blueprint
name: My Blueprint
group: kubernetes
description: One-line summary shown in the card.
logo: https://cdn.simpleicons.org/kubernetes/326CE5
tags: [k8s, example]
author: opensible
stars: 0
source: https://github.com/opensible/opensible/tree/main/IaC/blueprints/kubernetes/my-blueprint
available: true
templateId: my-template
filenameStem: my-blueprint
entrypoint: playbook.yml
requires:
  ansible: ">=2.14"
defaults:
  cluster_name: opensible
  become: true
formSchema:
  - key: cluster_name
    label: Cluster name
    type: text
    default: opensible
    group: Basics
```

Playbooks are treated as source-of-truth — the runner executes `playbook.yml`
unchanged. The UI renders cards and forms purely from `blueprint.yaml`.
