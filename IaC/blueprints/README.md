# Stack Blueprints

Community-contributable catalog of ready-to-run OpenSible stacks. Each blueprint
is a self-contained folder that ships:

```
<category>/<blueprint-id>/
├── blueprint.yaml     # metadata: name, description, tags, author, source, logo
├── playbook.yml       # ready-to-run Ansible playbook
├── vars.example.yml   # example variable overrides
└── README.md          # long-form docs
```

## Layout

```
IaC/blueprints/
├── kubernetes/
│   ├── kubernetes-ha-kubeadm/   ✅ available
│   └── k3s-ha-etcd/             ✅ available
└── docker/
    └── docker-engine/           ✅ available
```

Blueprints listed in `src/lib/blueprints/*.ts` without a matching folder here
render as **Coming soon** in the UI.

## Contributing a blueprint

1. `cp -r kubernetes/k3s-ha-etcd kubernetes/<my-blueprint>`
2. Edit `blueprint.yaml` (bump `id`, `name`, `author`, `source`).
3. Replace `playbook.yml` with your playbook. Keep it idempotent.
4. Document required variables in `vars.example.yml` and `README.md`.
5. Add an entry under `src/lib/blueprints/<group>.ts` with `available: true`.

Playbooks are treated as source-of-truth — the UI reads `blueprint.yaml` for
display metadata and hands `playbook.yml` to the runner unchanged.
