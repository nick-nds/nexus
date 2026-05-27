"""Wire built-in query tools into a :class:`ToolRegistry`.

Called once from the query engine's bootstrap code. A user plugin
that wants to override a built-in tool can register a replacement
after the built-ins; since :class:`ToolRegistry.register` rejects
duplicates, plugins must either use a different name or intercept
the bootstrap flow - the same deliberate friction the embedder
registry applies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.core.query.tools.describe_class import DescribeClassTool
from nexus.core.query.tools.describe_flow import DescribeFlowTool
from nexus.core.query.tools.describe_module import DescribeModuleTool
from nexus.core.query.tools.expand_call_tree import ExpandCallTreeTool
from nexus.core.query.tools.explore_entity import ExploreEntityTool
from nexus.core.query.tools.find_cache_users import FindCacheUsersTool
from nexus.core.query.tools.find_callers import FindCallersTool
from nexus.core.query.tools.find_dispatchers import FindDispatchersTool
from nexus.core.query.tools.find_event_chains import FindEventChainsTool
from nexus.core.query.tools.find_handlers import FindHandlersTool
from nexus.core.query.tools.find_implementations import FindImplementationsTool
from nexus.core.query.tools.find_jobs_dispatching import FindJobsDispatchingTool
from nexus.core.query.tools.find_listeners import FindListenersTool
from nexus.core.query.tools.get_full_block import GetFullBlockTool
from nexus.core.query.tools.get_model_context import GetModelContextTool
from nexus.core.query.tools.get_node_body import GetNodeBodyTool
from nexus.core.query.tools.get_policy_for import GetPolicyForTool
from nexus.core.query.tools.get_request_flow import GetRequestFlowTool
from nexus.core.query.tools.list_by_kind import ListByKindTool
from nexus.core.query.tools.list_modules import ListModulesTool
from nexus.core.query.tools.list_routes import ListRoutesTool
from nexus.core.query.tools.list_scheduled_tasks import ListScheduledTasksTool
from nexus.core.query.tools.resolve_binding import ResolveBindingTool
from nexus.core.query.tools.semantic_search import SemanticSearchTool
from nexus.core.query.tools.trace_route import TraceRouteTool

if TYPE_CHECKING:
    from nexus.core.query.registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register every built-in query tool on ``registry``.

    Order is deterministic so the CLI's ``--help`` output is
    stable across runs. New tools should be appended at the end.
    """
    # Batch 1: structural primitives.
    registry.register(ListRoutesTool)
    registry.register(ListScheduledTasksTool)
    registry.register(ListByKindTool)
    registry.register(ListModulesTool)
    registry.register(DescribeModuleTool)
    registry.register(ExploreEntityTool)
    registry.register(DescribeClassTool)
    registry.register(GetModelContextTool)

    # Batch 2: route-centric traces.
    registry.register(TraceRouteTool)
    registry.register(GetRequestFlowTool)
    registry.register(DescribeFlowTool)
    registry.register(FindHandlersTool)

    # Batch 3: events / jobs / policies / bindings / call graph.
    registry.register(FindListenersTool)
    registry.register(FindDispatchersTool)
    registry.register(FindEventChainsTool)
    registry.register(FindJobsDispatchingTool)
    registry.register(GetPolicyForTool)
    registry.register(ResolveBindingTool)
    registry.register(FindImplementationsTool)
    registry.register(FindCallersTool)
    registry.register(ExpandCallTreeTool)
    registry.register(FindCacheUsersTool)

    # Semantic retrieval.
    registry.register(SemanticSearchTool)

    # Body retrieval - escape hatch when ``semantic_search`` misses a
    # known chunk. See ``nexus-feedback-method-body-retrieval.md`` for
    # the user-pain motivation.
    registry.register(GetFullBlockTool)
    registry.register(GetNodeBodyTool)
