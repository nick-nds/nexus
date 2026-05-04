# Profile: Laravel API

**Name:** `laravel-api`

## What it detects

API-only Laravel applications: heavy on API resources, transformers, and a route-first structure, typically without Blade views. Detected via the presence of `routes/api.php`, an `Api` namespace in the controllers directory, and/or an `Http/Resources` directory.

## Detection signals

| Signal | Weight | Condition |
|---|---|---|
| `routes/api.php` exists | 40 | File present |
| `app/Http/Controllers/Api` exists | 30 | Directory present |
| `app/Http/Resources` exists | 30 | Directory present |

## Conventions assumed

- All routes are under `routes/api.php` (no `routes/web.php` or Blade).
- API controllers live under `app/Http/Controllers/Api/`.
- Responses are shaped through API Resources (`app/Http/Resources/`).
- Authentication is via tokens (Sanctum, Passport, JWT) rather than session cookies.

## When to use

Use `laravel-api` for projects that are exclusively backend APIs. If your project has both a web UI and an API (a common monolith pattern), `laravel-default` is usually the better fit.

## Example `nexus.yml`

```yaml
schema_version: '1.0'
project:
  slug: my-api
  profile: laravel-api
```
