# Profile: Laravel DDD with CQRS

**Name:** `laravel-ddd-cqrs`

## What it detects

Laravel projects that combine Domain-Driven Design module structure with CQRS: separate `CommandHandlers/` and `QueryHandlers/` directories under each module's `Application` layer. Detects the handler split via naming suffixes as well as directory structure.

## Detection signals

| Signal | Weight | Condition |
|---|---|---|
| `app/Modules/*/Domain` exists | 40 | Any module has a `Domain` subdirectory |
| `app/Modules/*/Application/CommandHandlers` exists | 25 | Application layer has a `CommandHandlers` directory |
| `app/Modules/*/Application/QueryHandlers` exists | 15 | Application layer has a `QueryHandlers` directory |
| Class suffix `Handler` frequency | variable | Many classes end in `Handler` |

## Conventions assumed

- Module layout follows `laravel-ddd` conventions (see [Laravel DDD profile](laravel-ddd.md)).
- Commands are plain data objects (no `handle` method).
- Command handlers live in `Application/CommandHandlers/` and have a single `handle(Command $cmd)` method.
- Query handlers live in `Application/QueryHandlers/` and have a single `handle(Query $query)` method.

## When to use

Use `laravel-ddd-cqrs` when both the DDD module structure and the CQRS handler split are present. If you have modules but no handler split, use `laravel-ddd`.

## Example `nexus.yml`

```yaml
schema_version: '1.0'
project:
  slug: my-cqrs-app
  profile: laravel-ddd-cqrs
```
