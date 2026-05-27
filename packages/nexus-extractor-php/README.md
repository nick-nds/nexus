# nick-nds/nexus-extractor

> Laravel runtime + AST extractor for the [Nexus](https://github.com/nick-nds/nexus) code intelligence tool.

This Composer package adds an Artisan command, `nexus:extract`, that introspects a Laravel application and emits a comprehensive `reflection.json` describing every primitive Nexus's Python pipeline needs to build a typed semantic graph of the codebase.

It is **convention-agnostic**: it uses Laravel's runtime registries, the Composer autoload class map, `instanceof` classification, and `nikic/php-parser`-based static analysis. There are no path assumptions, no project-specific configuration, and no telemetry.

## Requirements

- PHP 8.2+
- Laravel 10, 11, or 12

Older versions are not officially supported. See
[Unsupported Laravel versions](#unsupported-laravel-versions) at the bottom
of this document if you want to try it anyway.

## Installation

```bash
composer require --dev nick-nds/nexus-extractor
```

The service provider is auto-discovered.

## Usage

```bash
php artisan nexus:extract
```

This produces `storage/app/nexus/reflection.json`. Options:

| Option | Default | Description |
|---|---|---|
| `--output=PATH` | `storage/app/nexus/reflection.json` | Where to write the JSON file |
| `--include-vendor` | off | Include all vendor classes (large output) |
| `--vendor-allowlist=VENDOR/PKG` | none | Include only the listed vendor packages (repeatable) |
| `--include-tests` | off | Include the project's `tests/` classmap entries (see "Broken class handling") |
| `--profile=NAME` | none | Hint for downstream Python tooling (not used here) |
| `--quiet-progress` | off | Suppress per-phase progress output |

### Exit codes

- `0` - success (may include warnings about partial extraction)
- non-zero - fatal error, partial document written with `errors` entry
- `2` - usage / argument error

### Broken class handling

Phase B walks the Composer classmap and reflects on each class. This can fail
hard: if a class has an incompatible parent, a `final readonly` mismatch, or
a missing abstract method, PHP raises an uncatchable fatal error during
class declaration.

Nexus mitigates this two ways:

1. **`tests/` classes are skipped by default.** Test fixtures frequently
   contain intentionally-broken fakes that drift out of sync with the
   interfaces they implement. Use `--include-tests` to opt in.
2. **A shutdown handler writes a partial `reflection.json` on fatal.** If a
   class anywhere in the classmap can't be loaded, you still get a document
   containing every phase that completed before the crash, plus an `errors`
   entry naming the offending class, file, and PHP error. The process exits
   non-zero so scripted callers can detect the failure.

This is defense-in-depth under the assumption that the tool runs against a
working application. A class that is part of your production code and
cannot be declared is a real bug in your project; fix it and re-run.

## What it extracts

**Phase A - Runtime registries:**

- Routes (with middleware, where clauses, names, parameters)
- Service container bindings (singletons, regular, contextual, deferred, aliases)
- Event/listener map
- Gates and policies (with model→policy mapping)
- Middleware (global, group, route)
- Selected config (database connections, queue, broadcasting; secrets redacted)
- Scheduled tasks

**Phase B - Class autoload sweep:**

- Every class in the project class map
- Classification by `instanceof` against Laravel base types (Model, Controller, FormRequest, Resource, Job, Notification, Event, Listener, Policy, ServiceProvider, Observer, Middleware, Mailable, Command, Cast, Rule, Exception, ...)
- Reflection: methods, parameters with types, return types, PHP 8 attributes, traits, interfaces

**Phase C - AST static analysis (`nikic/php-parser`):**

- Event dispatches (`event(...)`, `Event::dispatch(...)`, `SomeEvent::dispatch(...)`)
- Job/notification dispatches
- View returns (`view('name', [...])`)
- Validation rules in `FormRequest::rules()` and inline `$request->validate(...)`
- Policy `authorize` calls and `Gate::*` calls

## Privacy

This package never makes outbound network calls. It only reads your application's reflection state and writes a single local JSON file. Inspect `src/` to verify.

## Development

```bash
composer install
composer check    # pint --test, phpstan, phpunit
```

## Unsupported Laravel versions

Nexus officially targets Laravel 10, 11, and 12 on PHP 8.2+. Those are the
versions we test, the versions CI runs, and the versions we promise will
keep working.

If you want to try Nexus on an older Laravel version anyway - typically
Laravel 9 on PHP 8.2+ - you can, but you're off the supported path.

**What will definitely not work:**

- **PHP 8.1 or older.** The extractor's source uses PHP 8.2+ features
  (`readonly` properties, first-class callable syntax, `enum`s). The code
  cannot be parsed on earlier PHP versions. There is no workaround.
- **Laravel 7 or older.** Too many of Laravel's internal APIs (container
  bindings, route registration, event dispatcher shape) are different
  from what our runtime extractors expect.

**What might work with a manual override:** Laravel 8 or 9 on PHP 8.2+.
Most of the extractor talks to stable Laravel APIs (Router, Container,
Gate, Event Dispatcher) that haven't changed meaningfully since Laravel 5.
We just haven't tested these combinations.

### How to try it on an unsupported version

The package `composer.json` pins `illuminate/console`, `illuminate/contracts`,
`illuminate/support`, `illuminate/routing`, `illuminate/events`, and
`illuminate/filesystem` to `^10.0 || ^11.0 || ^12.0`. Composer will refuse to
install on an older project. To get past this:

1. Fork this repository.
2. In your fork's `composer.json`, relax the `illuminate/*` constraints to
   include the version you want to try - for example,
   `^9.0 || ^10.0 || ^11.0 || ^12.0`. Do **not** relax the `php` constraint
   below `^8.2` - that will not help.
3. Tag a version in your fork (or use `dev-main`) and add it to your
   project's `composer.json` as a VCS repository:

   ```jsonc
   {
       "repositories": [
           {
               "type": "vcs",
               "url": "https://github.com/your-username/nexus-extractor-php.git"
           }
       ],
       "require-dev": {
           "nick-nds/nexus-extractor": "dev-main"
       }
   }
   ```
4. Run `composer update nick-nds/nexus-extractor`, then `php artisan nexus:extract`.

### What to expect

- **Phase A (runtime registries)** probably works. Laravel's Router,
  Container, Gate, and Event Dispatcher internals are stable across
  versions and we read them defensively.
- **Phase B (class sweep)** probably works. It relies on Composer's class
  map and PHP reflection, which are version-independent.
- **Phase C (AST static analysis)** works for any PHP source `nikic/php-parser`
  can parse, which covers PHP 7.0 and up.
- **Per-version edge cases are undocumented.** If something breaks, you get
  to investigate it.

If you do try this and it works, we'd love a PR that adds your Laravel
version to the supported range. If you try it and it breaks in a way you
can fix, we'd love a PR for that too. Bug reports against unsupported
versions without a proposed fix will be closed.

## License

Business Source License 1.1 - see [LICENSE](LICENSE) for full terms.

Free to use for any purpose **except** building a competing commercial product. Converts to Apache 2.0 on 2030-05-27.

For alternative licensing, contact nitin.niku97@gmail.com.
