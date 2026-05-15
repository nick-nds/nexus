<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction;

use Illuminate\Contracts\Foundation\Application;
use Nexus\Extractor\Extraction\Support\PackageScope;
use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\CurrentClassTracker;
use Nexus\Extractor\Support\ErrorCollector;
use Nexus\Extractor\Support\ProgressReporter;

/**
 * Mutable context threaded through every extractor in a single run.
 *
 * The context is intentionally explicit (no globals, no facades inside
 * extractors) so that every extractor's behaviour is determined by its
 * constructor arguments and the context object alone. This is what makes the
 * extractors trivially unit-testable with a small fake application.
 */
final class ExtractionContext
{
    /**
     * @param  list<string>  $vendorAllowlist
     */
    public function __construct(
        public readonly Application $app,
        public readonly ReflectionDocument $document,
        public readonly ErrorCollector $errors,
        public readonly ProgressReporter $progress,
        public readonly bool $includeVendor,
        public readonly array $vendorAllowlist,
        public readonly ?string $profileHint,
        public readonly bool $includeTests = false,
        public readonly ?CurrentClassTracker $classTracker = null,
        public readonly ?PackageScope $package = null,
    ) {}
}
