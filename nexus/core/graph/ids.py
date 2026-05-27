"""Stable, deterministic node-id generators.

Every node in the graph has a string id derived from the underlying
entity. The ids are deterministic - the same reflection document
produces the same ids on every build - which is the foundation of:

* The golden-diff testing strategy (Phase 3 onward).
* Incremental sync (Phase 3): change detection compares old and new
  graphs by id, so the only way for "the same controller" to keep its
  embedding cache hit is for it to keep its id.
* Cross-project federation (Phase 6): namespace prefixes are added on
  top of these ids without changing their semantic core.

Centralising id construction in one module means there is exactly one
place to look when something has the wrong identity, and exactly one
place to change when an id scheme needs to evolve.
"""

from __future__ import annotations


def class_id(fqn: str) -> str:
    """Stable id for a class node, keyed on the fully-qualified name."""
    return f"class:{fqn}"


def method_id(class_fqn: str, method_name: str) -> str:
    """Stable id for a method node belonging to a class."""
    return f"method:{class_fqn}::{method_name}"


def route_id(method: str, uri: str) -> str:
    """Stable id for a route node, keyed on its first HTTP verb and URI.

    A route handling multiple HTTP verbs (e.g. ``GET|HEAD``) collapses
    into one node identified by the first verb. The full method list
    is preserved in the node's ``attributes``.
    """
    return f"route:{method}:{uri}"


def middleware_id(name: str) -> str:
    """Stable id for a middleware node, keyed on its alias or class FQN."""
    return f"middleware:{name}"


def event_id(fqn: str) -> str:
    """Stable id for an event node."""
    return f"event:{fqn}"


def listener_id(fqn: str, method: str = "handle") -> str:
    """Stable id for a listener node.

    A listener is identified by its class plus the handler method name
    so a single class registering multiple handlers ends up as multiple
    listener nodes.
    """
    return f"listener:{fqn}::{method}"


def gate_id(ability: str) -> str:
    """Stable id for a gate node, keyed on the ability name."""
    return f"gate:{ability}"


def policy_id(fqn: str) -> str:
    """Stable id for a policy node, keyed on the policy class FQN."""
    return f"policy:{fqn}"


def binding_id(abstract: str) -> str:
    """Stable id for a container binding node, keyed on the abstract name."""
    return f"binding:{abstract}"


def schedule_id(expression: str, target: str) -> str:
    """Stable id for a scheduled task node.

    Two tasks with the same cron expression and the same command are
    rare in practice and would collapse into one node here. The full
    cron expression and command are preserved in attributes.
    """
    return f"schedule:{expression}|{target}"
