"""Concrete query tools.

Each tool is a small class satisfying the :class:`~nexus.core.query.Tool`
protocol. The module exports helpers for wiring the full set into
a :class:`~nexus.core.query.ToolRegistry` via
:func:`register_builtin_tools`.
"""

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
from nexus.core.query.tools.registration import register_builtin_tools
from nexus.core.query.tools.resolve_binding import ResolveBindingTool
from nexus.core.query.tools.semantic_search import SemanticSearchTool
from nexus.core.query.tools.trace_route import TraceRouteTool

__all__ = [
    "DescribeClassTool",
    "DescribeFlowTool",
    "DescribeModuleTool",
    "ExpandCallTreeTool",
    "ExploreEntityTool",
    "FindCacheUsersTool",
    "FindCallersTool",
    "FindDispatchersTool",
    "FindEventChainsTool",
    "FindHandlersTool",
    "FindImplementationsTool",
    "FindJobsDispatchingTool",
    "FindListenersTool",
    "GetFullBlockTool",
    "GetModelContextTool",
    "GetNodeBodyTool",
    "GetPolicyForTool",
    "GetRequestFlowTool",
    "ListByKindTool",
    "ListModulesTool",
    "ListRoutesTool",
    "ListScheduledTasksTool",
    "ResolveBindingTool",
    "SemanticSearchTool",
    "TraceRouteTool",
    "register_builtin_tools",
]
