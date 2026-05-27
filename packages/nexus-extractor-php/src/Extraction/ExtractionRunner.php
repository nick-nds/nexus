<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction;

use Nexus\Extractor\Output\JsonWriter;
use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\ExtractionError;
use Throwable;

/**
 * Pure-data runner for the extraction pipeline. Both ExtractCommand and
 * ExtractPackageCommand consume it; the runner owns no console I/O of
 * its own - progress reporting is on the ExtractionContext.
 *
 * Returns an exit code:
 *   0 - success (warnings allowed)
 *   1 - fatal error (pipeline crash, write failure, structured ExtractionError)
 */
final class ExtractionRunner
{
    public function __construct(
        private readonly ExtractorPipeline $pipeline,
        private readonly JsonWriter $writer,
    ) {}

    public function run(ExtractionContext $context, ReflectionDocument $document, string $outputPath): int
    {
        $errors = $context->errors;

        try {
            $this->pipeline->run($context);
        } catch (Throwable $e) {
            $errors->fail(new ExtractionError(
                code: 'pipeline_failed',
                message: $e->getMessage(),
                file: $e->getFile(),
                line: $e->getLine(),
            ));
        }

        try {
            $this->writer->write($document, $outputPath);
        } catch (Throwable) {
            return 1;
        }

        return $errors->hasErrors() ? 1 : 0;
    }
}
