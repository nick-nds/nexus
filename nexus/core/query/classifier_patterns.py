"""Compiled regex patterns + noun table for :class:`QueryClassifier`.

Extracted from ``classifier.py`` so the dispatcher stays under ~250
lines (the rule list grew large and hid the actual classify-flow).
Every constant in this module is consumed by the matcher methods on
:class:`QueryClassifier`; nothing else imports from here.

The constants are deliberately ordered to mirror the classifier's
rule-matching cascade so a maintainer reading top-to-bottom sees
the patterns in the same order the dispatcher tries them.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Lexical patterns
# ---------------------------------------------------------------------------

# HTTP verb + path ("GET /api/users", "POST /orders").
HTTP_VERB_PATH = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[\w/\-.{}:*]*)",
    re.IGNORECASE,
)

# Fully-qualified PHP class name: at least one backslash, starts with a
# capitalised segment. Captures both ``App\Models\User`` and
# ``App\Http\Controllers\UserController::show``.
FQN = re.compile(
    r"(?<![\w\\])([A-Z][\w]*(?:\\[A-Z][\w]*)+(?:::[A-Za-z_][\w]*)?)",
)

# Kinds that nudge a plain FQN toward ``get_model_context`` rather
# than ``describe_class``.
MODEL_NAMESPACE_HINT = re.compile(r"\\Models?\\", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Phrase patterns - ordered to match the classifier's rule cascade
# ---------------------------------------------------------------------------

# A listener-of style phrase. Captures the noun after "listens to",
# "listeners of", "listeners for", "who handles".
LISTENERS_OF = re.compile(
    r"(?:who|what)\s+listens?\s+(?:to|for)\s+(\S+)|"
    r"listeners?\s+(?:for|of)\s+(\S+)|"
    r"who\s+handles?\s+(?:the\s+)?(\S+)\s+event",
    re.IGNORECASE,
)

# Who dispatches / fires / triggers an event.
DISPATCHERS_OF = re.compile(
    r"(?:who|what)\s+(?:dispatches?|fires?|triggers?|emits?)\s+(\S+)|"
    r"(?:dispatchers?|firers?)\s+(?:for|of)\s+(\S+)|"
    r"where\s+(?:is\s+)?(\S+)\s+(?:dispatched|fired)",
    re.IGNORECASE,
)

# Who dispatches a job. Kept separate so we can route to
# ``find_jobs_dispatching`` instead of ``find_dispatchers``.
JOB_DISPATCHERS = re.compile(
    r"(?:who|where)\s+(?:dispatches?|queues?)\s+(?:the\s+)?(\S+Job)\b|"
    r"(\S+Job)\s+(?:dispatched|queued)",
    re.IGNORECASE,
)

# "who implements X" / "implementations of X".
IMPLEMENTERS_OF = re.compile(
    r"(?:who|what)\s+implements?\s+(\S+)|"
    r"implementations?\s+of\s+(\S+)|"
    r"implementers?\s+of\s+(\S+)",
    re.IGNORECASE,
)

# "who calls X" / "callers of X".
CALLERS_OF = re.compile(
    r"(?:who|what)\s+calls?\s+(\S+)|callers?\s+of\s+(\S+)",
    re.IGNORECASE,
)

# "policy for X", "authorization for X".
POLICY_FOR = re.compile(
    r"polic(?:y|ies)\s+(?:for|on|of)\s+(\S+)|"
    r"authori[sz]ation\s+(?:for|on)\s+(\S+)",
    re.IGNORECASE,
)

# "bound to X" / "what does the container resolve X to".
BINDING_OF = re.compile(
    r"(?:bound|binding|container)\s+(?:for|to|of)\s+(\S+)|"
    r"resolve[sd]?\s+(\S+)",
    re.IGNORECASE,
)

# Plain "show routes" / "list routes" with no further qualifiers.
# Accepts:
#   "list routes", "list all routes", "show routes", "show me the routes",
#   "show all routes", "what are the routes", "what routes exist".
LIST_ROUTES = re.compile(
    r"(?:^|\s)(?:list|show|what)\s+(?:\w+\s+)*?routes\b",
    re.IGNORECASE,
)

# "list events" / "show all jobs" / "what notifications exist" - the
# kind-scoped enumeration for any singular/plural class kind. Kept
# separate from ``LIST_ROUTES`` so the more-specific routes rule
# stays a clean shortcut. ``LIST_BY_KIND_NOUNS`` maps the captured
# noun (singular OR plural) back to the canonical NodeKind value.
#
# The filler between verb and noun is restricted to articles /
# quantifiers (``all``, ``the``, ``me all``, ``me the`` …) - NOT
# arbitrary words. Otherwise ``"show me the Invoice model"`` would
# slurp ``"Invoice"`` as filler and route to ``list_by_kind(model)``
# instead of ``explore_entity(Invoice)``. The trailing ``\s*\??$``
# pins the noun to the end of the question, with optional ``exist``
# / ``are there`` qualifiers; that anchor blocks "list events that
# fire on login" (which should go elsewhere) from matching.
LIST_BY_KIND = re.compile(
    r"(?:^|\s)(?:list|show|what)\s+"
    r"(?:(?:all|the|every|some|me\s+(?:all|the))\s+)*"
    r"(?P<noun>events?|jobs?|notifications?|listeners?|observers?|"
    r"models?|controllers?|form[\s_-]?requests?|policies?|"
    r"mailables?|resources?|commands?|service[\s_-]?providers?|"
    r"casts?|classes?)"
    r"(?:\s+(?:exist|are\s+there))?\s*\??$",
    re.IGNORECASE,
)

#: Maps a captured noun (lower-cased, after collapsing whitespace) to
#: the corresponding ``NodeKind`` value. We keep the table alongside
#: the regex because it's a UX surface - the kind names users type
#: don't always match the enum names exactly (``form requests`` vs
#: ``form_request``, ``policies`` vs ``policy``).
LIST_BY_KIND_NOUNS: dict[str, str] = {
    "event": "event",
    "events": "event",
    "job": "job",
    "jobs": "job",
    "notification": "notification",
    "notifications": "notification",
    "listener": "listener",
    "listeners": "listener",
    "observer": "observer",
    "observers": "observer",
    "model": "model",
    "models": "model",
    "controller": "controller",
    "controllers": "controller",
    "form request": "form_request",
    "form requests": "form_request",
    "form-request": "form_request",
    "form-requests": "form_request",
    "form_request": "form_request",
    "form_requests": "form_request",
    "policy": "policy",
    "policies": "policy",
    "mailable": "mailable",
    "mailables": "mailable",
    "resource": "resource",
    "resources": "resource",
    "command": "command",
    "commands": "command",
    "service provider": "service_provider",
    "service providers": "service_provider",
    "service-provider": "service_provider",
    "service-providers": "service_provider",
    "service_provider": "service_provider",
    "service_providers": "service_provider",
    "cast": "cast",
    "casts": "cast",
    "class": "class",
    "classes": "class",
}

# "what handles X / who handles X / which controller handles X / handler for X".
# Captures the trailing target (path, name, or "verb path"), excluding the
# "X event" form so the listener rule keeps that intent.
HANDLER_OF = re.compile(
    r"(?:^|\s)(?:"
    r"(?:what|who|which\s+\w+)\s+handles?\s+(?:the\s+)?"
    r"|(?:show\s+)?handler\s+for\s+(?:the\s+)?"
    r")(?P<rest>.+?)\s*\??$",
    re.IGNORECASE,
)

# "show flow for /path" / "request flow for POST /path".
REQUEST_FLOW = re.compile(
    r"(?:^|\s)(?:show\s+)?(?:full\s+)?(?:request\s+)?flow\s+for\s+"
    r"(?:(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?"
    r"(?P<uri>/\S+)",
    re.IGNORECASE,
)

# Fuzzy flow discovery - natural-language phrasings where the user
# doesn't have a precise URI. ``_match_request_flow`` runs first, so
# anything containing ``flow for /...`` already routed there; this
# rule catches the verb-phrase forms:
#
#   "how does order placement work"
#   "walk me through the lead creation flow"
#   "describe the flow for placing an order"
#   "what happens when a user signs up"
DESCRIBE_FLOW = re.compile(
    r"(?:^|\s)(?:"
    r"how\s+does\s+(?P<rest_a>.+?)\s+(?:work|happen)"
    r"|walk\s+me\s+through\s+(?:the\s+)?(?P<rest_b>.+?)"
    r"|describe\s+(?:the\s+)?flow\s+(?:for|of)\s+(?P<rest_c>.+?)"
    r"|what\s+happens\s+(?:when|on|after|during)\s+(?P<rest_d>.+?)"
    r")\s*\??$",
    re.IGNORECASE,
)

# Trailing filler words on a captured fuzzy-flow target - stripped
# so ``"the lead creation flow"`` and ``"order placement"`` both
# resolve cleanly to their underlying nouns.
FLOW_NOISE_SUFFIX = re.compile(
    r"\s+(?:flow|process|happens|happen|works|work|route|endpoint)\s*$",
    re.IGNORECASE,
)

# Discovery questions: "explain X", "tell me about X", "what is X",
# "show me X entity", "X domain", "X model". Captures the target
# name; the FQN-aware rule (``_match_fqn``) handles fully-qualified
# inputs and runs first, so this rule only fires for short names
# and fragments.
EXPLORE_ENTITY = re.compile(
    r"(?:^|\s)(?:"
    r"explain(?:\s+the)?"
    r"|describe(?:\s+the)?"
    r"|tell\s+me\s+about(?:\s+the)?"
    r"|what\s+(?:is|are)(?:\s+the)?"
    r"|show\s+me(?:\s+the)?"
    r"|show(?:\s+the)?"
    r"|who\s+is(?:\s+the)?"
    r")\s+(?P<rest>.+?)\s*\??$",
    re.IGNORECASE,
)

# Words that - when they appear at the end of the captured target -
# don't change the lookup but tell us the user is talking about a
# class. We strip them so ``"Product entity"`` and ``"Product model"``
# both resolve to ``"Product"``.
ENTITY_NOISE_SUFFIX = re.compile(
    r"\s+(?:entity|class|model|service|controller|aggregate|"
    r"command|handler|event|listener|job|notification|"
    r"and\s+(?:its|all)\s+related\s+entities?)\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: Minimum length for an entity-discovery target. Sub-three-character
#: fragments produce noisy match sets dominated by substring hits; we
#: refuse them and let the semantic fallback catch the rare cases
#: where a two-letter class name was intentional.
MIN_ENTITY_LENGTH = 3
