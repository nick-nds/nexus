<?php

declare(strict_types=1);

namespace Nexus\Extractor\Console;

use Illuminate\Console\Command;
use Illuminate\Contracts\Foundation\Application;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\ExtractionRunner;
use Nexus\Extractor\Extraction\Extractor;
use Nexus\Extractor\Extraction\ExtractorPipeline;
use Nexus\Extractor\Extraction\PhaseA\BindingExtractor;
use Nexus\Extractor\Extraction\PhaseA\ConfigExtractor;
use Nexus\Extractor\Extraction\PhaseA\EventListenerExtractor;
use Nexus\Extractor\Extraction\PhaseA\GatePolicyExtractor;
use Nexus\Extractor\Extraction\PhaseA\MiddlewareExtractor;
use Nexus\Extractor\Extraction\PhaseA\RouteExtractor;
use Nexus\Extractor\Extraction\PhaseA\ScheduleExtractor;
use Nexus\Extractor\Extraction\PhaseB\ClassMapWalker;
use Nexus\Extractor\Extraction\PhaseB\ProjectClassExtractor;
use Nexus\Extractor\Extraction\PhaseC\StaticAnalysisExtractor;
use Nexus\Extractor\Extraction\Support\NamespaceExclusionFilter;
use Nexus\Extractor\Extraction\Support\PackageScope;
use Nexus\Extractor\Output\JsonWriter;
use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\CurrentClassTracker;
use Nexus\Extractor\Support\ErrorCollector;
use Nexus\Extractor\Support\FatalErrorHandler;
use Nexus\Extractor\Support\ProgressReporter;

/**
 * Artisan command that runs the Nexus extraction pipeline scoped to a single
 * Composer package, using a Testbench skeleton as the host application.
 *
 * Exit codes:
 *   0 - success (warnings allowed)
 *   1 - fatal pipeline or write error
 *   2 - usage error (missing --output, unresolvable package)
 */
final class ExtractPackageCommand extends Command
{
    /** @var string */
    protected $signature = 'nexus:extract-package
        {--package= : <vendor>/<name>; auto-detected from composer.json if omitted}
        {--output= : Path to write reflection.json (REQUIRED)}
        {--quiet-progress : Suppress per-phase progress output}';

    /** @var string */
    protected $description = 'Extract a reflection.json for a Composer package via Testbench.';

    public function handle(Application $app, JsonWriter $writer): int
    {
        $output = $this->stringOption('output');
        if ($output === null) {
            $this->error('--output is required for nexus:extract-package.');

            return 2;
        }

        $package = $this->resolvePackage($app);
        if ($package === null) {
            return 2;
        }

        $errors = new ErrorCollector;
        $document = new ReflectionDocument($errors);
        $document->setProject($this->describeProject($app));

        $attribution = $this->readAttributionFromComposerJson($app, $package);
        $document->setPackage([
            'vendor' => $package->vendor,
            'name' => $package->name,
            'version' => $package->version,
            'description' => $attribution['description'],
            'authors' => $attribution['authors'],
            'license' => $attribution['license'],
            'homepage' => $attribution['homepage'],
        ]);

        $reporter = new ProgressReporter(
            output: $this->output->getOutput(),
            quiet: (bool) $this->option('quiet-progress'),
        );

        $tracker = new CurrentClassTracker;
        (new FatalErrorHandler($document, $tracker, $writer, $output))->register();

        $context = new ExtractionContext(
            app: $app,
            document: $document,
            errors: $errors,
            progress: $reporter,
            includeVendor: true,
            vendorAllowlist: [$package->fullName()],
            profileHint: null,
            includeTests: false,
            classTracker: $tracker,
            package: $package,
        );

        $pipeline = new ExtractorPipeline($this->buildExtractors());
        $runner = new ExtractionRunner($pipeline, $writer);
        $exit = $runner->run($context, $document, $output);

        if ($exit === 0) {
            // Apply both filters: scope-include (keep only target
            // package's classes) then namespace-exclude (Workbench /
            // Orchestra). Order matters - scope-include is the
            // primary signal, exclude handles the residual noise.
            $document->applyScopeFilter($package);
            $this->applyNamespaceFilter($document);
            $writer->write($document, $output);
            $this->reportSummary($document, $output);
        }

        return $exit;
    }

