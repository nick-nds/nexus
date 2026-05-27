<?php

declare(strict_types=1);

namespace Nexus\Extractor\Console;

use Illuminate\Console\Command;
use Illuminate\Contracts\Config\Repository;
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
use Nexus\Extractor\Output\JsonWriter;
use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\CurrentClassTracker;
use Nexus\Extractor\Support\ErrorCollector;
use Nexus\Extractor\Support\FatalErrorHandler;
use Nexus\Extractor\Support\ProgressReporter;

/**
 * Artisan command that runs the full Nexus extraction pipeline.
 *
 * This is the only public entry point of the package. It builds the pipeline
 * with explicit constructor wiring (no service container singletons), runs
 * it, writes the resulting reflection.json, and maps internal state to a
 * meaningful exit code:
 *
 *   0 - success (warnings allowed)
 *   1 - fatal error (boot failure, write failure, structured ExtractionError)
 *   2 - usage error (invalid options)
 */
final class ExtractCommand extends Command
{
    /** @var string */
    protected $signature = 'nexus:extract
        {--output= : Path to write reflection.json (defaults to storage/app/nexus/reflection.json)}
        {--include-vendor : Include all vendor classes in the class sweep}
        {--vendor-allowlist=* : Composer package names to include from vendor (repeatable)}
        {--include-tests : Include the project\'s tests/ classes in the sweep}
        {--profile= : Optional profile hint passed through to the document}
        {--quiet-progress : Suppress per-phase progress output}';

    /** @var string */
    protected $description = 'Extract a Laravel reflection.json document for the Nexus code intelligence tool.';

    public function handle(Application $app, JsonWriter $writer): int
    {
        $output = $this->resolveOutputPath($app);
        if ($output === null) {
            $this->error('Invalid --output path.');

            return 2;
        }

        $vendorAllowlist = $this->resolveVendorAllowlist();

        $errors = new ErrorCollector;
        $document = new ReflectionDocument($errors);
        $document->setProject($this->describeProject($app));

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
            includeVendor: (bool) $this->option('include-vendor'),
            vendorAllowlist: $vendorAllowlist,
            profileHint: $this->stringOption('profile'),
            includeTests: (bool) $this->option('include-tests'),
            classTracker: $tracker,
        );

        $pipeline = new ExtractorPipeline($this->buildExtractors());
        $runner = new ExtractionRunner($pipeline, $writer);

        $exit = $runner->run($context, $document, $output);

        if ($exit === 0 || $errors->hasErrors()) {
            $this->reportSummary($document, $output);
        } else {
            $this->error('Failed to write reflection document.');
        }

        return $exit;
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

    private function resolveOutputPath(Application $app): ?string
    {
        $option = $this->stringOption('output');

        if ($option === null) {
            /** @var string $storagePath */
            $storagePath = $app->storagePath('app/nexus/reflection.json');

            return $storagePath;
        }

        if (trim($option) === '') {
            return null;
        }

        return $option;
    }

    /**
     * @return list<string>
     */
    private function resolveVendorAllowlist(): array
    {
        /** @var array<int, string>|string|null $raw */
        $raw = $this->option('vendor-allowlist');

        if ($raw === null) {
            return [];
        }

        if (is_string($raw)) {
            return [$raw];
        }

        return array_values(array_filter(
            array_map(static fn ($v) => (string) $v, $raw),
            static fn (string $v): bool => $v !== '',
        ));
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
        $config = $app->make('config');
        $appName = $config instanceof Repository
            ? (string) ($config->get('app.name') ?? 'Laravel')
            : 'Laravel';

        return [
            'name' => $appName,
            'environment' => (string) $app->environment(),
            'laravel_version' => $this->resolveLaravelVersion($app),
            'php_version' => PHP_VERSION,
            'base_path' => $app->basePath(),
            'profile_hint' => $this->stringOption('profile'),
        ];
    }

    private function resolveLaravelVersion(Application $app): string
    {
        // Application::VERSION lives on the concrete Foundation\Application
        // class, not on the contract. Read it via reflection to keep PHPStan
        // happy and to avoid coupling to a specific concrete class name.
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
        $errors = $document->errors();
        $this->info(sprintf('Wrote %s', $path));
        $this->info(sprintf(
            'Sections: %d  Warnings: %d  Errors: %d',
            count($document->toArray()['sections'] ?? []),
            $errors->warningCount(),
            $errors->errorCount(),
        ));
    }
}
