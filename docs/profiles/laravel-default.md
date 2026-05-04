# Profile: Laravel (Default)

**Name:** `laravel-default`

## What it detects

Vanilla Laravel MVC projects with no DDD, CQRS, or action-based conventions — the majority of Laravel applications in the wild. This is also the **fallback profile** used when no more-specific profile scores above zero.

## Detection signals

| Signal | Weight | Condition |
|---|---|---|
| `app/Http/Controllers` exists | 40 | Directory present |
| `app/Models` exists | 30 | Directory present |
| `composer.json` requires `laravel/framework` | 30 | Package in require or require-dev |

## Conventions assumed

- Controllers live under `app/Http/Controllers/` (nested namespaces are fine).
- Eloquent models live under `app/Models/`.
- Events under `app/Events/`, listeners under `app/Listeners/`.
- Jobs under `app/Jobs/`.
- Policies under `app/Policies/`.
- Service Providers under `app/Providers/`.
- Routes in `routes/web.php` and/or `routes/api.php`.

## When to use

Choose `laravel-default` for any standard Laravel project that doesn't fit one of the more specific profiles. If in doubt, `nexus profile detect` will pick the best match automatically.

## Example `nexus.yml`

```yaml
schema_version: '1.0'
project:
  slug: my-app
  profile: laravel-default
```