    private function resolvePackage(Application $app): ?PackageScope
    {
        $flag = $this->stringOption('package');
        $composerPath = $app->basePath('composer.json');

        if (! is_file($composerPath)) {
            $this->error("composer.json not found at $composerPath");

            return null;
        }

        /** @var array<string, mixed> $hostComposer */
        $hostComposer = json_decode((string) file_get_contents($composerPath), associative: true) ?? [];

        $name = $flag ?? (isset($hostComposer['name']) && is_string($hostComposer['name']) ? $hostComposer['name'] : null);
        if (! is_string($name) || ! str_contains($name, '/')) {
            $this->error('Could not resolve package name from --package or composer.json.');

            return null;
        }

        [$vendor, $shortName] = explode('/', $name, 2);
        $rawVendorPath = $app->basePath("vendor/$vendor/$shortName");
        $vendorPath = realpath($rawVendorPath) ?: $rawVendorPath;

        // Prefer reading version and PSR-4 map from the package's own installed
        // composer.json (vendorPath/composer.json). This is more accurate than
        // reading from the host app's composer.json, and it allows the command to
        // run from a generic testbench skeleton host rather than inside the package.
        $pkgComposerPath = rtrim($vendorPath, '/').'/composer.json';
        $pkgComposer = is_file($pkgComposerPath)
            ? (json_decode((string) file_get_contents($pkgComposerPath), associative: true) ?? [])
            : $hostComposer;

        /** @var array<string, mixed> $pkgComposer */
        $version = isset($pkgComposer['version']) && is_string($pkgComposer['version'])
            ? $pkgComposer['version']
            : (isset($hostComposer['version']) && is_string($hostComposer['version']) ? $hostComposer['version'] : 'dev-main');

        /** @var array<string, string> $namespaces */
        $namespaces = isset($pkgComposer['autoload']['psr-4']) && is_array($pkgComposer['autoload']['psr-4'])
            ? $pkgComposer['autoload']['psr-4']
            : [];

        return new PackageScope(
            vendor: $vendor,
            name: $shortName,
            version: $version,
            vendorPath: $vendorPath,
            namespaces: $namespaces,
        );
    }

