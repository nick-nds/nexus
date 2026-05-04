# Profile: Laravel Filament

**Name:** `laravel-filament`

## What it detects

Laravel projects built primarily as Filament admin panels. Detects Filament resources, pages, and widgets. Often layered on top of another profile (e.g. MVC or repository) but flagged independently so Filament-specific structure is visible to the graph.

## Detection signals

| Signal | Weight | Condition |
|---|---|---|
| `composer.json` requires `filament/filament` | 50 | Package present |
| `app/Filament` directory exists | 30 | Directory present |
| Class suffix `Resource` frequency ≥ 3 | 20 | At least 3 classes end in `Resource` |

## Conventions assumed

- Filament resources live under `app/Filament/Resources/`.
- Custom Filament pages live under `app/Filament/Pages/`.
- Filament widgets live under `app/Filament/Widgets/`.
- Models are the same as the rest of the app (`app/Models/`).

## When to use

Use `laravel-filament` when the project is predominantly a Filament admin panel. For projects that mix a Filament panel with a customer-facing Laravel app, consider running `nexus profile detect` to see which profile scores highest.

## Example `nexus.yml`

```yaml
schema_version: '1.0'
project:
  slug: my-panel
  profile: laravel-filament
```
