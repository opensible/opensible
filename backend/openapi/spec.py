#!/usr/bin/env python3
"""
OpenAPI 3.0 specification for OpenSible API.
"""
from typing import Any, Dict

# Reusable response schemas
RESPONSES = {
    "200": {"description": "Success"},
    "201": {"description": "Created"},
    "400": {
        "description": "Bad Request",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/Error"},
                "example": {"error": "Invalid request", "message": "Validation failed"},
            }
        },
    },
    "401": {
        "description": "Unauthorized",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/Error"},
                "example": {"error": "Unauthorized", "message": "Invalid or missing token"},
            }
        },
    },
    "403": {
        "description": "Forbidden",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/Error"},
                "example": {"error": "Forbidden", "message": "Permission denied"},
            }
        },
    },
    "404": {
        "description": "Not Found",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/Error"},
                "example": {"error": "Not found", "message": "Resource not found"},
            }
        },
    },
}


def get_openapi_spec(base_url: str = "http://localhost:5000") -> Dict[str, Any]:
    """Generate OpenAPI 3.0 spec for the platform API."""
    base_url = base_url.rstrip("/")
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "OpenSible API",
            "description": "Programmatic access to manage projects, executions, inventory, secrets, vaults and runs. Requires Bearer token authentication.",
            "version": "v1",
        },
        "servers": [{"url": base_url}],
        "tags": [
            {"name": "Auth", "description": "Authentication and session management"},
            {"name": "Users", "description": "User management"},
            {"name": "Roles", "description": "Role management"},
            {"name": "Permissions", "description": "Permission management"},
            {"name": "Projects", "description": "Project management"},
            {"name": "Hosts", "description": "Host and inventory management"},
            {"name": "Groups", "description": "Inventory groups"},
            {"name": "Inventory", "description": "Inventory files and structure"},
            {"name": "Variables", "description": "Group and host variables"},
            {"name": "Playbooks", "description": "Playbook management"},
            {"name": "Executions", "description": "Execution history and logs"},
            {"name": "Runs", "description": "Playbook runs"},
            {"name": "Secrets", "description": "Project and global secrets"},
            {"name": "Vaults", "description": "Vault keys and encrypted files"},
            {"name": "Backups", "description": "Project backups and archives"},
            {"name": "Ansible", "description": "Ansible config and execution"},
            {"name": "Ansible Roles", "description": "Ansible roles storage (pack/role)"},
            {"name": "Admin", "description": "Admin and worker management"},
            {"name": "Worker", "description": "Worker API (internal, uses worker token)"},
            {"name": "Misc", "description": "Utilities and helpers"},
            {"name": "API Tokens", "description": "API access tokens"},
            {"name": "Cloud Provisioning", "description": "OpenTofu/Terraform stacks for cloud infrastructure (ByteDC)"},
            {"name": "Cloud Stacks", "description": "Stack CRUD, runs, logs and SSE streams"},
        ],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT/API Token",
                    "description": "Bearer token (JWT or API token). Create tokens in API → Tokens tab.",
                }
            },
            "schemas": {
                "Project": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Project ID"},
                        "name": {"type": "string", "description": "Project name"},
                        "description": {"type": "string", "description": "Project description"},
                    },
                },
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
            },
        },
        "security": [{"bearerAuth": []}],
        "paths": {
            "/api/projects": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "List projects",
                    "description": "Returns all projects. Optionally include archived projects.",
                    "parameters": [
                        {
                            "name": "include_archived",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean", "default": False},
                            "description": "Include archived projects in the result.",
                        }
                    ],
                    "responses": {
                        "200": {"description": "List of projects"},
                        "401": RESPONSES["401"],
                    },
                },
                "post": {
                    "tags": ["Projects"],
                    "summary": "Create project",
                    "description": "Creates a new project. Requires name and optional description.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Created project"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                    },
                },
            },
            "/api/projects/{project_id}": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "Get project",
                    "description": "Returns a single project by ID.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "Project details"},
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
                "put": {
                    "tags": ["Projects"],
                    "summary": "Update project",
                    "description": "Updates project metadata. Does not affect project data or sources.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Updated project"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
                "delete": {
                    "tags": ["Projects"],
                    "summary": "Delete project",
                    "description": "**Destructive.** Permanently deletes the project and all related data (inventory, playbooks, executions, secrets, vaults). This action cannot be undone.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "Project deleted"},
                        "401": RESPONSES["401"],
                        "403": RESPONSES["403"],
                        "404": RESPONSES["404"],
                    },
                },
            },
            "/api/projects/{project_id}/hosts_status": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "Get hosts status",
                    "description": "Returns host check status for all project hosts.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Hosts status"}, "401": RESPONSES["401"], "500": {"description": "Error"}},
                },
            },
            "/api/projects/{project_id}/host_settings": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "Get host settings",
                    "description": "Returns host status TTL and auto-check settings.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Host settings"}, "401": RESPONSES["401"], "500": {"description": "Error"}},
                },
                "put": {
                    "tags": ["Projects"],
                    "summary": "Update host settings",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"ttl_seconds": {"type": "integer"}, "auto_check_all_hosts": {"type": "boolean"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Settings updated"}, "401": RESPONSES["401"], "500": {"description": "Error"}},
                },
            },
            "/api/projects/{project_id}/sources": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "Get sources",
                    "description": "Returns project sources (git, etc.) configuration.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Sources config"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Projects"],
                    "summary": "Update sources",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Sources updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/sources/status": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "Get sources status",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Sources status"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/sources/revert": {
                "post": {
                    "tags": ["Projects"],
                    "summary": "Revert sources",
                    "description": "Reverts local changes to sources.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Reverted"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/sources/analyze": {
                "post": {
                    "tags": ["Projects"],
                    "summary": "Analyze sources",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Analysis result"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/sources/test": {
                "post": {
                    "tags": ["Projects"],
                    "summary": "Test sources",
                    "description": "Tests source connection (e.g. git clone).",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Test result"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/sources/{source_key}/sync": {
                "post": {
                    "tags": ["Projects"],
                    "summary": "Sync source",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "source_key", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Sync started"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/sources/{source_key}/sync/state": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "Get sync state",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "source_key", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Sync state"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/sources/{source_key}/sync/check-conflict": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "Check sync conflict",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "source_key", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Conflict check"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/sources/sync": {
                "post": {
                    "tags": ["Projects"],
                    "summary": "Sync all sources",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Sync result"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/sources/resolve": {
                "post": {
                    "tags": ["Projects"],
                    "summary": "Resolve sources",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Resolved"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/autosync": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "Get autosync config",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Autosync config"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Projects"],
                    "summary": "Update autosync config",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Autosync updated"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/restore": {
                "post": {
                    "tags": ["Projects"],
                    "summary": "Restore project",
                    "description": "Restores project from backup.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Restored"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/switch": {
                "post": {
                    "tags": ["Projects"],
                    "summary": "Switch project branch",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Switched"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/queue-stats": {
                "get": {
                    "tags": ["Projects"],
                    "summary": "Get queue stats",
                    "description": "Returns execution queue statistics for the project.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Queue stats"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/inventory/hosts": {
                "get": {
                    "tags": ["Hosts"],
                    "summary": "List hosts",
                    "description": "Returns all hosts for a project.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "List of hosts"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                    },
                },
            },
            "/api/inventory/groups": {
                "get": {
                    "tags": ["Groups"],
                    "summary": "List groups",
                    "description": "Returns all inventory groups for a project.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "List of groups"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                    },
                },
                "post": {
                    "tags": ["Groups"],
                    "summary": "Create group",
                    "description": "Creates a new inventory group.",
                    "parameters": [],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "name": {"type": "string"},
                                    },
                                    "required": ["project_id", "name"],
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Created group"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                    },
                },
            },
            "/api/inventory/groups/{group_name}": {
                "delete": {
                    "tags": ["Groups"],
                    "summary": "Delete group",
                    "description": "**Destructive.** Removes the group from inventory. Hosts in the group are not deleted.",
                    "parameters": [
                        {"name": "group_name", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Group name"},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Project ID"},
                    ],
                    "responses": {"200": {"description": "Group deleted"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/inventory/groups/{group_name}/hosts": {
                "put": {
                    "tags": ["Groups"],
                    "summary": "Update group hosts",
                    "description": "Replaces hosts in the group. Use project_id in body.",
                    "parameters": [{"name": "group_name", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "hosts": {"type": "array", "items": {"type": "string"}}},
                                    "required": ["project_id", "hosts"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Hosts updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/inventory/group-vars/{group_name}": {
                "get": {
                    "tags": ["Groups"],
                    "summary": "Get group vars content",
                    "description": "Returns group_vars file content for a group.",
                    "parameters": [
                        {"name": "group_name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Group vars content"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/inventory/hosts/{host_name}/groups": {
                "put": {
                    "tags": ["Groups"],
                    "summary": "Update host groups",
                    "description": "Replaces groups for a host. Use project_id in body.",
                    "parameters": [{"name": "host_name", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "groups": {"type": "array", "items": {"type": "string"}}},
                                    "required": ["project_id", "groups"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Groups updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/groups/{group_name}/connection-secret": {
                "put": {
                    "tags": ["Groups"],
                    "summary": "Set group connection secret",
                    "description": "Assigns a connection secret to all hosts in the group.",
                    "parameters": [{"name": "group_name", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "secret_name": {"type": "string"}},
                                    "required": ["project_id"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Connection secret set"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/inventory/list": {
                "get": {
                    "tags": ["Inventory"],
                    "summary": "List inventory files",
                    "description": "Returns inventory file structure for a project.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "Inventory files"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                    },
                },
            },
            "/api/group_vars/list": {
                "get": {
                    "tags": ["Variables"],
                    "summary": "List group vars",
                    "description": "Returns group variables for a project.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "Group variables"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                    },
                },
            },
            "/api/group_vars/get": {
                "get": {
                    "tags": ["Variables"],
                    "summary": "Get group vars file content",
                    "description": "Returns content of a group_vars file. Supports vault-encrypted files.",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "file", "in": "query", "schema": {"type": "string", "default": "all.yml"}},
                        {"name": "vault_id", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "File content"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/group_vars/save": {
                "post": {
                    "tags": ["Variables"],
                    "summary": "Save group vars file",
                    "description": "Saves group_vars file. Validates YAML. Supports vault encryption.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "file": {"type": "string", "default": "all.yml"},
                                        "content": {"type": "string"},
                                        "inventory_file": {"type": "string"},
                                        "vaultId": {"type": "string"},
                                    },
                                    "required": ["content"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "File saved"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/group_vars/update_var": {
                "post": {
                    "tags": ["Variables"],
                    "summary": "Update single group var",
                    "description": "Merges or removes a single variable in group_vars file.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "file": {"type": "string", "default": "all.yml"},
                                        "var_name": {"type": "string"},
                                        "var_value": {},
                                        "inventory_file": {"type": "string"},
                                    },
                                    "required": ["var_name"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Variable updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/group_vars/download": {
                "get": {
                    "tags": ["Variables"],
                    "summary": "Download group vars file",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "file", "in": "query", "schema": {"type": "string", "default": "all.yml"}},
                    ],
                    "responses": {"200": {"description": "YAML file"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/group_vars/delete": {
                "post": {
                    "tags": ["Variables"],
                    "summary": "Delete group vars file",
                    "description": "**Destructive.** Deletes a group_vars file.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "file": {"type": "string"}},
                                    "required": ["file"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "File deleted"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/host_vars/list": {
                "get": {
                    "tags": ["Variables"],
                    "summary": "List host vars",
                    "description": "Returns host variables for a project.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "Host variables"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                    },
                },
            },
            "/api/host_vars/get": {
                "get": {
                    "tags": ["Variables"],
                    "summary": "Get host vars file content",
                    "description": "Returns content of a host_vars file. Supports vault-encrypted files.",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "file", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "inventory_file", "in": "query", "schema": {"type": "string"}},
                        {"name": "vault_id", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "File content"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/host_vars/save": {
                "post": {
                    "tags": ["Variables"],
                    "summary": "Save host vars file",
                    "description": "Saves host_vars file. Validates YAML. Supports vault encryption.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "file": {"type": "string"},
                                        "content": {"type": "string"},
                                        "inventory_file": {"type": "string"},
                                        "vaultId": {"type": "string"},
                                    },
                                    "required": ["file", "content"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "File saved"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/host_vars/update_var": {
                "post": {
                    "tags": ["Variables"],
                    "summary": "Update single host var",
                    "description": "Merges or removes a single variable in host_vars file.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "file": {"type": "string"},
                                        "var_name": {"type": "string"},
                                        "var_value": {},
                                        "inventory_file": {"type": "string"},
                                    },
                                    "required": ["var_name"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Variable updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/host_vars/download": {
                "get": {
                    "tags": ["Variables"],
                    "summary": "Download host vars file",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "file", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "YAML file"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/host_vars/delete": {
                "post": {
                    "tags": ["Variables"],
                    "summary": "Delete host vars file",
                    "description": "**Destructive.** Deletes a host_vars file.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "file": {"type": "string"}},
                                    "required": ["file"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "File deleted"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/host_vars/create": {
                "post": {
                    "tags": ["Variables"],
                    "summary": "Create host vars file",
                    "description": "Creates a new empty host_vars file. Must end with .yml or .yaml.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "file": {"type": "string"}},
                                    "required": ["file"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "File created"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/projects/{project_id}/playbooks": {
                "get": {
                    "tags": ["Playbooks"],
                    "summary": "List playbooks",
                    "description": "Returns all playbooks for a project.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "List of playbooks"},
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
                "post": {
                    "tags": ["Playbooks"],
                    "summary": "Create playbook",
                    "description": "Creates a new playbook in the project.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "content": {"type": "string"},
                                    },
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Created playbook"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
            },
            "/api/projects/{project_id}/playbooks/{playbook_id}": {
                "get": {
                    "tags": ["Playbooks"],
                    "summary": "Get playbook",
                    "description": "Returns a single playbook by ID.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {"description": "Playbook content"},
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
                "put": {
                    "tags": ["Playbooks"],
                    "summary": "Update playbook",
                    "description": "**Destructive.** Overwrites playbook content. Previous content is replaced.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "content": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Updated playbook"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
                "delete": {
                    "tags": ["Playbooks"],
                    "summary": "Delete playbook",
                    "description": "**Destructive.** Permanently deletes the playbook. This action cannot be undone.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {"description": "Playbook deleted"},
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
            },
            "/api/projects/{project_id}/playbooks/upload": {
                "post": {
                    "tags": ["Playbooks"],
                    "summary": "Upload playbook",
                    "description": "Upload YAML playbook file (multipart/form-data). Validates and parses structure.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}, "name": {"type": "string"}},
                                    "required": ["file"],
                                }
                            }
                        }
                    },
                    "responses": {"201": {"description": "Playbook created"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"], "409": {"description": "Playbook name conflict"}},
                },
            },
            "/api/projects/{project_id}/playbooks/reorder": {
                "post": {
                    "tags": ["Playbooks"],
                    "summary": "Reorder playbooks",
                    "description": "Changes playbook display order.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"playbook_orders": {"type": "array", "items": {"type": "object"}}},
                                    "required": ["playbook_orders"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Order updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/playbooks/{playbook_id}/clone": {
                "post": {
                    "tags": ["Playbooks"],
                    "summary": "Clone playbook",
                    "description": "Creates a copy of the playbook with a new name.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {"201": {"description": "Playbook cloned"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"], "409": {"description": "Name conflict"}},
                },
            },
            "/api/projects/{project_id}/playbooks/{playbook_id}/download": {
                "get": {
                    "tags": ["Playbooks"],
                    "summary": "Download playbook",
                    "description": "Returns playbook as YAML file.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "YAML file"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/playbooks/{playbook_id}/validate": {
                "post": {
                    "tags": ["Playbooks"],
                    "summary": "Validate playbook",
                    "description": "Validates playbook against inventory and roles.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "playbook": {"type": "object"},
                                        "inventory_groups": {"type": "object"},
                                        "available_roles": {"type": "array", "items": {"type": "string"}},
                                    },
                                    "required": ["playbook"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Validation result"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/playbooks/{playbook_id}/preview": {
                "post": {
                    "tags": ["Playbooks"],
                    "summary": "Preview playbook YAML",
                    "description": "Generates YAML preview from playbook model.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"playbook": {"type": "object"}},
                                    "required": ["playbook"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "YAML preview"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/playbooks/{playbook_id}/schedule": {
                "get": {
                    "tags": ["Playbooks"],
                    "summary": "Get playbook schedule",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Schedule config"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Playbooks"],
                    "summary": "Update playbook schedule",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Schedule updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Playbooks"],
                    "summary": "Delete playbook schedule",
                    "description": "**Destructive.** Removes scheduled runs.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Schedule deleted"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/playbooks/{playbook_id}/schedule/next-run": {
                "get": {
                    "tags": ["Playbooks"],
                    "summary": "Get next scheduled run",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Next run time"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/playbooks/{playbook_id}/runs": {
                "get": {
                    "tags": ["Playbooks"],
                    "summary": "List playbook runs",
                    "description": "Returns execution history for this playbook.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "List of runs"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/playbooks/{playbook_id}/run": {
                "post": {
                    "tags": ["Runs"],
                    "summary": "Run playbook",
                    "description": "Starts a playbook execution. Returns execution ID for tracking.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "limit": {"type": "string", "description": "Host limit (e.g. 'all', 'group1')"},
                                        "extra_vars": {"type": "object", "description": "Extra variables"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Execution started"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
            },
            "/api/executions": {
                "get": {
                    "tags": ["Executions"],
                    "summary": "List executions",
                    "description": "Returns execution history. Can be filtered by project.",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                        {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0}},
                        {"name": "q", "in": "query", "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "List of executions"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
                "post": {
                    "tags": ["Executions"],
                    "summary": "Create execution record",
                    "description": "Creates execution record. Requires save_history enabled.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}},
                                    "required": ["project_id"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Execution ID"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "500": {"description": "Failed to create"}},
                },
            },
            "/api/executions/{execution_id}": {
                "get": {
                    "tags": ["Executions"],
                    "summary": "Get execution",
                    "description": "Returns execution details and status.",
                    "parameters": [
                        {"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Execution details"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "patch": {
                    "tags": ["Executions"],
                    "summary": "Update execution",
                    "description": "Updates execution record fields.",
                    "parameters": [
                        {"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Updated"}, "401": RESPONSES["401"], "404": RESPONSES["404"], "500": {"description": "Update failed"}},
                },
            },
            "/api/projects/{project_id}/executions/{execution_id}/cancel": {
                "post": {
                    "tags": ["Executions"],
                    "summary": "Cancel execution",
                    "description": "Cancels QUEUED execution. Transition: QUEUED → CANCELED.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Canceled"}, "401": RESPONSES["401"], "404": RESPONSES["404"], "409": {"description": "Invalid status"}},
                },
            },
            "/api/projects/{project_id}/executions/{execution_id}/stop": {
                "post": {
                    "tags": ["Executions"],
                    "summary": "Stop execution",
                    "description": "Requests stop for RUNNING execution. Transition: RUNNING → CANCELING.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Stop requested"}, "401": RESPONSES["401"], "404": RESPONSES["404"], "409": {"description": "Invalid status"}},
                },
            },
            "/api/executions/{execution_id}/logs": {
                "get": {
                    "tags": ["Executions"],
                    "summary": "Get execution logs",
                    "description": "Returns structured logs with optional filters.",
                    "parameters": [
                        {"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "inventory", "in": "query", "schema": {"type": "string"}},
                        {"name": "host", "in": "query", "schema": {"type": "string"}},
                        {"name": "playbook", "in": "query", "schema": {"type": "string"}},
                        {"name": "cursor", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Log lines"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
                "post": {
                    "tags": ["Executions"],
                    "summary": "Append execution log",
                    "description": "Appends log content (internal/worker use).",
                    "parameters": [
                        {"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "schema": {"type": "string"}},
                    ],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Log appended"}, "401": RESPONSES["401"]},
                },
            },
            "/api/executions/{execution_id}/log": {
                "get": {
                    "tags": ["Executions"],
                    "summary": "Get execution log (raw)",
                    "description": "Returns raw log content.",
                    "parameters": [
                        {"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Raw log"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/executions/{execution_id}/log/stream": {
                "get": {
                    "tags": ["Executions"],
                    "summary": "Stream execution log",
                    "description": "Server-sent events stream of log content.",
                    "parameters": [
                        {"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "SSE stream"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/executions/clear": {
                "post": {
                    "tags": ["Executions"],
                    "summary": "Clear execution history",
                    "description": "**Destructive.** Clears old executions per retention policy.",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "History cleared"}, "401": RESPONSES["401"]},
                },
            },
            "/api/executions/stats": {
                "get": {
                    "tags": ["Executions"],
                    "summary": "Get execution stats",
                    "description": "Returns execution statistics.",
                    "responses": {"200": {"description": "Stats"}, "401": RESPONSES["401"]},
                },
            },
            "/api/execution_settings": {
                "get": {
                    "tags": ["Executions"],
                    "summary": "Get execution settings",
                    "description": "Returns execution history settings (no auth for read).",
                    "responses": {"200": {"description": "Settings and stats"}},
                },
                "post": {
                    "tags": ["Executions"],
                    "summary": "Save execution settings",
                    "description": "Updates execution history settings (save_history, log_level, retention, etc.).",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "save_history": {"type": "boolean"},
                                        "log_level": {"type": "string"},
                                        "max_log_size_mb": {"type": "integer"},
                                        "max_upload_size_mb": {"type": "integer"},
                                        "host_status_ttl_seconds": {"type": "integer"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Settings saved"}, "401": RESPONSES["401"], "500": {"description": "Save failed"}},
                },
            },
            "/api/secrets": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "List project secrets",
                    "description": "Returns secrets for a project. Secrets are project-scoped credentials.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "List of secrets"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                    },
                },
                "post": {
                    "tags": ["Secrets"],
                    "summary": "Create project secret",
                    "description": "Creates a new secret. Types: ssh_key, login_password.",
                    "parameters": [],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "name": {"type": "string"},
                                        "type": {"type": "string", "enum": ["ssh_key", "login_password"]},
                                        "username": {"type": "string"},
                                        "description": {"type": "string"},
                                        "privateKey": {"type": "string"},
                                        "passphrase": {"type": "string"},
                                        "password": {"type": "string"},
                                    },
                                    "required": ["name", "type"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Secret created"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "409": {"description": "Secret already exists"}},
                },
            },
            "/api/secrets/{secret_name}": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "Get secret",
                    "description": "Returns secret metadata (no sensitive values).",
                    "parameters": [
                        {"name": "secret_name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Secret metadata"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Secrets"],
                    "summary": "Update secret",
                    "description": "Updates secret metadata and optionally rotates material.",
                    "parameters": [
                        {"name": "secret_name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "username": {"type": "string"},
                                        "description": {"type": "string"},
                                        "privateKey": {"type": "string"},
                                        "passphrase": {"type": "string"},
                                        "password": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Secret updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Secrets"],
                    "summary": "Delete secret",
                    "description": "**Destructive.** Permanently deletes the secret.",
                    "parameters": [
                        {"name": "secret_name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Secret deleted"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/secrets/meta": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "List secrets meta",
                    "description": "Returns secret names and types for dropdowns (no sensitive data).",
                    "parameters": [{"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Secrets meta"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/global/secrets": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "List global secrets",
                    "description": "Returns global (platform-level) secrets.",
                    "responses": {"200": {"description": "List of global secrets"}, "401": RESPONSES["401"]},
                },
                "post": {
                    "tags": ["Secrets"],
                    "summary": "Create global secret",
                    "description": "Creates a new global secret.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "type": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Secret created"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/global/secrets/{secret_id}": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "Get global secret",
                    "parameters": [{"name": "secret_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Secret details"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Secrets"],
                    "summary": "Update global secret",
                    "parameters": [{"name": "secret_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Secret updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Secrets"],
                    "summary": "Delete global secret",
                    "description": "**Destructive.** Deletes a global secret.",
                    "parameters": [{"name": "secret_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Secret deleted"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/global/secrets/options": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "Get global secrets options",
                    "description": "Returns options for global secrets configuration.",
                    "responses": {"200": {"description": "Options"}, "401": RESPONSES["401"]},
                },
            },
            "/api/global/secrets/permissions": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "Get global secrets permissions",
                    "responses": {"200": {"description": "Permissions"}, "401": RESPONSES["401"]},
                },
            },
            "/api/global/secrets/encryption-key": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "Get encryption key status",
                    "description": "Returns encryption key status for global secrets.",
                    "responses": {"200": {"description": "Key status"}, "401": RESPONSES["401"]},
                },
                "post": {
                    "tags": ["Secrets"],
                    "summary": "Set encryption key",
                    "description": "Sets or uploads encryption key for global secrets.",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"key": {"type": "string"}}}}}},
                    "responses": {"200": {"description": "Key set"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/global/secrets/encryption-key/create": {
                "post": {
                    "tags": ["Secrets"],
                    "summary": "Create encryption key",
                    "description": "Generates a new encryption key for global secrets.",
                    "responses": {"200": {"description": "Key created"}, "401": RESPONSES["401"]},
                },
            },
            "/api/global/secrets/encryption-key/download": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "Download encryption key",
                    "description": "Downloads the encryption key file. Use with caution.",
                    "responses": {"200": {"description": "Key file"}, "401": RESPONSES["401"]},
                },
            },
            "/api/hosts/{host_name}/connection-secret": {
                "put": {
                    "tags": ["Secrets"],
                    "summary": "Set host connection secret",
                    "description": "Assigns a connection secret to a host (host_vars).",
                    "parameters": [{"name": "host_name", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "secret_name": {"type": "string"}},
                                    "required": ["project_id"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Connection secret set"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/hosts/{host_name}/connection-secret/resolve": {
                "get": {
                    "tags": ["Secrets"],
                    "summary": "Resolve host connection secret",
                    "description": "Resolves connection secret for a host (checks host_vars, then group_vars).",
                    "parameters": [
                        {"name": "host_name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Resolved secret info"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/projects/{project_id}/vaults": {
                "get": {
                    "tags": ["Vaults"],
                    "summary": "List vaults",
                    "description": "Returns vault files for a project.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "List of vaults"},
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
                "post": {
                    "tags": ["Vaults"],
                    "summary": "Create vault",
                    "description": "Creates a vault and binds it to a key.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "keyId": {"type": "string"},
                                        "vaultId": {"type": "string"},
                                    },
                                    "required": ["name", "keyId"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Vault created"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/vaults/{vault_id}": {
                "get": {
                    "tags": ["Vaults"],
                    "summary": "Get vault",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "vault_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Vault details"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Vaults"],
                    "summary": "Update vault",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "vault_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "keyId": {"type": "string"}, "vaultId": {"type": "string"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Vault updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Vaults"],
                    "summary": "Delete vault",
                    "description": "**Destructive.** Removes vault binding. Does not delete encrypted files.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "vault_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Vault deleted"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/vaults/{vault_id}/encrypt": {
                "post": {
                    "tags": ["Vaults"],
                    "summary": "Encrypt content",
                    "description": "Encrypts content using ansible-vault with the vault's key.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "vault_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"content": {"type": "string"}},
                                    "required": ["content"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Encrypted content"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/vaults/{vault_id}/decrypt": {
                "post": {
                    "tags": ["Vaults"],
                    "summary": "Decrypt content",
                    "description": "Decrypts ansible-vault encrypted content.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "vault_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"content": {"type": "string"}},
                                    "required": ["content"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Decrypted content"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/vault-files/get": {
                "get": {
                    "tags": ["Vaults"],
                    "summary": "Get vault file content",
                    "description": "Returns content of a vault file (path within group_vars or host_vars).",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "path", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Path within group_vars or host_vars"},
                        {"name": "vault_id", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "File content"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/vault-files/save": {
                "post": {
                    "tags": ["Vaults"],
                    "summary": "Save vault file",
                    "description": "Saves content to a vault file. Supports encryption.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"},
                                        "content": {"type": "string"},
                                        "vaultId": {"type": "string"},
                                    },
                                    "required": ["path", "content"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "File saved"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/vault-keys": {
                "get": {
                    "tags": ["Vaults"],
                    "summary": "List vault keys",
                    "description": "Returns encryption keys for vault files.",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Project ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "List of vault keys"},
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
                "post": {
                    "tags": ["Vaults"],
                    "summary": "Create vault key",
                    "description": "Creates a new vault encryption key.",
                    "parameters": [{"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "password": {"type": "string"}, "type": {"type": "string", "default": "vault_password"}},
                                    "required": ["name", "password"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Key created"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/projects/{project_id}/vault-keys/{key_id}": {
                "get": {
                    "tags": ["Vaults"],
                    "summary": "Get vault key",
                    "description": "Returns vault key metadata (password never returned).",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "key_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Key metadata"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Vaults"],
                    "summary": "Update vault key",
                    "description": "Updates key name, type, or password.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "key_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "type": {"type": "string"}, "password": {"type": "string"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Key updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Vaults"],
                    "summary": "Delete vault key",
                    "description": "**Destructive.** Deletes key. Fails if key is used by vaults.",
                    "parameters": [
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "key_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Key deleted"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/backups/list": {
                "get": {
                    "tags": ["Backups"],
                    "summary": "List backups",
                    "description": "Returns backup files for a project (group_vars, host_vars, inventory, playbooks).",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "type", "in": "query", "schema": {"type": "string", "enum": ["all", "group_vars", "host_vars", "inventory", "playbooks"], "default": "all"}},
                    ],
                    "responses": {"200": {"description": "List of backups"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/backups/create": {
                "post": {
                    "tags": ["Backups"],
                    "summary": "Create project backup",
                    "description": "Creates full project archive (tar.gz).",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "reason": {"type": "string", "default": "auto"}},
                                    "required": ["project_id"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Backup created"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/backups/download": {
                "get": {
                    "tags": ["Backups"],
                    "summary": "Download backup file",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "path", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Backup path"},
                    ],
                    "responses": {"200": {"description": "File download"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/backups/restore": {
                "post": {
                    "tags": ["Backups"],
                    "summary": "Restore from backup",
                    "description": "Restores a file from backup.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "path": {"type": "string"}},
                                    "required": ["path"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Restored"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/backups/archives/list": {
                "get": {
                    "tags": ["Backups"],
                    "summary": "List project archives",
                    "description": "Returns full project archives (tar.gz) for restore.",
                    "parameters": [{"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "List of archives"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/backups/archives/restore": {
                "post": {
                    "tags": ["Backups"],
                    "summary": "Restore from archive",
                    "description": "**Destructive.** Restores full project from tar.gz archive.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "path": {"type": "string"}, "archive": {"type": "string"}},
                                    "required": ["path"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Project restored"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/backups/archives/download": {
                "get": {
                    "tags": ["Backups"],
                    "summary": "Download project archive",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "path", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Archive download"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/backup-settings": {
                "get": {
                    "tags": ["Backups"],
                    "summary": "Get backup settings",
                    "parameters": [],
                    "responses": {"200": {"description": "Backup settings"}, "401": RESPONSES["401"]},
                },
                "put": {
                    "tags": ["Backups"],
                    "summary": "Update backup settings",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Settings updated"}, "401": RESPONSES["401"], "500": {"description": "Update failed"}},
                },
            },
            "/api/api-tokens": {
                "get": {
                    "tags": ["API Tokens"],
                    "summary": "List API tokens",
                    "description": "Returns API tokens for the current user. Token values are never returned.",
                    "responses": {
                        "200": {"description": "List of tokens"},
                        "401": RESPONSES["401"],
                    },
                },
                "post": {
                    "tags": ["API Tokens"],
                    "summary": "Create API token",
                    "description": "Creates a new API token. The token value is returned only once and cannot be retrieved later.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "scope": {"type": "string", "enum": ["global", "project"]},
                                        "projectId": {"type": "string"},
                                        "expiresDays": {"type": "integer"},
                                    },
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Token created (value shown once)"},
                        "400": RESPONSES["400"],
                        "401": RESPONSES["401"],
                    },
                },
            },
            "/api/api-tokens/{token_id}/revoke": {
                "post": {
                    "tags": ["API Tokens"],
                    "summary": "Revoke token",
                    "description": "**Destructive.** Revokes the token immediately. It will stop working and cannot be restored.",
                    "parameters": [
                        {
                            "name": "token_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Token ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "Token revoked"},
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
            },
            "/api/api-tokens/{token_id}/rotate": {
                "post": {
                    "tags": ["API Tokens"],
                    "summary": "Regenerate token",
                    "description": "**Destructive.** Revokes the old token and creates a new one. The old token stops working immediately. The new token value is returned only once.",
                    "parameters": [
                        {
                            "name": "token_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Token ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "New token (value shown once)"},
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
            },
            "/api/api-tokens/{token_id}": {
                "delete": {
                    "tags": ["API Tokens"],
                    "summary": "Delete token",
                    "description": "**Destructive.** Permanently deletes the token record. This cannot be undone.",
                    "parameters": [
                        {
                            "name": "token_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Token ID",
                        }
                    ],
                    "responses": {
                        "200": {"description": "Token deleted"},
                        "401": RESPONSES["401"],
                        "404": RESPONSES["404"],
                    },
                },
            },
            "/api/ansible_config/list": {
                "get": {
                    "tags": ["Ansible"],
                    "summary": "List Ansible config files",
                    "description": "Returns .cfg files from project ansible-config directory.",
                    "parameters": [{"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Config files list"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/ansible_config/get": {
                "get": {
                    "tags": ["Ansible"],
                    "summary": "Get Ansible config content",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "file", "in": "query", "schema": {"type": "string", "default": "ansible.cfg"}},
                    ],
                    "responses": {"200": {"description": "Config content"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/ansible_config/save": {
                "post": {
                    "tags": ["Ansible"],
                    "summary": "Save Ansible config",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "file": {"type": "string"}, "content": {"type": "string"}},
                                    "required": ["content"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Config saved"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/ansible_config/download": {
                "get": {
                    "tags": ["Ansible"],
                    "summary": "Download Ansible config file",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "file", "in": "query", "schema": {"type": "string", "default": "ansible.cfg"}},
                    ],
                    "responses": {"200": {"description": "Config file"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/ansible_config/delete": {
                "post": {
                    "tags": ["Ansible"],
                    "summary": "Delete Ansible config file",
                    "description": "**Destructive.** Deletes config file from project.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "file": {"type": "string"}},
                                    "required": ["file"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "File deleted"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/ansible_config/select": {
                "post": {
                    "tags": ["Ansible"],
                    "summary": "Select Ansible config",
                    "description": "Sets the active config file for the project.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "file": {"type": "string"}},
                                    "required": ["file"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Config selected"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/run_ansible": {
                "post": {
                    "tags": ["Ansible"],
                    "summary": "Run Ansible playbook",
                    "description": "Starts Ansible execution. Requires hosts, roles, ansible_config.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "hosts": {"type": "array", "items": {"type": "string"}},
                                        "roles": {"type": "array", "items": {"type": "string"}},
                                        "inventory_files": {"type": "array", "items": {"type": "string"}},
                                        "ansible_config": {"type": "string"},
                                    },
                                    "required": ["hosts", "roles", "ansible_config"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Execution started"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/ansible_status": {
                "get": {
                    "tags": ["Ansible"],
                    "summary": "Get Ansible status",
                    "description": "Returns current Ansible execution status.",
                    "responses": {"200": {"description": "Status (running/idle)"}, "401": RESPONSES["401"]},
                },
            },
            "/api/stop_ansible": {
                "post": {
                    "tags": ["Ansible"],
                    "summary": "Stop Ansible",
                    "description": "Requests stop of running Ansible process.",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Stop requested"}, "401": RESPONSES["401"]},
                },
            },
            "/api/hosts": {
                "get": {
                    "tags": ["Hosts"],
                    "summary": "List hosts (legacy)",
                    "description": "Returns host names from inventory.",
                    "responses": {"200": {"description": "Host names"}, "401": RESPONSES["401"]},
                },
            },
            "/api/check_host": {
                "post": {
                    "tags": ["Hosts"],
                    "summary": "Check host availability",
                    "description": "Ping host via Ansible.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "host": {"type": "string"},
                                        "ansible_config": {"type": "string"},
                                        "inventory_files": {"type": "array", "items": {"type": "string"}},
                                        "connection_secret": {"type": "string"},
                                    },
                                    "required": ["host", "ansible_config"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Host check result"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/check_hosts": {
                "post": {
                    "tags": ["Hosts"],
                    "summary": "Check multiple hosts",
                    "description": "Batch host availability check.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "hosts": {"type": "array", "items": {"type": "string"}},
                                        "ansible_config": {"type": "string"},
                                        "inventory_files": {"type": "array", "items": {"type": "string"}},
                                        "connection_secrets": {"type": "object"},
                                    },
                                    "required": ["hosts"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Check results"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/hosts/{host_name}/facts": {
                "post": {
                    "tags": ["Hosts"],
                    "summary": "Gather host facts",
                    "description": "Runs setup module to gather Ansible facts.",
                    "parameters": [{"name": "host_name", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "ansible_config": {"type": "string"}, "inventory_files": {"type": "array"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Facts"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/roles/storage": {
                "get": {
                    "tags": ["Ansible Roles"],
                    "summary": "Get roles storage tree",
                    "description": "Returns tree of roles in project repo/roles.",
                    "parameters": [{"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Roles tree"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "500": {"description": "Resolve error"}},
                },
            },
            "/api/roles/config": {
                "get": {
                    "tags": ["Ansible Roles"],
                    "summary": "Get roles config",
                    "parameters": [{"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Roles config"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
                "post": {
                    "tags": ["Ansible Roles"],
                    "summary": "Save roles config",
                    "parameters": [{"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"config": {"type": "object"}},
                                    "required": ["config"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Config saved"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "500": {"description": "Save failed"}},
                },
            },
            "/api/roles/{pack_id}/{role_name}": {
                "get": {
                    "tags": ["Ansible Roles"],
                    "summary": "Get role details",
                    "description": "Returns role details (defaults, variables). For pack/role or top-level role.",
                    "parameters": [
                        {"name": "pack_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "role_name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Role details"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/roles/files/{role_path}": {
                "get": {
                    "tags": ["Ansible Roles"],
                    "summary": "List role files",
                    "parameters": [
                        {"name": "role_path", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Files list"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/roles/file/{pack_id}/{role_name}/{file_path}": {
                "get": {
                    "tags": ["Ansible Roles"],
                    "summary": "Get role file content",
                    "parameters": [
                        {"name": "pack_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "role_name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "file_path", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "File content"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Ansible Roles"],
                    "summary": "Update role file",
                    "parameters": [
                        {"name": "pack_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "role_name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "file_path", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"content": {"type": "string"}}}}}},
                    "responses": {"200": {"description": "File updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/admin/workers": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "List workers",
                    "description": "Returns all workers. Admin only.",
                    "responses": {"200": {"description": "Workers list"}, "401": RESPONSES["401"], "403": RESPONSES["403"]},
                },
                "post": {
                    "tags": ["Admin"],
                    "summary": "Create worker",
                    "description": "Creates worker. Token returned only once! Admin only.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "capabilities": {"type": "object"}, "tags": {"type": "array", "items": {"type": "string"}}},
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Worker created (token shown once)"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "403": RESPONSES["403"]},
                },
            },
            "/api/admin/workers/{worker_id}": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "Get worker",
                    "parameters": [{"name": "worker_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Worker details"}, "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
                "patch": {
                    "tags": ["Admin"],
                    "summary": "Update worker",
                    "parameters": [{"name": "worker_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "tags": {"type": "array"}, "tagColors": {"type": "object"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Worker updated"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Admin"],
                    "summary": "Delete worker",
                    "description": "**Destructive.** Permanently deletes worker.",
                    "parameters": [{"name": "worker_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Worker deleted"}, "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/admin/workers/{worker_id}/enable": {
                "post": {
                    "tags": ["Admin"],
                    "summary": "Enable worker",
                    "parameters": [{"name": "worker_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Worker enabled"}, "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/admin/workers/{worker_id}/disable": {
                "post": {
                    "tags": ["Admin"],
                    "summary": "Disable worker",
                    "parameters": [{"name": "worker_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Worker disabled"}, "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/admin/workers/{worker_id}/request-info": {
                "post": {
                    "tags": ["Admin"],
                    "summary": "Request worker info",
                    "description": "Requests worker to send system info.",
                    "parameters": [{"name": "worker_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Request sent"}, "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/admin/workers/{worker_id}/rotate-token": {
                "post": {
                    "tags": ["Admin"],
                    "summary": "Rotate worker token",
                    "description": "**Destructive.** Generates new token. Old token stops working. New token returned only once!",
                    "parameters": [{"name": "worker_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "New token (shown once)"}, "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/admin/workers/{worker_id}/runs": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "Get worker runs",
                    "description": "Returns execution history for this worker.",
                    "parameters": [{"name": "worker_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Runs list"}, "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/worker/register": {
                "post": {
                    "tags": ["Worker"],
                    "summary": "Register worker",
                    "description": "Self-registration. Prefer creating via Admin API in production.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "capabilities": {"type": "object"}, "tags": {"type": "array", "items": {"type": "string"}}},
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Worker created (token returned)"}, "400": RESPONSES["400"], "500": {"description": "Registration failed"}},
                },
            },
            "/api/worker/claim": {
                "post": {
                    "tags": ["Worker"],
                    "summary": "Claim execution",
                    "description": "Claims next QUEUED execution. Requires worker Bearer token.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"projectId": {"type": "string"}, "maxConcurrency": {"type": "integer"}, "tags": {"type": "array"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Execution claimed"}, "204": {"description": "No tasks available"}, "401": RESPONSES["401"], "429": {"description": "Rate limit"}},
                },
            },
            "/api/worker/heartbeat": {
                "post": {
                    "tags": ["Worker"],
                    "summary": "Worker heartbeat",
                    "description": "Updates worker lastSeenAt. Requires worker Bearer token.",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Heartbeat received"}, "401": RESPONSES["401"]},
                },
            },
            "/api/worker/system-info": {
                "post": {
                    "tags": ["Worker"],
                    "summary": "Report system info",
                    "description": "Worker reports system info (e.g. Mitogen version). Requires worker Bearer token.",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Info received"}, "401": RESPONSES["401"]},
                },
            },
            "/api/worker/executions/{execution_id}/log": {
                "post": {
                    "tags": ["Worker"],
                    "summary": "Append execution log",
                    "description": "Worker appends log to claimed execution. Requires worker Bearer token.",
                    "parameters": [{"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Log appended"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/worker/executions/{execution_id}/finish": {
                "post": {
                    "tags": ["Worker"],
                    "summary": "Finish execution",
                    "description": "Worker reports execution completion. Requires worker Bearer token.",
                    "parameters": [{"name": "execution_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"status": {"type": "string"}, "result": {"type": "object"}},
                                    "required": ["status"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Execution finished"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "403": RESPONSES["403"], "404": RESPONSES["404"]},
                },
            },
            "/api/all_data": {
                "get": {
                    "tags": ["Misc"],
                    "summary": "Get all project data",
                    "description": "Returns group_vars, host_vars, hosts, groups for a project.",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "inventory_files", "in": "query", "schema": {"type": "array", "items": {"type": "string"}}},
                    ],
                    "responses": {"200": {"description": "Project data"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/dashboard_stats": {
                "get": {
                    "tags": ["Misc"],
                    "summary": "Get dashboard stats",
                    "description": "Returns dashboard statistics.",
                    "parameters": [{"name": "project_id", "in": "query", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Stats"}, "401": RESPONSES["401"]},
                },
            },
            "/api/preview": {
                "post": {
                    "tags": ["Misc"],
                    "summary": "Preview YAML",
                    "description": "Generates YAML preview from group_vars and host_vars.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"group_vars": {"type": "object"}, "host_vars": {"type": "object"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "YAML preview"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/yaml/parse": {
                "post": {
                    "tags": ["Misc"],
                    "summary": "Parse YAML",
                    "description": "Parses YAML content to JSON.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"yaml": {"type": "string"}},
                                    "required": ["yaml"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Parsed data"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/yaml/validate": {
                "post": {
                    "tags": ["Misc"],
                    "summary": "Validate YAML",
                    "description": "Validates YAML syntax. Returns line/column on error.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"content": {"type": "string"}, "context": {"type": "string"}, "filename": {"type": "string"}},
                                    "required": ["content"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Validation result"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/save": {
                "post": {
                    "tags": ["Misc"],
                    "summary": "Save all variables",
                    "description": "Saves group_vars and host_vars to project files.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "group_vars": {"type": "object"},
                                        "host_vars": {"type": "object"},
                                        "backup_settings": {"type": "object"},
                                    },
                                    "required": ["group_vars", "host_vars"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Saved"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/queue": {
                "get": {
                    "tags": ["Misc"],
                    "summary": "Get execution queue",
                    "description": "Returns QUEUED executions.",
                    "parameters": [
                        {"name": "project_id", "in": "query", "schema": {"type": "string"}},
                        {"name": "playbook_id", "in": "query", "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 100}},
                    ],
                    "responses": {"200": {"description": "Queued executions"}, "401": RESPONSES["401"]},
                },
            },
            "/api/search": {
                "post": {
                    "tags": ["Misc"],
                    "summary": "Global search",
                    "description": "Searches across projects, hosts, groups, roles, playbooks, variables, executions.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string"},
                                        "entity_types": {"type": "array", "items": {"type": "string"}},
                                        "project_ids": {"type": "array", "items": {"type": "string"}},
                                        "limit": {"type": "integer", "default": 50},
                                    },
                                    "required": ["query"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Search results"}, "401": RESPONSES["401"]},
                },
            },
            "/api/server_logs": {
                "get": {
                    "tags": ["Misc"],
                    "summary": "Get server logs",
                    "description": "Returns server log content.",
                    "parameters": [
                        {"name": "project_id", "in": "query", "schema": {"type": "string"}},
                        {"name": "lines", "in": "query", "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "Log content"}, "401": RESPONSES["401"]},
                },
            },
            "/api/frontend_logs": {
                "post": {
                    "tags": ["Misc"],
                    "summary": "Submit frontend logs",
                    "description": "Accepts frontend error logs for debugging.",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Logs received"}, "401": RESPONSES["401"]},
                },
            },
            "/api/capabilities": {
                "get": {
                    "tags": ["Misc"],
                    "summary": "Get capabilities",
                    "description": "Returns platform capabilities (e.g. Mitogen availability for workers).",
                    "responses": {"200": {"description": "Capabilities"}, "401": RESPONSES["401"]},
                },
            },
            "/api/auth/login": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Login",
                    "description": "Authenticate with username and password. Returns access_token and refresh_token.",
                    "security": [],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"username": {"type": "string"}, "password": {"type": "string"}},
                                    "required": ["username", "password"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Tokens and user info"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                }
            },
            "/api/auth/logout": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Logout",
                    "description": "Invalidate the current token.",
                    "responses": {"200": {"description": "Logged out"}, "401": RESPONSES["401"]},
                }
            },
            "/api/auth/refresh": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Refresh token",
                    "description": "Get a new access token using refresh token.",
                    "security": [],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"refresh_token": {"type": "string"}},
                                    "required": ["refresh_token"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "New access token"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                }
            },
            "/api/auth/me": {
                "get": {
                    "tags": ["Auth"],
                    "summary": "Current user",
                    "description": "Returns the authenticated user's profile, roles, and permissions.",
                    "responses": {"200": {"description": "User info"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                }
            },
            "/api/users": {
                "get": {
                    "tags": ["Users"],
                    "summary": "List users",
                    "description": "Returns all users with role details.",
                    "responses": {"200": {"description": "List of users"}, "401": RESPONSES["401"]},
                },
                "post": {
                    "tags": ["Users"],
                    "summary": "Create user",
                    "description": "Creates a new user with username, password, optional email and roles.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "username": {"type": "string"},
                                        "password": {"type": "string"},
                                        "email": {"type": "string"},
                                        "roles": {"type": "array", "items": {"type": "string"}},
                                    },
                                    "required": ["username", "password"],
                                }
                            }
                        }
                    },
                    "responses": {"201": {"description": "User created"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/users/{user_id}": {
                "get": {
                    "tags": ["Users"],
                    "summary": "Get user",
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "User details"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Users"],
                    "summary": "Update user",
                    "description": "Update username, email, roles, is_active, or password.",
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "username": {"type": "string"},
                                        "email": {"type": "string"},
                                        "roles": {"type": "array", "items": {"type": "string"}},
                                        "is_active": {"type": "boolean"},
                                        "password": {"type": "string"},
                                        "current_password": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Updated user"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Users"],
                    "summary": "Delete user",
                    "description": "**Destructive.** Permanently deletes the user. Cannot delete yourself.",
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "User deleted"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/users/{user_id}/roles": {
                "post": {
                    "tags": ["Users"],
                    "summary": "Assign roles",
                    "description": "Assign roles to a user. Replaces existing roles.",
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"roles": {"type": "array", "items": {"type": "string"}}},
                                    "required": ["roles"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Roles assigned"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/roles": {
                "get": {
                    "tags": ["Roles"],
                    "summary": "List roles",
                    "description": "Returns all roles with permission details.",
                    "responses": {"200": {"description": "List of roles"}, "401": RESPONSES["401"]},
                },
                "post": {
                    "tags": ["Roles"],
                    "summary": "Create role",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "permissions": {"type": "array", "items": {"type": "string"}},
                                    },
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {"201": {"description": "Role created"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/roles/{role_id}": {
                "get": {
                    "tags": ["Roles"],
                    "summary": "Get role",
                    "parameters": [{"name": "role_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Role details"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Roles"],
                    "summary": "Update role",
                    "parameters": [{"name": "role_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "permissions": {"type": "array", "items": {"type": "string"}},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Updated role"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Roles"],
                    "summary": "Delete role",
                    "description": "**Destructive.** Permanently deletes the role.",
                    "parameters": [{"name": "role_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Role deleted"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/permissions": {
                "get": {
                    "tags": ["Permissions"],
                    "summary": "List permissions",
                    "responses": {"200": {"description": "List of permissions"}, "401": RESPONSES["401"]},
                },
                "post": {
                    "tags": ["Permissions"],
                    "summary": "Create permission",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "resource": {"type": "string"},
                                        "action": {"type": "string"},
                                    },
                                    "required": ["name", "resource", "action"],
                                }
                            }
                        }
                    },
                    "responses": {"201": {"description": "Permission created"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/permissions/{perm_id}": {
                "get": {
                    "tags": ["Permissions"],
                    "summary": "Get permission",
                    "parameters": [{"name": "perm_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Permission details"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Permissions"],
                    "summary": "Update permission",
                    "parameters": [{"name": "perm_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "resource": {"type": "string"},
                                        "action": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Updated permission"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Permissions"],
                    "summary": "Delete permission",
                    "parameters": [{"name": "perm_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Permission deleted"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/permissions/by-resource/{resource}": {
                "get": {
                    "tags": ["Permissions"],
                    "summary": "List permissions by resource",
                    "parameters": [{"name": "resource", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Permissions for resource"}, "401": RESPONSES["401"]},
                },
            },
            "/api/inventory/get": {
                "get": {
                    "tags": ["Inventory"],
                    "summary": "Get inventory file content",
                    "description": "Returns the content of an inventory file. Supports vault-encrypted files.",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "file", "in": "query", "schema": {"type": "string", "default": "inventories/inventory.yml"}},
                        {"name": "vault_id", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "File content"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/inventory/save": {
                "post": {
                    "tags": ["Inventory"],
                    "summary": "Save inventory file",
                    "description": "Saves inventory file content. Validates YAML. Supports vault encryption.",
                    "parameters": [],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "file": {"type": "string"},
                                        "content": {"type": "string"},
                                        "env": {"type": "string"},
                                        "vaultId": {"type": "string"},
                                    },
                                    "required": ["content"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "File saved"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/inventory/create-folder": {
                "post": {
                    "tags": ["Inventory"],
                    "summary": "Create inventory folder",
                    "description": "Creates a folder in the inventories directory.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"folder_path": {"type": "string"}},
                                    "required": ["folder_path"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Folder created"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/inventory/download": {
                "get": {
                    "tags": ["Inventory"],
                    "summary": "Download inventory file",
                    "parameters": [
                        {"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "file", "in": "query", "schema": {"type": "string", "default": "inventories/inventory.yml"}},
                    ],
                    "responses": {"200": {"description": "YAML file"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/inventory/environments": {
                "get": {
                    "tags": ["Inventory"],
                    "summary": "List inventory environments",
                    "description": "Returns subfolders in inventories with inventory files.",
                    "parameters": [{"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Environments list"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/inventory/export": {
                "get": {
                    "tags": ["Inventory"],
                    "summary": "Export inventory to ZIP",
                    "description": "Exports all inventory files to a ZIP archive.",
                    "parameters": [{"name": "project_id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "ZIP file"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/inventory/add_host": {
                "post": {
                    "tags": ["Inventory"],
                    "summary": "Add host to inventory",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_id": {"type": "string"},
                                        "host_name": {"type": "string"},
                                        "inventory_file": {"type": "string"},
                                        "group_name": {"type": "string"},
                                        "host_ip": {"type": "string"},
                                        "vars_file": {"type": "string"},
                                    },
                                    "required": ["host_name"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Host added"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/inventory/import": {
                "post": {
                    "tags": ["Inventory"],
                    "summary": "Import inventory",
                    "description": "Import inventory from ZIP or single YAML file. Use multipart/form-data with 'file'.",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Import result"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/inventory/auto_update_vars_file": {
                "post": {
                    "tags": ["Inventory"],
                    "summary": "Auto-update vars_file",
                    "description": "Adds vars_file for hosts in inventory files that lack it.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"files": {"type": "array", "items": {"type": "string"}}},
                                    "required": ["files"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Updated files list"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/inventory/delete": {
                "post": {
                    "tags": ["Inventory"],
                    "summary": "Delete inventory file",
                    "description": "**Destructive.** Deletes an inventory file.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string"}},
                                    "required": ["file"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "File deleted"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/inventory/ensure-dirs": {
                "post": {
                    "tags": ["Inventory"],
                    "summary": "Ensure inventory directories",
                    "description": "Creates group_vars and host_vars directories for inventory structure.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}},
                                    "required": ["project_id"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Directories ensured"}, "400": RESPONSES["400"], "401": RESPONSES["401"]},
                },
            },
            "/api/cloud/providers": {
                "get": {
                    "tags": ["Cloud Provisioning"],
                    "summary": "List supported cloud providers",
                    "description": "Returns the catalogue of providers wired into the IaC engine. Today only `bytedc` is implemented; other entries are placeholders for the roadmap.",
                    "responses": {"200": {"description": "Providers list", "content": {"application/json": {"example": {"providers": [{"id": "bytedc", "name": "ByteDC"}]}}}}, "401": RESPONSES["401"]},
                },
            },
            "/api/cloud/bytedc/schema": {
                "get": {
                    "tags": ["Cloud Provisioning"],
                    "summary": "Get the ByteDC stack form schema",
                    "description": "Returns the wizard schema used by the Cloud Provisioning UI: sections, fields, defaults, validation rules and secret-key markers. Drives the dynamic form on the Compute / Network / Ingress / Review steps.",
                    "responses": {"200": {"description": "Schema document"}, "401": RESPONSES["401"]},
                },
            },
            "/api/cloud/stacks": {
                "get": {
                    "tags": ["Cloud Stacks"],
                    "summary": "List stacks in the current project",
                    "description": "Lists every stack saved under the active project. Use the `X-Project-Id` header (preferred) or `project_id` query param to scope the request.",
                    "parameters": [
                        {"name": "X-Project-Id", "in": "header", "schema": {"type": "string"}, "description": "Active project id (preferred)."},
                        {"name": "project_id", "in": "query", "schema": {"type": "string"}, "description": "Fallback for clients that cannot set headers."}
                    ],
                    "responses": {"200": {"description": "Stacks list", "content": {"application/json": {"example": {"stacks": [{"name": "dev", "provider": "bytedc", "env": "dev", "last_action": "apply", "last_status": "succeeded"}]}}}}, "401": RESPONSES["401"]},
                },
                "post": {
                    "tags": ["Cloud Stacks"],
                    "summary": "Create a new stack",
                    "description": "Creates a stack directory under the project, renders `terraform.tfvars` + `backend.hcl` from the supplied values, and stores any secret values (e.g. ByteDC AK/SK) in the encrypted secrets store. Stack name must match `[a-z0-9_-]{3,50}`.",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "required": ["name", "values"],
                            "properties": {
                                "name": {"type": "string", "example": "dev"},
                                "provider": {"type": "string", "default": "bytedc"},
                                "values": {"type": "object", "description": "Flat map of tfvars + secrets keyed by the field ids defined in /api/cloud/bytedc/schema."}
                            }
                        }}}
                    },
                    "responses": {"201": {"description": "Stack created"}, "400": RESPONSES["400"], "401": RESPONSES["401"], "409": {"description": "Stack already exists"}},
                },
            },
            "/api/cloud/stacks/{name}": {
                "parameters": [{"name": "name", "in": "path", "required": True, "schema": {"type": "string"}}],
                "get": {
                    "tags": ["Cloud Stacks"],
                    "summary": "Get stack details",
                    "description": "Returns the rendered `terraform.tfvars`, `backend.hcl`, file listing, metadata blob (last action/status/run id) and a `has_secrets` flag. Secret values themselves are never returned.",
                    "responses": {"200": {"description": "Stack details"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "put": {
                    "tags": ["Cloud Stacks"],
                    "summary": "Update a stack",
                    "description": "Re-renders `terraform.tfvars` from the supplied values. Secret keys present in the payload merge into the existing secrets store; omit a secret to keep its current value.",
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"values": {"type": "object"}}}}}},
                    "responses": {"200": {"description": "Updated"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
                "delete": {
                    "tags": ["Cloud Stacks"],
                    "summary": "Delete a stack",
                    "description": "Removes the stack directory and its secrets. Refuses to delete a stack with a local `terraform.tfstate` unless `force=true` is supplied — destroy the infrastructure first to avoid orphaning cloud resources.",
                    "parameters": [{"name": "force", "in": "query", "schema": {"type": "boolean"}, "description": "Bypass the local-state guard."}],
                    "responses": {"200": {"description": "Deleted"}, "401": RESPONSES["401"], "404": RESPONSES["404"], "409": {"description": "Local state present"}},
                },
            },
            "/api/cloud/stacks/{name}/actions": {
                "post": {
                    "tags": ["Cloud Stacks"],
                    "summary": "Queue a tofu run (init/plan/apply/destroy/validate/fmt)",
                    "description": "Enqueues a `TOFU_RUN` execution that any online worker can claim. Secrets are injected only into the worker process. Returns immediately with a `run_id` (= execution id); poll `/runs/{run_id}` or subscribe to the SSE stream to follow progress.",
                    "parameters": [{"name": "name", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["action"],
                        "properties": {"action": {"type": "string", "enum": ["init", "plan", "apply", "destroy", "validate", "fmt"]}}
                    }}}},
                    "responses": {"202": {"description": "Queued", "content": {"application/json": {"example": {"ok": True, "run_id": "exec_abc123", "status": "queued"}}}}, "400": RESPONSES["400"], "401": RESPONSES["401"], "404": RESPONSES["404"], "500": {"description": "Failed to queue"}},
                },
            },
            "/api/cloud/stacks/{name}/runs": {
                "get": {
                    "tags": ["Cloud Stacks"],
                    "summary": "List recent runs for a stack",
                    "description": "Returns up to 50 most-recent `TOFU_RUN` executions for the given stack, newest first, with a UI-friendly status (queued / running / succeeded / failed / canceled).",
                    "parameters": [{"name": "name", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Runs list"}, "401": RESPONSES["401"]},
                },
            },
            "/api/cloud/stacks/{name}/runs/{run_id}": {
                "get": {
                    "tags": ["Cloud Stacks"],
                    "summary": "Get a run with its full log",
                    "description": "Returns run metadata plus the full worker log file (UTF-8). While the run is still queued and no log exists yet, a placeholder line is returned.",
                    "parameters": [
                        {"name": "name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "Run details + log"}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
            "/api/cloud/stacks/{name}/runs/{run_id}/stream": {
                "get": {
                    "tags": ["Cloud Stacks"],
                    "summary": "Server-Sent Events stream of a run's log",
                    "description": "SSE endpoint that tails the worker-written log line by line. Emits `data: <line>` events and a final `event: end` event when the run reaches SUCCESS/FAILED/CANCELED. Waits up to 60 s for the worker to start writing before timing out.",
                    "parameters": [
                        {"name": "name", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "text/event-stream", "content": {"text/event-stream": {}}}, "401": RESPONSES["401"], "404": RESPONSES["404"]},
                },
            },
        },
    }
