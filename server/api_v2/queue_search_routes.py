"""v2: queue / search / capabilities / project queue-stats — Pydantic v2.

Handlers delegate to the v1 blueprint's view functions so behavior stays
byte-identical; Pydantic models drive validation and OpenAPI schemas.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from flask.views import MethodView
from flask_smorest import Blueprint
from pydantic import BaseModel, ConfigDict, Field

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from api.queue_search_routes import (
    api_capabilities as _v1_capabilities,
    api_get_queue as _v1_get_queue,
    api_global_search as _v1_global_search,
    api_project_queue_stats as _v1_project_queue_stats,
)

from ._pydantic import pyd_body, pyd_query, pyd_response


blp = Blueprint(
    "queue_search_v2",
    __name__,
    url_prefix="/api/v2",
    description="Execution queue, global search, runtime capabilities.",
)


# ---------- Shared enums ----------

EntityType = Literal[
    "projects", "hosts", "groups", "roles",
    "playbooks", "variables", "executions", "drafts",
]


# ---------- Queue ----------

class QueueQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: Optional[str] = Field(default=None, max_length=200)
    playbook_id: Optional[str] = Field(default=None, max_length=200)
    limit: int = Field(default=100, ge=1, le=1000)


class QueuedItem(BaseModel):
    """Single queued execution. Extra keys are preserved for forward-compat."""
    model_config = ConfigDict(extra="allow")
    id: Optional[str] = None
    projectId: Optional[str] = None
    playbookId: Optional[str] = None
    status: Optional[str] = None
    queuedAt: Optional[str] = None


class QueueOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: bool = True
    queued: List[QueuedItem] = Field(default_factory=list)
    count: int = Field(ge=0, default=0)


# ---------- Search ----------

class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)
    entity_types: Optional[List[EntityType]] = None
    project_ids: Optional[List[str]] = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=500)


class SearchResultItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: Optional[str] = None
    name: Optional[str] = None
    projectId: Optional[str] = None
    type: Optional[str] = None


class SearchResults(BaseModel):
    model_config = ConfigDict(extra="ignore")
    projects: List[SearchResultItem] = Field(default_factory=list)
    hosts: List[SearchResultItem] = Field(default_factory=list)
    groups: List[SearchResultItem] = Field(default_factory=list)
    roles: List[SearchResultItem] = Field(default_factory=list)
    playbooks: List[SearchResultItem] = Field(default_factory=list)
    variables: List[SearchResultItem] = Field(default_factory=list)
    executions: List[SearchResultItem] = Field(default_factory=list)
    drafts: List[SearchResultItem] = Field(default_factory=list)


class SearchOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: bool = True
    results: SearchResults
    total: int = Field(ge=0, default=0)
    query: str


# ---------- Capabilities ----------

class CapabilitiesOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    mitogen_available: bool = False


# ---------- Project queue stats ----------

class QueueStatsOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: bool = True
    projectId: str
    queuedCount: int = Field(ge=0)
    runningCount: int = Field(ge=0)
    workersOnline: int = Field(ge=0)
    activeWorkersForProject: int = Field(ge=0)


# ---------- Routes ----------

@blp.route("/queue")
class QueueView(MethodView):
    @require_auth
    @pyd_query(QueueQuery)
    @pyd_response(200, QueueOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def get(self, query: QueueQuery):
        """List queued (not yet running) executions across projects."""
        return _v1_get_queue().get_json()


@blp.route("/search")
class SearchView(MethodView):
    @require_auth
    @pyd_body(SearchIn)
    @pyd_response(200, SearchOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def post(self, payload: SearchIn):
        """Full-text search across projects, hosts, groups, roles, playbooks, variables, executions."""
        return _v1_global_search().get_json()


@blp.route("/capabilities")
class CapabilitiesView(MethodView):
    @require_auth
    @pyd_response(200, CapabilitiesOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def get(self):
        """Runtime capabilities negotiated with online workers (e.g. Mitogen)."""
        return _v1_capabilities().get_json()


@blp.route("/projects/<string:project_id>/queue-stats")
class ProjectQueueStatsView(MethodView):
    @require_auth
    @pyd_response(200, QueueStatsOut)
    @blp.doc(security=[{"BearerAuth": []}])
    def get(self, project_id: str):
        """Per-project counts of queued/running executions and available workers."""
        return _v1_project_queue_stats(project_id).get_json()
