# Profile: Laravel (Repository + Service)

**Name:** `laravel-repository`

## What it detects

Laravel MVC projects augmented with a Repository pattern and a Service layer. Detects repositories, contracts, services, DTOs, and transformers via directory structure and class-suffix frequency. Common in teams that prefer traditional enterprise patterns over DDD.

## Detection signals

| Signal | Weight | Condition |
|---|---|---|
| `app/Repositories` directory exists | 30 | Directory present |
| `app/Services` directory exists | 25 | Directory present |
| `app/Contracts` directory exists | 15 | Directory present |
| Class suffix `Repository` frequency | variable | Many classes end in `Repository` |
| Class suffix `Service` frequency | variable | Many classes end in `Service` |

## Conventions assumed

- Eloquent models are in `app/Models/`.
- Repository interfaces are in `app/Contracts/Repositories/` (or `app/Contracts/`).
- Concrete repositories are in `app/Repositories/`.
- Service classes are in `app/Services/`.
- DTOs and data transfer objects may live in `app/DTOs/` or `app/Data/`.
- Contracts are bound in `AppServiceProvider` or a dedicated `RepositoryServiceProvider`.

## When to use

Use `laravel-repository` when your team uses an explicit repository abstraction for data access and a service layer for business logic. If you also have DDD module structure, consider `laravel-ddd`.

## Example `nexus.yml`

```yaml
schema_version: '1.0'
project:
  slug: my-enterprise-app
  profile: laravel-repository
```
