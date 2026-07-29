"""Template: Manage Linux users, groups, permissions, SSH keys and sudo.

A flexible user/group management template that lets operators declare a desired
state for groups and users across the selected hosts. Supports:

- Create / update / remove groups (with gid, system flag).
- Create / update / remove users (with uid, primary group, extra groups, shell,
  home directory, comment, expire date, account lock/unlock).
- Password management: set an initial password, force password change on next
  login, or perform a bulk password reset for existing users.
- SSH public key management via authorized_keys (add or replace).
- Sudo rights: per-user sudoers.d drop-in (with or without NOPASSWD).
- Optional deletion behaviour: remove home directory or just lock the account.
"""
from __future__ import annotations

from typing import Any, Dict, List

import yaml as _yaml

from ._common import render_hosts


TEMPLATE = {
    "id": "manage-users",
    "name": "Manage Linux Users & Groups",
    "category": "Identity",
    "icon": "users",
    "description": (
        "Declaratively manage Linux users, groups, sudo rights, SSH keys and "
        "password policy across hosts. Supports create/update/remove, password "
        "reset, force-change-on-next-login, account lock/unlock and per-user "
        "sudoers drop-ins."
    ),
    "tags": ["users", "groups", "iam", "linux", "ssh", "sudo", "security"],
    "variables": [
        {
            "name": "groups",
            "label": "Groups (YAML list)",
            "type": "code",
            "language": "yaml",
            "rows": 6,
            "help": (
                "One entry per group. Fields: name (required), gid, system "
                "(bool), state (present|absent). Example below."
            ),
            "default": (
                "# - name: developers\n"
                "#   gid: 2001\n"
                "#   state: present\n"
                "# - name: legacy-team\n"
                "#   state: absent\n"
                "[]\n"
            ),
        },
        {
            "name": "users",
            "label": "Users (YAML list)",
            "type": "code",
            "language": "yaml",
            "rows": 16,
            "help": (
                "One entry per user. Fields: name (required), uid, group "
                "(primary), groups (list, extra), shell, home, comment, "
                "state (present|absent), password (plain text — applied with "
                "Linux chpasswd), update_password (always|on_create), "
                "expires (YYYY-MM-DD), "
                "lock (bool), ssh_keys (list of public key strings — only for SSH-key users), "
                "ssh_keys_exclusive (bool — replace instead of append), "
                "sudo (bool | 'nopasswd' | 'full'), sudo_commands (list)."
            ),
            "default": (
                "# - name: alice\n"
                "#   uid: 3001\n"
                "#   group: developers\n"
                "#   groups: [docker, sudo]\n"
                "#   shell: /bin/bash\n"
                "#   comment: \"Alice Example\"\n"
                "#   password: \"ChangeMe!123\"\n"
                "#   update_password: always\n"
                "#   ssh_keys:\n"
                "#     - \"ssh-ed25519 AAAA... alice@laptop\"\n"
                "#   sudo: nopasswd\n"
                "# - name: bob\n"
                "#   state: absent\n"
                "[]\n"
            ),
        },
        {
            "name": "default_shell",
            "label": "Default shell",
            "type": "string",
            "default": "/bin/bash",
            "placeholder": "/bin/bash",
        },
        {
            "name": "create_home",
            "label": "Create home directory",
            "type": "boolean",
            "default": True,
        },
        {
            "name": "home_mode",
            "label": "Home directory mode",
            "type": "string",
            "default": "0750",
            "placeholder": "0750",
        },
        {
            "name": "ssh_login_group",
            "label": "SSH login group",
            "type": "string",
            "default": "ssh-users",
            "placeholder": "ssh-users",
            "help": (
                "SSH-key users are appended to this group. "
                "Password-only users do not touch SSH key/login-policy tasks. "
                "Leave empty to disable."
            ),
        },
        {
            "name": "manage_ssh_login_policy",
            "label": "Manage SSH login policy",
            "type": "boolean",
            "default": True,
            "help": (
                "When enabled for SSH-key users, removes a legacy AllowUsers line, "
                "writes AllowGroups for the SSH login group, validates sshd config, "
                "and reloads sshd if needed. Password-only users do not trigger this."
            ),
        },
        {
            "name": "reset_password",
            "label": "Reset password for existing users",
            "type": "boolean",
            "default": False,
            "help": "When enabled, every user with a password field is force-updated, even if update_password: on_create is set in the user YAML.",
        },
        {
            "name": "remove_home_on_delete",
            "label": "Remove home directory on user removal",
            "type": "boolean",
            "default": False,
        },
        {
            "name": "lock_instead_of_delete",
            "label": "Lock account instead of deleting (state=absent)",
            "type": "boolean",
            "default": False,
            "help": "Safer: keeps the user but disables login and expires the account.",
        },
        {
            "name": "sudoers_dir",
            "label": "sudoers.d directory",
            "type": "string",
            "default": "/etc/sudoers.d",
        },
        {
            "name": "become",
            "label": "Run as sudo (become)",
            "type": "boolean",
            "default": True,
        },
        {
            "name": "vault_files",
            "label": "Vault files (one path per line)",
            "type": "code",
            "language": "yaml",
            "rows": 4,
            "help": (
                "Paths to ansible-vault encrypted vars files, relative to the project root "
                "(e.g. group_vars/all/vault.yml). Loaded via vars_files so you can reference "
                "secrets in the Users list using Jinja, e.g. password: \"{{ vault_tolaleng_pw }}\". "
                "Requires the matching vault key attached in Infrastructure > Vaults."
            ),
            "default": "# - group_vars/all/vault.yml\n",
        },
    ],
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _parse_list(raw: Any) -> List[Dict[str, Any]]:
    """Parse a YAML list of dicts. Accepts already-parsed lists too."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        data = raw
    else:
        try:
            data = _yaml.safe_load(str(raw)) or []
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("name")]


def _yaml_dump(value: Any) -> str:
    dumped = _yaml.safe_dump(value, default_flow_style=True, width=99999).strip()
    if dumped.endswith("\n..."):
        dumped = dumped[:-4].rstrip()
    return dumped


def suggested_filename(values: Dict[str, Any]) -> str:
    return "tmpl-manage-users.yml"


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #

def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"
    default_shell = values.get("default_shell") or "/bin/bash"
    create_home = bool(values.get("create_home", True))
    home_mode = str(values.get("home_mode") or "0750")
    reset_password = bool(values.get("reset_password", False))
    remove_home = bool(values.get("remove_home_on_delete", False))
    lock_instead = bool(values.get("lock_instead_of_delete", False))
    sudoers_dir = values.get("sudoers_dir") or "/etc/sudoers.d"
    ssh_login_group = (
        "ssh-users"
        if "ssh_login_group" not in values
        else str(values.get("ssh_login_group") or "").strip()
    )
    manage_ssh_login_policy = bool(values.get("manage_ssh_login_policy", True))
    # Parse vault_files: accept YAML list OR plain lines. Strip comments/blanks.
    raw_vf = values.get("vault_files")
    vault_files: List[str] = []
    if raw_vf:
        parsed = None
        try:
            parsed = _yaml.safe_load(str(raw_vf))
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            vault_files = [str(p).strip() for p in parsed if str(p).strip()]
        else:
            for ln in str(raw_vf).splitlines():
                s = ln.strip().lstrip("-").strip()
                if not s or s.startswith("#"):
                    continue
                vault_files.append(s)

    groups_list = _parse_list(values.get("groups"))
    users_list = _parse_list(values.get("users"))

    if not groups_list and not users_list:
        raise ValueError(
            "Manage Linux Users & Groups: both the Users and Groups lists are empty. "
            "Uncomment or add at least one entry (the default example lines start with "
            "'#'). Example user entry:\n"
            "- name: alice\n"
            "  password: \"ChangeMe!123\"\n"
            "  sudo: nopasswd\n"
        )

    # Normalise / enrich user entries with runtime defaults so the play stays simple.
    normalised_users: List[Dict[str, Any]] = []
    for u in users_list:
        state = str(u.get("state") or "present").lower()
        entry: Dict[str, Any] = {
            "name": str(u["name"]),
            "state": state,
        }
        if u.get("uid") is not None:
            entry["uid"] = u["uid"]
        if u.get("group"):
            entry["group"] = str(u["group"])
        if u.get("groups"):
            gs = u["groups"] if isinstance(u["groups"], list) else [u["groups"]]
            entry["groups"] = [str(g) for g in gs if str(g).strip()]
            entry["append"] = bool(u.get("append", True))
        entry["shell"] = str(u.get("shell") or default_shell)
        if u.get("home"):
            entry["home"] = str(u["home"])
        if u.get("comment"):
            entry["comment"] = str(u["comment"])
        if u.get("expires"):
            entry["expires"] = str(u["expires"])
        entry["create_home"] = create_home
        entry["home_mode"] = home_mode

        if u.get("password"):
            entry["password_plain"] = str(u["password"])
            # IMPORTANT: default to "always" so password resets actually take
            # effect on existing users. The rendered playbook uses chpasswd
            # directly for password-only users so the plain password is applied
            # exactly as entered, instead of mixing password creation with SSH
            # key/login-policy behavior.
            entry["update_password"] = "always" if reset_password else str(u.get("update_password") or "always")
        entry["lock"] = bool(u.get("lock", False))

        ssh_keys = u.get("ssh_keys") or []
        if isinstance(ssh_keys, str):
            ssh_keys = [ssh_keys]
        entry["ssh_keys"] = [str(k).strip() for k in ssh_keys if str(k).strip()]
        entry["ssh_keys_exclusive"] = bool(u.get("ssh_keys_exclusive", False))

        sudo = u.get("sudo")
        if sudo:
            sudo_str = str(sudo).lower()
            entry["sudo_mode"] = "nopasswd" if sudo_str in ("nopasswd", "true", "yes", "1") else sudo_str
            cmds = u.get("sudo_commands") or ["ALL"]
            if isinstance(cmds, str):
                cmds = [cmds]
            entry["sudo_commands"] = [str(c) for c in cmds if str(c).strip()] or ["ALL"]

        normalised_users.append(entry)

    normalised_groups: List[Dict[str, Any]] = []
    for g in groups_list:
        item: Dict[str, Any] = {"name": str(g["name"]), "state": str(g.get("state") or "present").lower()}
        if g.get("gid") is not None:
            item["gid"] = g["gid"]
        if g.get("system"):
            item["system"] = bool(g["system"])
        normalised_groups.append(item)

    # Auto-ensure any groups referenced by user entries (primary group + extra
    # groups) that aren't already declared in the Groups list. Without this,
    # `ansible.builtin.user` fails with "Group X does not exist" whenever a
    # user references a group (e.g. docker, sudo) that hasn't been created yet.
    declared_group_names = {g["name"] for g in normalised_groups}
    referenced_group_names: List[str] = []
    seen_ref: set = set()
    for u in normalised_users:
        if u.get("state") != "present":
            continue
        candidates: List[str] = []
        if u.get("group"):
            candidates.append(str(u["group"]))
        for g in (u.get("groups") or []):
            candidates.append(str(g))
        for name in candidates:
            if not name or name in declared_group_names or name in seen_ref:
                continue
            seen_ref.add(name)
            referenced_group_names.append(name)
    referenced_groups = [{"name": n, "state": "present"} for n in referenced_group_names]

    login_check_users = [
        {
            "name": u["name"],
            "has_password": "password_plain" in u,
            "locked": bool(u.get("lock", False)),
        }
        for u in normalised_users
        if u.get("state") == "present"
    ]
    ssh_login_users = [
        {"name": u["name"]}
        for u in normalised_users
        if u.get("state") == "present"
        and not bool(u.get("lock", False))
        and bool(u.get("ssh_keys"))
    ]
    any_ssh_keys = any(
        u.get("state") == "present" and u.get("ssh_keys")
        for u in normalised_users
    )

    # ---- Build YAML text ---------------------------------------------------
    lines: List[str] = [
        "---",
        f"# Rendered from template: {TEMPLATE['name']}",
        f"- name: Manage Linux users and groups",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *([f"  vars_files:"] + [f"    - {vf}" for vf in vault_files] if vault_files else []),
        "  vars:",
        f"    manage_users_lock_instead_of_delete: {'true' if lock_instead else 'false'}",
        f"    manage_users_remove_home: {'true' if remove_home else 'false'}",
        f"    manage_users_sudoers_dir: {sudoers_dir}",
        f"    manage_users_groups: {_yaml_dump(normalised_groups)}",
        f"    manage_users_referenced_groups: {_yaml_dump(referenced_groups)}",
        f"    manage_users_users: {_yaml_dump(normalised_users)}",
        f"    manage_users_login_checks: {_yaml_dump(login_check_users)}",
        *([
            f"    manage_users_ssh_login_group: {_yaml_dump(ssh_login_group)}",
            f"    manage_users_manage_ssh_login_policy: {'true' if manage_ssh_login_policy else 'false'}",
            f"    manage_users_ssh_login_users: {_yaml_dump(ssh_login_users)}",
        ] if any_ssh_keys else []),
        "  tasks:",
        "    # ---------- Groups ----------",
        "    - name: Ensure groups",
        "      ansible.builtin.group:",
        "        name: \"{{ item.name }}\"",
        "        gid: \"{{ item.gid | default(omit) }}\"",
        "        system: \"{{ item.system | default(omit) }}\"",
        "        state: \"{{ item.state | default('present') }}\"",
        "      loop: \"{{ manage_users_groups }}\"",
        "      when: manage_users_groups | length > 0",
        "",
        "    - name: Ensure referenced user groups exist (auto)",
        "      ansible.builtin.group:",
        "        name: \"{{ item.name }}\"",
        "        state: present",
        "      loop: \"{{ manage_users_referenced_groups }}\"",
        "      when: manage_users_referenced_groups | length > 0",
        "",
        "    # ---------- Users (present) ----------",
        "    - name: Ensure users (present)",
        "      ansible.builtin.user:",
        "        name: \"{{ item.name }}\"",
        "        uid: \"{{ item.uid | default(omit) }}\"",
        "        group: \"{{ item.group | default(omit) }}\"",
        "        groups: \"{{ item.groups | default(omit) }}\"",
        "        append: \"{{ item.append | default(omit) }}\"",
        "        shell: \"{{ item.shell | default(omit) }}\"",
        "        home: \"{{ item.home | default(omit) }}\"",
        "        comment: \"{{ item.comment | default(omit) }}\"",
        "        create_home: \"{{ item.create_home | default(true) }}\"",
        "        expires: \"{{ (item.expires | to_datetime('%Y-%m-%d')).timestamp() | int if item.expires is defined else -1 }}\"",
        "        update_password: \"{{ item.update_password | default(omit) }}\"",
        "        password_lock: \"{{ item.lock | default(false) }}\"",
        "        state: present",
        "      loop: \"{{ manage_users_users | selectattr('state','equalto','present') | list }}\"",
        "      loop_control:",
        "        label: \"{{ item.name }}\"",
        "      no_log: true",
        "",
        "    - name: Set passwords for password users",
        "      ansible.builtin.command: chpasswd",
        "      args:",
        "        stdin: \"{{ item.name }}:{{ item.password_plain }}\"",
        "      loop: \"{{ manage_users_users | selectattr('state','equalto','present') | selectattr('password_plain','defined') | list }}\"",
        "      loop_control:",
        "        label: \"{{ item.name }}\"",
        "      changed_when: true",
        "      no_log: true",
        "",
        "    - name: Check password login state",
        "      ansible.builtin.command: passwd -S {{ item.name }}",
        "      loop: \"{{ manage_users_login_checks | selectattr('has_password','equalto',true) | list }}\"",
        "      loop_control:",
        "        label: \"{{ item.name }}\"",
        "      register: manage_users_passwd_status",
        "      changed_when: false",
        "      failed_when: false",
        "",
        "    - name: Fail if password login is still locked",
        "      ansible.builtin.fail:",
        "        msg: \"User {{ item.item.name }} was created/updated, but password login is still locked or has no password (passwd status: {{ item.stdout }}). Set lock: false, enable Reset password for existing users, or check PAM/SSH policy on the host.\"",
        "      loop: \"{{ manage_users_passwd_status.results | default([]) }}\"",
        "      loop_control:",
        "        label: \"{{ item.item.name }}\"",
        "      when:",
        "        - not (item.item.locked | default(false) | bool)",
        "        - item.stdout is defined",
        "        - (item.stdout.split() | length) > 1",
        "        - item.stdout.split()[1] in ['L', 'LK', 'NP']",
        "",
        "    - name: Set home directory permissions",
        "      ansible.builtin.file:",
        "        path: \"{{ item.home | default('/home/' ~ item.name) }}\"",
        "        state: directory",
        "        owner: \"{{ item.name }}\"",
        "        group: \"{{ item.group | default(item.name) }}\"",
        "        mode: \"{{ item.home_mode | default('0750') }}\"",
        "      loop: \"{{ manage_users_users | selectattr('state','equalto','present') | list }}\"",
        "      loop_control:",
        "        label: \"{{ item.name }}\"",
        "      when: (item.create_home | default(true)) | bool",
        "      no_log: true",
        "",
    ]

    # Only emit SSH login policy / authorized_keys blocks if at least one user
    # actually provides ssh_keys. This keeps password-only user renders free of
    # SSH key tasks (user requested distinct password-vs-sshkey behaviour).
    if any_ssh_keys:
        lines.extend([
            "    # ---------- SSH login policy ----------",
            "    - name: Ensure SSH login group",
            "      ansible.builtin.group:",
            "        name: \"{{ manage_users_ssh_login_group }}\"",
            "        state: present",
            "      when:",
            "        - manage_users_ssh_login_group | length > 0",
            "        - manage_users_ssh_login_users | length > 0",
            "",
            "    - name: Allow managed SSH-key users to pass SSH AllowGroups",
            "      ansible.builtin.user:",
            "        name: \"{{ item.name }}\"",
            "        groups: \"{{ manage_users_ssh_login_group }}\"",
            "        append: true",
            "      loop: \"{{ manage_users_ssh_login_users }}\"",
            "      loop_control:",
            "        label: \"{{ item.name }}\"",
            "      when: manage_users_ssh_login_group | length > 0",
            "      no_log: true",
            "",
            "    - name: Ensure Ansible connection user is in SSH login group (lockout guard)",
            "      ansible.builtin.user:",
            "        name: \"{{ ansible_user | default(ansible_user_id) }}\"",
            "        groups: \"{{ manage_users_ssh_login_group }}\"",
            "        append: true",
            "      when:",
            "        - manage_users_manage_ssh_login_policy | bool",
            "        - manage_users_ssh_login_group | length > 0",
            "        - manage_users_ssh_login_users | length > 0",
            "        - (ansible_user | default(ansible_user_id)) | length > 0",
            "",
            "    - name: Remove legacy SSH AllowUsers baseline",
            "      ansible.builtin.lineinfile:",
            "        path: /etc/ssh/sshd_config.d/10-dbcs.conf",
            "        regexp: '^AllowUsers\\s+'",
            "        state: absent",
            "      register: manage_users_ssh_allowusers",
            "      when:",
            "        - manage_users_manage_ssh_login_policy | bool",
            "        - manage_users_ssh_login_group | length > 0",
            "        - manage_users_ssh_login_users | length > 0",
            "",
            "    - name: Allow SSH login for managed SSH-key users group",
            "      ansible.builtin.lineinfile:",
            "        path: /etc/ssh/sshd_config.d/10-dbcs.conf",
            "        create: true",
            "        regexp: '^AllowGroups\\s+'",
            "        line: \"AllowGroups {{ manage_users_ssh_login_group }}\"",
            "        owner: root",
            "        group: root",
            "        mode: '0644'",
            "      register: manage_users_ssh_allowgroups",
            "      when:",
            "        - manage_users_manage_ssh_login_policy | bool",
            "        - manage_users_ssh_login_group | length > 0",
            "        - manage_users_ssh_login_users | length > 0",
            "",
            "    - name: Drop legacy per-user SSH password-login overrides",
            "      ansible.builtin.file:",
            "        path: /etc/ssh/sshd_config.d/10-{{ item.name }}.conf",
            "        state: absent",
            "      loop: \"{{ manage_users_ssh_login_users }}\"",
            "      loop_control:",
            "        label: \"{{ item.name }}\"",
            "      register: manage_users_ssh_user_overrides",
            "      when:",
            "        - manage_users_manage_ssh_login_policy | bool",
            "        - manage_users_ssh_login_group | length > 0",
            "",
            "    - name: Validate sshd configuration",
            "      ansible.builtin.command: /usr/sbin/sshd -t",
            "      changed_when: false",
            "      when:",
            "        - manage_users_manage_ssh_login_policy | bool",
            "        - manage_users_ssh_login_group | length > 0",
            "        - manage_users_ssh_login_users | length > 0",
            "        - (manage_users_ssh_allowusers.changed | default(false)) or (manage_users_ssh_allowgroups.changed | default(false)) or (manage_users_ssh_user_overrides.changed | default(false))",
            "",
            "    - name: Reload sshd after login policy change",
            "      ansible.builtin.service:",
            "        name: \"{{ 'ssh' if ansible_facts.os_family == 'Debian' else 'sshd' }}\"",
            "        state: reloaded",
            "      when:",
            "        - manage_users_manage_ssh_login_policy | bool",
            "        - manage_users_ssh_login_group | length > 0",
            "        - manage_users_ssh_login_users | length > 0",
            "        - (manage_users_ssh_allowusers.changed | default(false)) or (manage_users_ssh_allowgroups.changed | default(false)) or (manage_users_ssh_user_overrides.changed | default(false))",
            "",
            "    # ---------- SSH authorized_keys ----------",
            "    - name: Manage authorized_keys",
            "      ansible.posix.authorized_key:",
            "        user: \"{{ item.0.name }}\"",
            "        key: \"{{ item.1 }}\"",
            "        state: present",
            "        exclusive: \"{{ item.0.ssh_keys_exclusive | default(false) }}\"",
            "      loop: \"{{ manage_users_users | selectattr('state','equalto','present') | subelements('ssh_keys', skip_missing=True) }}\"",
            "",
        ])

    lines.extend([
        "    # ---------- Sudoers drop-ins ----------",
        "    - name: Ensure sudoers.d directory",
        "      ansible.builtin.file:",
        "        path: \"{{ manage_users_sudoers_dir }}\"",
        "        state: directory",
        "        owner: root",
        "        group: root",
        "        mode: '0750'",
        "      when: manage_users_users | selectattr('sudo_mode','defined') | list | length > 0",
        "",
        "    - name: Deploy per-user sudoers drop-ins",
        "      ansible.builtin.copy:",
        "        dest: \"{{ manage_users_sudoers_dir }}/90-{{ item.name }}\"",
        "        owner: root",
        "        group: root",
        "        mode: '0440'",
        "        validate: \"visudo -cf %s\"",
        "        content: |",
        "          {{ item.name }} ALL=(ALL) {{ 'NOPASSWD:' if item.sudo_mode == 'nopasswd' else '' }} {{ item.sudo_commands | default(['ALL']) | join(',') }}",
        "      loop: \"{{ manage_users_users | selectattr('state','equalto','present') | selectattr('sudo_mode','defined') | list }}\"",
        "      loop_control:",
        "        label: \"{{ item.name }}\"",
        "      no_log: true",
        "",
        "    - name: Remove per-user sudoers drop-ins (users without sudo)",
        "      ansible.builtin.file:",
        "        path: \"{{ manage_users_sudoers_dir }}/90-{{ item.name }}\"",
        "        state: absent",
        "      loop: \"{{ manage_users_users | selectattr('state','equalto','present') | rejectattr('sudo_mode','defined') | list }}\"",
        "",
        "    # ---------- Users (removal) ----------",
        "    - name: Lock accounts marked for removal (safe mode)",
        "      ansible.builtin.user:",
        "        name: \"{{ item.name }}\"",
        "        password_lock: true",
        "        shell: /usr/sbin/nologin",
        "        expires: 1",
        "      loop: \"{{ manage_users_users | selectattr('state','equalto','absent') | list }}\"",
        "      when: manage_users_lock_instead_of_delete | bool",
        "",
        "    - name: Delete users marked for removal",
        "      ansible.builtin.user:",
        "        name: \"{{ item.name }}\"",
        "        state: absent",
        "        remove: \"{{ manage_users_remove_home }}\"",
        "        force: \"{{ manage_users_remove_home }}\"",
        "      loop: \"{{ manage_users_users | selectattr('state','equalto','absent') | list }}\"",
        "      when: not (manage_users_lock_instead_of_delete | bool)",
        "",
        "    - name: Remove sudoers drop-ins for deleted users",
        "      ansible.builtin.file:",
        "        path: \"{{ manage_users_sudoers_dir }}/90-{{ item.name }}\"",
        "        state: absent",
        "      loop: \"{{ manage_users_users | selectattr('state','equalto','absent') | list }}\"",
        "",
        "    # ---------- Group removal (last, after users are detached) ----------",
        "    - name: Remove groups marked absent",
        "      ansible.builtin.group:",
        "        name: \"{{ item.name }}\"",
        "        state: absent",
        "      loop: \"{{ manage_users_groups | selectattr('state','equalto','absent') | list }}\"",
        "",
    ])
    return "\n".join(lines)
