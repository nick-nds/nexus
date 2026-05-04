<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction;

use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\ErrorCollector;

/**
 * Contract for a single extractor step.
 *
 * Extractors are pure with respect to the file system: they read from the
 * Laravel application via the {@see ExtractionContext} and write into the
 * {@see ReflectionDocument} bound to that context.
 * They never write directly to disk and never throw past their boundary.
 *
 * Failures are recorded as warnings or errors via the
 * {@see ErrorCollector} on the context, and the
 * pipeline continues to the next extractor.
 */
interface Extractor
{
    /**
     * A short identifier used in progress output and error context.
     */
    public function name(): string;

    /**
     * Run the extractor against the given context. Must not throw.
     */
    public function extract(ExtractionContext $context): void;
}
