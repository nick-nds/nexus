# Profile: Laravel Actions

**Name:** `laravel-actions`

## What it detects

Action-based Laravel projects where most business logic lives in single-responsibility action classes (e.g. `CreateUserAction`, `SendInvoiceAction`). Common with the `lorisleiva/laravel-actions` package but also used as a hand-rolled convention.

## Detection signals

| Signal | Weight | Condition |
|---|---|---|
| `app/Actions` directory exists | 40 | Directory present |
| `composer.json` requires `lorisleiva/laravel-actions` | 30 | Package present |
| Class suffix `Action` frequency ≥ 5 | 30 | At least 5 classes end in `Action` |

## Conventions assumed

- Business logic lives in `app/Actions/` as single-method classes.
- Each action has a primary public method (often `handle`, `execute`, or `__invoke`).
- Controllers are thin - they accept a request, call one action, and return a response.
- Actions can be dispatched as jobs, queued, or run synchronously.

## When to use

Use `laravel-actions` if your project follows the action pattern, whether via the `lorisleiva/laravel-actions` package or by hand. Mix with `laravel-ddd` if you also have a module structure.

## Example `nexus.yml`

```yaml
schema_version: '1.0'
project:
  slug: my-actions-app
  profile: laravel-actions
```
