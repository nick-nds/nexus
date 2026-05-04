# Profile: Laravel DDD

**Name:** `laravel-ddd`

## What it detects

Laravel projects that use Domain-Driven Design with a module-per-bounded-context layout at `app/Modules/<Module>/(Domain|Application|Infrastructure|Presentation)/`, but without CQRS command/query handler separation. The superset profile `laravel-ddd-cqrs` extends this with CQRS detection.

## Detection signals

| Signal | Weight | Condition |
|---|---|---|
| `app/Modules/*/Domain` exists | 50 | Any module has a `Domain` subdirectory |
| `app/Modules/*/Application` exists | 30 | Any module has an `Application` subdirectory |
| `app/Modules/*/Infrastructure` exists | 20 | Any module has an `Infrastructure` subdirectory |

## Conventions assumed

- Each bounded context is a directory under `app/Modules/`.
- Entities and aggregates live in the `Domain` layer.
- Use-cases and application services live in the `Application` layer.
- Repository implementations, external adapters, and persistence live in `Infrastructure`.
- Controllers and API responses live in `Presentation`.

## When to use

Use `laravel-ddd` when your team applies DDD module structure but doesn't separate commands and queries into dedicated handler classes. If your project has `CommandHandlers/` and `QueryHandlers/`, use `laravel-ddd-cqrs` instead.

## Example `nexus.yml`

```yaml
schema_version: '1.0'
project:
  slug: my-ddd-app
  profile: laravel-ddd
```
