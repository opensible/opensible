import type { BlueprintGroup, FormSchemaField } from "./types";

const LOGO = "https://cdn.simpleicons.org/ansible/EE0000";

const formSchema: FormSchemaField[] = [
  // ── Play ─────────────────────────────────────────────────────────────
  { key: "play_name", label: "Play name", type: "text",
    default: "Generic deployment", group: "Play",
    help: "Human-readable name shown in the play header." },
  { key: "become", label: "Run as sudo (become)", type: "bool",
    default: true, group: "Play" },
  { key: "gather_facts", label: "Gather facts", type: "bool",
    default: true, group: "Play" },
  { key: "any_errors_fatal", label: "Any errors fatal", type: "bool",
    default: false, group: "Play",
    help: "Abort the whole play on the first host failure." },
  { key: "serial", label: "Serial / rolling batch size", type: "text",
    default: "", group: "Play",
    placeholder: "e.g. 1, 25%, 2",
    help: "Leave blank to run on all hosts in parallel." },

  // ── Packages ─────────────────────────────────────────────────────────
  { key: "packages", label: "Packages", type: "list",
    default: [], group: "Packages",
    serialize: "yaml-list",
    help: "Managed with apt on Debian/Ubuntu and dnf on RedHat/CentOS." },
  { key: "packages_state", label: "Package state", type: "select",
    default: "present", group: "Packages",
    options: [
      { value: "present", label: "present" },
      { value: "latest", label: "latest" },
      { value: "absent", label: "absent" },
    ] },
  { key: "update_cache", label: "Refresh package cache first", type: "bool",
    default: true, group: "Packages" },

  // ── Services ─────────────────────────────────────────────────────────
  { key: "services", label: "Systemd services (YAML list)", type: "code",
    language: "yaml", rows: 5, group: "Services",
    default:
      "# - name: nginx\n#   state: restarted\n#   enabled: true\n",
    help: "Each item accepts: name, state, enabled, daemon_reload." },

  // ── Files & directories ──────────────────────────────────────────────
  { key: "directories", label: "Directories to ensure (YAML list)", type: "code",
    language: "yaml", rows: 4, group: "Files & Directories",
    default:
      "# - path: /opt/app\n#   owner: root\n#   group: root\n#   mode: '0755'\n" },
  { key: "files", label: "Files to drop (YAML list)", type: "code",
    language: "yaml", rows: 6, group: "Files & Directories",
    default:
      "# - dest: /etc/myapp/config.yml\n#   mode: '0644'\n#   content: |\n#     hello: world\n",
    help: "Each item needs `dest` and `content`." },
  { key: "git_repos", label: "Git checkouts (YAML list)", type: "code",
    language: "yaml", rows: 4, group: "Files & Directories",
    default:
      "# - repo: https://github.com/example/app.git\n#   dest: /opt/app\n#   version: main\n" },

  // ── Shell steps ──────────────────────────────────────────────────────
  { key: "pre_shell", label: "Pre-shell commands (one per line)", type: "textarea",
    rows: 4, group: "Shell steps",
    default: "", placeholder: "echo 'preparing…'",
    help: "Runs before packages/files/services." },
  { key: "post_shell", label: "Post-shell commands (one per line)", type: "textarea",
    rows: 4, group: "Shell steps",
    default: "", placeholder: "systemctl status myapp --no-pager || true",
    help: "Runs after everything else." },

  // ── Environment ──────────────────────────────────────────────────────
  { key: "env_vars", label: "Environment variables (YAML mapping)", type: "code",
    language: "yaml", rows: 4, group: "Environment",
    default: "# HTTP_PROXY: http://proxy:3128\n# APP_ENV: production\n" },

  // ── Advanced / Raw ───────────────────────────────────────────────────
  { key: "extra_tasks", label: "Extra tasks (raw YAML, appended verbatim)", type: "code",
    language: "yaml", rows: 6, group: "Advanced",
    default:
      "# - name: My custom task\n#   ansible.builtin.debug:\n#     msg: hello from generic template\n" },
  { key: "extra_handlers", label: "Handlers (raw YAML)", type: "code",
    language: "yaml", rows: 4, group: "Advanced",
    default:
      "# - name: reload nginx\n#   ansible.builtin.service:\n#     name: nginx\n#     state: reloaded\n" },
  { key: "raw_playbook", label: "Raw playbook YAML (overrides everything above)", type: "code",
    language: "yaml", rows: 12, group: "Advanced",
    default:
      "# Paste a complete Ansible playbook here to run it verbatim.\n# When non-empty, all fields above are ignored.\n",
    help: "Full playbook mode. Overrides all structured fields when non-empty." },
];

export const genericGroup: BlueprintGroup = {
  id: "generic",
  name: "Generic",
  description: "Schema-driven, general-purpose runners for any Ansible playbook.",
  logo: LOGO,
  blueprints: [
    {
      id: "generic",
      name: "Generic / General-purpose",
      description:
        "Flexible runner powered by a schema-driven form. Compose packages, files, services, shell steps and raw tasks — or paste any complete Ansible playbook.",
      logo: LOGO,
      tags: ["generic", "raw", "custom", "install", "deploy", "config", "shell"],
      author: "opensible",
      stars: 24,
      available: true,
      templateId: "generic",
      filenameStem: "generic",
      formSchema,
    },
  ],
};
