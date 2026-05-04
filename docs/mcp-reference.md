# MCP Reference

Every tool that Nexus exposes over MCP, with input schema, example call, and example response.

All tools are registered automatically from the `ToolRegistry` when `nexus mcp serve` starts. Tool names, input schemas, and output shapes are **frozen at v1.0** — breaking changes require a semver major bump.

---

## describe_class

Describe a PHP class: its kind, parent, methods, properties, and which routes reference it.

**Input schema**

| Field | Type | Required | Description |
|---|---|---|---|
| `fqn` | string | yes | Fully-qualified class name, e.g. `App\Models\User`. |

**Example**

```json
{
  "tool": "describe_class",
  "arguments": {
    "fqn": "App\\Http\\Controllers\\UserController"
  }
}
```

**Example response**

```json
{
  "fqn": "App\\Http\\Controllers\\UserController",
  "kind": "controller",
  "parent": "App\\Http\\Controllers\\Controller",
  "interfaces": [],
  "methods": [
    { "name": "index", "visibility": "public" },
    { "name": "store", "visibility": "public" }
  ],
  "properties": [],
  "related_routes": [
    { "route_id": "route:GET:/users", "uri": "/users", "methods": ["GET"] }
  ],
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `class_not_found` | No node with that FQN in the graph. |

---

## find_callers

Find all call sites of a method across the codebase.

> **Note:** `find_callers` relies on `CALLS` edges, which are populated by Phase 3 LSP-driven analysis (Intelephense or phpactor). If no LSP server is configured, this tool returns an empty result. For dispatch-based questions, use `find_dispatchers` or `find_jobs_dispatching` instead — those work via static AST analysis and do not require an LSP.

**Input schema**

| Field | Type | Required | Description |
|---|---|---|---|
| `method_fqn` | string | yes | `ClassName::methodName`, e.g. `App\Services\AuthService::login`. |

**Example**

```json
{
  "tool": "find_callers",
  "arguments": {
    "method_fqn": "App\\Services\\AuthService::login"
  }
}
```

**Example response**

```json
{
  "method_fqn": "App\\Services\\AuthService::login",
  "callers": [
    {
      "caller_fqn": "App\\Http\\Controllers\\AuthController::store",
      "caller_kind": "controller",
      "file": "app/Http/Controllers/AuthController.php",
      "line": 42
    }
  ],
  "total": 1,
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `method_not_found` | No method node with that FQN in the graph. |

---

## find_dispatchers

Find all places that dispatch a given event. Results are populated from static AST analysis — no LSP server required.

**Input schema**

| Field | Type | Required | Description |
|---|---|---|---|
| `event` | string | yes | Event class FQN or graph node ID. |

**Example**

```json
{
  "tool": "find_dispatchers",
  "arguments": {
    "event": "App\\Events\\UserRegistered"
  }
}
```

**Example response**

```json
{
  "event_fqn": "App\\Events\\UserRegistered",
  "dispatchers": [
    {
      "dispatcher_fqn": "App\\Http\\Controllers\\AuthController::register",
      "dispatcher_kind": "controller"
    }
  ],
  "total": 1,
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `event_not_found` | No event node with that FQN in the graph. |

---

## find_event_chains

Trace the full listener/subscriber chain triggered by an event, up to `max_depth` levels.

**Input schema**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `event` | string | yes | — | Event class FQN or graph node ID. |
| `max_depth` | integer | no | `3` | Maximum chain depth to traverse. Must be ≥ 1. |

**Example**

```json
{
  "tool": "find_event_chains",
  "arguments": {
    "event": "App\\Events\\OrderPlaced",
    "max_depth": 2
  }
}
```

**Example response**

```json
{
  "event_fqn": "App\\Events\\OrderPlaced",
  "steps": [
    { "depth": 1, "kind": "listener", "fqn": "App\\Listeners\\SendOrderConfirmation", "method": "handle" },
    { "depth": 1, "kind": "listener", "fqn": "App\\Listeners\\UpdateInventory", "method": "handle" }
  ],
  "depth_reached": 1,
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `event_not_found` | No event node with that FQN in the graph. |

---

## find_handlers

Find route handlers matching an optional URI glob, HTTP method, or handler FQN. At least one filter is required.

**Input schema**

| Field | Type | Required | Description |
|---|---|---|---|
| `uri_glob` | string\|null | no | Shell-style glob for the URI (e.g. `/api/v1/*`). |
| `method` | string\|null | no | HTTP method filter (e.g. `GET`, `POST`). |
| `handler_fqn` | string\|null | no | Controller FQN or `Controller::method`. |

**Example**

```json
{
  "tool": "find_handlers",
  "arguments": {
    "uri_glob": "/api/v1/users*",
    "method": "GET"
  }
}
```

**Example response**

```json
{
  "handlers": [
    {
      "uri": "/api/v1/users",
      "methods": ["GET"],
      "class_fqn": "App\\Http\\Controllers\\UserController",
      "method_name": "index",
      "action_kind": "controller",
      "middleware": ["api", "auth:sanctum"]
    }
  ],
  "total": 1,
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `missing_filter` | No filter was provided. |

---

## find_implementations

Find all concrete implementations of an interface or abstract class.

**Input schema**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `interface_fqn` | string | yes | — | Fully-qualified interface or abstract class name. |
| `include_subclasses` | boolean | no | `false` | Also include subclasses (not just direct implementors). |

**Example**

```json
{
  "tool": "find_implementations",
  "arguments": {
    "interface_fqn": "App\\Contracts\\PaymentGateway",
    "include_subclasses": false
  }
}
```

**Example response**

```json
{
  "interface_fqn": "App\\Contracts\\PaymentGateway",
  "implementations": [
    { "fqn": "App\\Services\\StripeGateway", "kind": "class", "via": "implements" },
    { "fqn": "App\\Services\\PaypalGateway", "kind": "class", "via": "implements" }
  ],
  "total": 2,
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `class_not_found` | No node with that FQN in the graph. |

---

## find_jobs_dispatching

Find all dispatch sites for a given job class. Results are populated from static AST analysis — no LSP server required.

**Input schema**

| Field | Type | Required | Description |
|---|---|---|---|
| `job` | string | yes | Job class FQN or short name (e.g. `App\Jobs\SendWelcomeEmail`). |

**Example**

```json
{
  "tool": "find_jobs_dispatching",
  "arguments": {
    "job": "App\\Jobs\\SendWelcomeEmail"
  }
}
```

**Example response**

```json
{
  "job_fqn": "App\\Jobs\\SendWelcomeEmail",
  "dispatch_sites": [
    {
      "dispatcher_fqn": "App\\Http\\Controllers\\AuthController::register",
      "dispatcher_kind": "controller"
    }
  ],
  "total": 1,
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `job_not_found` | No job node with that FQN in the graph. |

---

## find_listeners

List all listeners and subscribers registered for an event.

**Input schema**

| Field | Type | Required | Description |
|---|---|---|---|
| `event` | string | yes | Event class FQN or graph node ID. |

**Example**

```json
{
  "tool": "find_listeners",
  "arguments": {
    "event": "App\\Events\\UserRegistered"
  }
}
```

**Example response**

```json
{
  "event_fqn": "App\\Events\\UserRegistered",
  "listeners": [
    {
      "listener_fqn": "App\\Listeners\\SendWelcomeEmail",
      "method": "handle",
      "queued": true
    },
    {
      "listener_fqn": "App\\Listeners\\CreateUserProfile",
      "method": "handle",
      "queued": false
    }
  ],
  "total": 2,
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `event_not_found` | No event node with that FQN in the graph. |

---

## get_model_context

Return full context for an Eloquent model: relationships, observers, policies, events, and dispatched jobs.

**Input schema**

| Field | Type | Required | Description |
|---|---|---|---|
| `fqn` | string | yes | Fully-qualified Eloquent model class name. |

**Example**

```json
{
  "tool": "get_model_context",
  "arguments": {
    "fqn": "App\\Models\\Order"
  }
}
```

**Example response**

```json
{
  "fqn": "App\\Models\\Order",
  "is_model": true,
  "table": "orders",
  "relationships": [
    { "name": "user", "kind": "belongsTo", "related_fqn": "App\\Models\\User" },
    { "name": "items", "kind": "hasMany", "related_fqn": "App\\Models\\OrderItem" }
  ],
  "observers": ["App\\Observers\\OrderObserver"],
  "policy": null,
  "events_fired": [],
  "jobs_dispatched": [],
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `class_not_found` | No node with that FQN in the graph. |

---

## get_policy_for

Return the Gate policy bound to a given model.

**Input schema**

| Field | Type | Required | Description |
|---|---|---|---|
| `model_fqn` | string | yes | Fully-qualified Eloquent model class name. |

**Example**

```json
{
  "tool": "get_policy_for",
  "arguments": {
    "model_fqn": "App\\Models\\Post"
  }
}
```

**Example response**

```json
{
  "model_fqn": "App\\Models\\Post",
  "policy_fqn": "App\\Policies\\PostPolicy",
  "methods": ["viewAny", "view", "create", "update", "delete", "restore", "forceDelete"],
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `model_not_found` | No model node with that FQN in the graph. |
| `policy_not_found` | Model exists but has no registered Gate policy. |

---

## get_request_flow

Show the complete request lifecycle for a route: middleware stack, form request validation, controller handler, service calls, events, and jobs.

**Input schema**

At least one of `route_id`, or `method`+`uri` must be provided.

| Field | Type | Required | Description |
|---|---|---|---|
| `route_id` | string\|null | no | Internal route identifier from `list_routes`. |
| `method` | string\|null | no | HTTP method (e.g. `POST`). |
| `uri` | string\|null | no | Exact URI path (e.g. `/api/orders`). |

**Example**

```json
{
  "tool": "get_request_flow",
  "arguments": {
    "method": "POST",
    "uri": "/api/v1/orders"
  }
}
```

**Example response**

```json
{
  "uri": "/api/v1/orders",
  "methods": ["POST"],
  "middleware": ["api", "auth:sanctum", "throttle:60,1"],
  "form_request": "App\\Http\\Requests\\CreateOrderRequest",
  "handler": {
    "class_fqn": "App\\Http\\Controllers\\OrderController",
    "method_name": "store",
    "action_kind": "controller"
  },
  "event_chain": [],
  "job_chain": [],
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `route_not_found` | No route matches the provided criteria. |

---

## list_routes

List registered routes with optional filters.

**Input schema**

All fields are optional. Providing no fields returns all routes.

| Field | Type | Description |
|---|---|---|
| `method` | string\|null | HTTP method filter (e.g. `GET`). |
| `uri_glob` | string\|null | Shell-style glob for the URI (e.g. `/api/*`). |
| `name_glob` | string\|null | Shell-style glob for the route name (e.g. `api.users.*`). |
| `middleware` | string\|null | Filter to routes that include this middleware. |

**Example**

```json
{
  "tool": "list_routes",
  "arguments": {
    "method": "GET",
    "uri_glob": "/api/v1/*"
  }
}
```

**Example response**

```json
{
  "routes": [
    {
      "route_id": "route:GET:/api/v1/users",
      "uri": "/api/v1/users",
      "methods": ["GET"],
      "name": "api.users.index",
      "controller": "App\\Http\\Controllers\\UserController",
      "action": "index",
      "action_kind": "controller",
      "middleware": ["api", "auth:sanctum"]
    }
  ],
  "total": 1,
  "returned": 1
}
```

---

## resolve_binding

Resolve a service-container binding to its concrete implementation.

**Input schema**

| Field | Type | Required | Description |
|---|---|---|---|
| `abstract` | string | yes | Abstract class or interface FQN registered in the container. |

**Example**

```json
{
  "tool": "resolve_binding",
  "arguments": {
    "abstract": "App\\Contracts\\PaymentGateway"
  }
}
```

**Example response**

```json
{
  "abstract": "App\\Contracts\\PaymentGateway",
  "concrete": "App\\Services\\StripeGateway",
  "concrete_kind": "class",
  "shared": true,
  "provider_file": "app/Providers/AppServiceProvider.php",
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `binding_not_found` | No binding registered for the abstract type. |

---

## semantic_search

Search the codebase semantically using vector similarity.

**Input schema**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Natural-language query string. |
| `top_k` | integer | no | `30` | Candidate results to retrieve from the vector store before re-ranking. |
| `final_k` | integer | no | `10` | Results to return after re-ranking. |

**Example**

```json
{
  "tool": "semantic_search",
  "arguments": {
    "query": "how is user authentication implemented",
    "top_k": 20,
    "final_k": 5
  }
}
```

**Example response**

```json
{
  "query": "how is user authentication implemented",
  "results": [
    {
      "chunk_id": "chunk:App\\Http\\Controllers\\AuthController::login",
      "node_id": "method:App\\Http\\Controllers\\AuthController::login",
      "kind": "method",
      "label": "AuthController::login",
      "score": 0.923,
      "text": "Authenticate a user via email and password..."
    }
  ],
  "total": 5,
  "error": null,
  "error_code": null
}
```

---

## trace_route

Trace all code touched by a route: middleware, form request, controller, services, events, and jobs — in execution order.

**Input schema**

At least one of `route_id`, or `method`+`uri` must be provided.

| Field | Type | Required | Description |
|---|---|---|---|
| `route_id` | string\|null | no | Internal route identifier from `list_routes`. |
| `method` | string\|null | no | HTTP method (e.g. `GET`). |
| `uri` | string\|null | no | Exact URI path (e.g. `/api/v1/users/{id}`). |

**Example**

```json
{
  "tool": "trace_route",
  "arguments": {
    "method": "GET",
    "uri": "/api/v1/users/{id}"
  }
}
```

**Example response**

```json
{
  "uri": "/api/v1/users/{id}",
  "methods": ["GET"],
  "middleware": ["api", "auth:sanctum"],
  "handler": {
    "class_fqn": "App\\Http\\Controllers\\UserController",
    "method_name": "show",
    "action_kind": "controller"
  },
  "fires_events": [],
  "dispatches_jobs": [],
  "error": null,
  "error_code": null
}
```

**Error codes**

| Code | Meaning |
|---|---|
| `route_not_found` | No route matches the provided criteria. |
