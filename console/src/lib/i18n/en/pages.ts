export const pages: Record<string, string> = {
  // Home dashboard
  "page.home.title": "Home Dashboard",
  "page.home.subtitle": "Overview across your projects, cloud provisioning and infrastructure automation.",

  // Project settings (Ansible)
  "page.settings.title": "Project Settings",
  "page.settings.subtitle": "Git source configuration for the Ansible repository workspace (roles, playbooks, inventories, ansible.cfg).",

  // Cloud - cost
  "page.cost.title": "Cost Analysis",
  "page.cost.subtitle": "Manage provider pricing, estimate provisioning costs, and review per-Apply cost reports.",
  "page.cost.compute": "Compute",
  "page.cost.storage": "Storage",
  "page.cost.network": "Network",
  "page.cost.managed": "Managed Services",

  // Cloud - stack settings
  "page.cloudSettings.title": "Stack Project Settings",
  "page.cloudSettings.subtitle": "Git source configuration for the OpenTofu/Terraform stacks workspace used by Cloud Provisioning.",

  // Cloud - summary
  "page.summary.title": "Provisioning Summary",
  "page.summary.subtitle": "OpenTofu/Terraform runs across all stacks in this project.",

  // Infrastructure - deployment
  "page.deployment.title": "Deployment",
  "page.deployment.subtitle": "Ansible deployment runs across this project.",

  // Infrastructure - hosts
  "page.hosts.title": "Hosts & Groups",
  "page.hosts.subtitle": "Manage Ansible inventories, groups, hosts and variables.",

  // Infrastructure - playbooks
  "page.playbooks.title": "Playbooks",
  "page.playbooks.subtitle": "Reusable Ansible playbooks for deployment and orchestration.",
  "page.playbooks.subtitleCount": "{count} playbooks in this project.",

  // Infrastructure - roles
  "page.roles.title": "Roles",
  "page.roles.subtitle": "Reusable Ansible roles and tasks organized by pack.",

  // Infrastructure - combined playbooks & roles
  "page.playbooksRoles.title": "Playbooks & Roles",
  "page.playbooksRoles.subtitle": "Manage reusable Ansible playbooks and roles from one place.",

  // Infrastructure - templates
  "page.templates.title": "Stack Deployment & Templates",
  "page.templates.subtitle": "Reusable Build & Deployment Jobs — parameterized template runs with lifecycle actions (Init, Validate, Plan, Apply).",

  // Infrastructure - vaults & secrets
  "page.vaults.title": "Vaults & Secrets",
  "page.vaults.subtitle": "Manage ansible-vault keys, inspect encrypted files in your inventory, and store SSH keys / passwords used by Ansible runs.",

  // System - api
  "page.api.title": "API",
  "page.api.subtitle": "Programmatic access to the platform.",

  // System - secrets
  "page.systemSecrets.title": "Secrets Management",
  "page.systemSecrets.subtitle": "Global encrypted secrets — SSH keys, tokens, and credentials referenced by stacks and projects.",

  // System - server logs
  "page.serverLogs.title": "Server Logs",
  "page.serverLogs.subtitle": "Live tail of backend, worker, and frontend logs.",

  // System - settings
  "page.systemSettings.title": "System Settings",
  "page.systemSettings.subtitle": "Application preferences and system options.",

  // System - users
  "page.users.title": "Team Members",
  "page.users.subtitle": "Manage users, roles, and permissions.",

  // System - workers
  "page.workers.title": "Workers",
  "page.workers.subtitle": "Worker agents that execute Ansible / OpenTofu jobs.",


  // Workers page
  "workers.addWorker": "Add Worker",
  "workers.agents": "Worker Agents",
  "workers.agentsDesc": "Registered worker nodes and their heartbeat status.",
  "workers.loading": "Loading workers…",
  "workers.empty": "No workers registered",
  "workers.emptyHint": "Click \"Add Worker\" to create one",

  // Users page
  "common.users": "Users",
  "users.emptyUsers": "No users found",
  "users.emptyRolesSearch": "No roles found",
  "users.noRoles": "No roles defined",
  "users.noPermissions": "No permissions",

  // Templates / Jobs page
  "templates.tab.jobs": "Build & Deployment Jobs",
  "templates.tab.catalog": "Resource Catalog",
  "templates.tab.blueprints": "Stack Blueprints",
  "templates.tab.jobs.desc": "Execute and monitor your automation jobs.",
  "templates.tab.catalog.desc": "Manage reusable infrastructure resources and configuration.",
  "templates.tab.blueprints.desc": "Browse reusable deployment templates from the community.",
  "templates.searchTemplates": "Search templates…",
  "templates.searchJobs": "Search jobs…",
  "templates.templateJobs": "Template Jobs",
  "templates.noMatch": "No templates match your search.",

  // Vaults & Secrets
  "vaults.tab.keys": "Vault Keys",
  "vaults.tab.files": "Encrypted Files",
  "vaults.tab.secrets": "Machine Secrets",
  "vaults.card.vaults": "Vaults",
  "vaults.card.vaultKeys": "Vault Keys",
  "vaults.noKeys": "No keys configured.",
  "vaults.vaultId": "Vault ID",
  "vaults.path": "Path",
  "vaults.vault": "Vault",
  "vaults.noEncrypted": "No encrypted files detected in the inventory repo.",
  "vaults.noSecrets": "No secrets found.",

  // Cost analysis
  "cost.providers": "Providers",
  "cost.loadingPricing": "Loading pricing…",
  "cost.step1": "Step 1 · Source & Provider",
  "cost.step2": "Step 2 · Infrastructure Resources",
  "cost.provider": "Provider",
  "cost.resourceSource": "Resource source",
  "cost.stack": "Stack",
  "cost.pastePlan": "Paste OpenTofu/Terraform plan JSON",
  "cost.costByService": "Cost by service",
  "cost.monthlyByResource": "Monthly cost by resource",
  "cost.detailedBreakdown": "Detailed cost breakdown",
  "cost.autoSaved": "Auto-saved after each successful Apply",
  "cost.resource": "Resource",
  "cost.kind": "Kind",
  "cost.unit": "Unit",
  "cost.unitPrice": "Unit Price",
  "cost.monthly": "Monthly",
  "cost.yearly": "Yearly",
  "cost.total": "Total",
  "cost.noResources": "No resources — click \"Add resource\" to start.",
  "cost.selectStack": "Select a stack…",
  "cost.allStacks": "All stacks",
  "cost.allProviders": "All providers",
  "cost.from": "From",
  // Home dashboard
  "page.home.totalProjects": "Total Projects",
  "page.home.activeProject": "Active: {name}",
  "page.home.noActiveProject": "No active project",
  "page.home.cloudProvisioning": "Cloud Provisioning",
  "page.home.opentofuStacks": "OpenTofu stacks",
  "page.home.infrastructure": "Infrastructure",
  "page.home.ansiblePlaybooks": "Ansible playbooks",
  "page.home.activeRuns": "Active Runs",
  "page.home.totalInHistory": "{count} total in history",
  "page.home.recentStacks": "Recent Stacks",
  "page.home.viewAll": "View all",
  "page.home.noStacks": "No stacks yet",
  "page.home.createStack": "Create a stack",
  "page.home.yourProjects": "Your Projects",
  "page.home.new": "New",
  "page.home.noProjects": "No projects yet",
  "page.home.createFirstProject": "Create your first project",
  // Provisioning Summary
  "summary.total": "Total",
  "summary.running": "Running",
  "summary.queued": "Queued",
  "summary.succeeded": "Succeeded",
  "summary.failed": "Failed",
  "summary.searchPlaceholder": "Search stack, project, env, provider, ID…",
  "summary.allStatuses": "All statuses",
  "summary.allActions": "All actions",
  "summary.allProviders": "All providers",
  "summary.allCloudProjects": "All cloud projects",
  "summary.allEnvironments": "All environments",
  "summary.allStacks": "All stacks",
  "summary.runsCount": "{shown} of {total} runs",
  "summary.col.provider": "Provider",
  "summary.col.project": "Project",
  "summary.col.env": "Env",
  "summary.col.dateTime": "Date / Time",
  "summary.col.job": "Job",
  "summary.col.duration": "Duration",
  "summary.col.age": "Age",
  "summary.col.status": "Status",
  "summary.noRuns": "No runs match the current filters.",
  "summary.loadingRun": "Loading run…",
  "summary.loadingLog": "Loading log…",
  "summary.exit": "exit",
  "summary.vmInventory": "Inventory Resources",
  "summary.vmCount": "{count} VMs",
  "summary.vmCountOne": "{count} VM",
  "summary.loadingInventory": "Loading inventory…",
  "summary.inventoryError": "Failed to load inventory.",
  "summary.noVms": "No VMs in tfstate yet.",
  "summary.vm.hostname": "Hostname",
  "summary.vm.status": "Status",
  "summary.vm.privateIp": "Private IP",
  "summary.vm.publicIp": "Public IP",
  "summary.vm.subnet": "Subnet",
  "summary.vm.vpc": "VPC",
  "summary.vm.az": "AZ",
  "summary.vm.flavor": "Flavor",
  "summary.vm.disk": "Disk",
  "summary.justNow": "just now",
  "summary.agoSeconds": "{n}s ago",
  "summary.agoMinutes": "{n}m ago",
  "summary.agoHours": "{n}h ago",
  "summary.agoDays": "{n}d ago",
};