    /**
     * @return array{description: string|null, authors: list<array<string, string|null>>, license: string|null, homepage: string|null}
     */
    private function readAttributionFromComposerJson(Application $app, PackageScope $package): array
    {
        // Resolution order:
        //   1. vendorPath/composer.json - the package as installed in a
        //      scratch dir's vendor/ (nexus-driven mode).
        //   2. getcwd()/composer.json - the working dir of the subprocess.
        //      For in-repo mode the target package IS the working dir;
        //      $app->basePath() can't be used here because Testbench
        //      bootstraps a Laravel skeleton whose basePath() returns
        //      vendor/orchestra/testbench-core/laravel/ - i.e. Laravel's
        //      own composer.json, which would replace the target's
        //      attribution with "The Laravel Framework." and an empty
        //      authors array.
        //   3. $app->basePath()/composer.json - last-resort fallback
        //      that only resolves correctly when the host happens to be
        //      the target (rare; documented for completeness).
        $candidates = [
            rtrim($package->vendorPath, '/').'/composer.json',
            getcwd() === false ? null : rtrim((string) getcwd(), '/').'/composer.json',
            $app->basePath('composer.json'),
        ];

        $composerPath = null;
        foreach ($candidates as $candidate) {
            if (is_string($candidate) && is_file($candidate)) {
                // Sanity-check: only accept if this composer.json names the
                // target package. Stops us from accidentally reading
                // Laravel's own composer.json (it always exists at
                // $app->basePath() under Testbench).
                $raw = json_decode((string) file_get_contents($candidate), associative: true);
                $name = is_array($raw) && isset($raw['name']) && is_string($raw['name'])
                    ? $raw['name']
                    : '';
                if ($name === $package->vendor.'/'.$package->name) {
                    $composerPath = $candidate;
                    break;
                }
            }
        }

        if ($composerPath === null) {
            return ['description' => null, 'authors' => [], 'license' => null, 'homepage' => null];
        }

        /** @var array<string, mixed> $composer */
        $composer = json_decode((string) file_get_contents($composerPath), associative: true) ?? [];

        $description = isset($composer['description']) && is_string($composer['description'])
            ? $composer['description']
            : null;

        $license = null;
        if (isset($composer['license'])) {
            $raw = $composer['license'];
            if (is_string($raw)) {
                $license = $raw;
            } elseif (is_array($raw)) {
                $license = implode(', ', array_map(static fn ($v) => (string) $v, $raw));
            }
        }

        $homepage = isset($composer['homepage']) && is_string($composer['homepage'])
            ? $composer['homepage']
            : null;

        $authors = [];
        if (isset($composer['authors']) && is_array($composer['authors'])) {
            foreach ($composer['authors'] as $entry) {
                if (! is_array($entry) || ! isset($entry['name']) || ! is_string($entry['name'])) {
                    continue;
                }
                $authors[] = [
                    'name' => $entry['name'],
                    'email' => isset($entry['email']) && is_string($entry['email']) ? $entry['email'] : null,
                    'homepage' => isset($entry['homepage']) && is_string($entry['homepage']) ? $entry['homepage'] : null,
                    'role' => isset($entry['role']) && is_string($entry['role']) ? $entry['role'] : null,
                ];
            }
        }

        return [
            'description' => $description,
            'authors' => $authors,
            'license' => $license,
            'homepage' => $homepage,
        ];
    }

    /**
     * @return list<Extractor>
     */
    private function buildExtractors(): array
    {
        return [
            // Phase A - Runtime registries
            new RouteExtractor,
            new BindingExtractor,
            new EventListenerExtractor,
            new GatePolicyExtractor,
            new MiddlewareExtractor,
            new ConfigExtractor,
            new ScheduleExtractor,
            // Phase B - Class autoload sweep
            new ProjectClassExtractor(new ClassMapWalker),
            // Phase C - AST static analysis
            new StaticAnalysisExtractor,
        ];
    }

    private function applyNamespaceFilter(ReflectionDocument $document): void
    {
        $filter = new NamespaceExclusionFilter;
        $document->applyNamespaceFilter($filter);
    }

    private function stringOption(string $name): ?string
    {
        /** @var mixed $value */
        $value = $this->option($name);

        return is_string($value) && $value !== '' ? $value : null;
    }

    /**
     * @return array<string, mixed>
     */
    private function describeProject(Application $app): array
    {
        return [
            'name' => (string) ($app->make('config')->get('app.name') ?? 'Testbench'),
            'environment' => (string) $app->environment(),
            'laravel_version' => $this->resolveLaravelVersion($app),
            'php_version' => PHP_VERSION,
            'base_path' => $app->basePath(),
            'profile_hint' => null,
        ];
    }

    private function resolveLaravelVersion(Application $app): string
    {
        $class = $app::class;
        if (defined($class.'::VERSION')) {
            /** @var mixed $value */
            $value = constant($class.'::VERSION');

            return is_string($value) ? $value : 'unknown';
        }

        return 'unknown';
    }

    private function reportSummary(ReflectionDocument $document, string $path): void
    {
        $array = $document->toArray();
        $this->info(sprintf('Wrote %s', $path));
        $this->info(sprintf(
            'Sections: %d  Warnings: %d  Errors: %d',
            count($array['sections'] ?? []),
            count($array['warnings'] ?? []),
            count($array['errors'] ?? []),
        ));
    }
}
