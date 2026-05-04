<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction;

use Nexus\Extractor\Support\ExtractionWarning;
use Throwable;

/**
 * Sequentially runs a list of {@see Extractor} steps against a context.
 *
 * The pipeline is deliberately tiny: it has no DAG, no parallelism, no
 * conditional steps. Every step runs in order; a thrown exception inside a
 * step is caught and recorded as a warning so that one buggy extractor never
 * aborts the whole run.
 */
final class ExtractorPipeline
{
    /**
     * @param  list<Extractor>  $extractors
     */
    public function __construct(
        private readonly array $extractors,
    ) {}

    public function run(ExtractionContext $context): void
    {
        foreach ($this->extractors as $extractor) {
            $name = $extractor->name();
            $context->progress->step($name, 'starting');

            try {
                $extractor->extract($context);
            } catch (Throwable $e) {
                $context->errors->warn(new ExtractionWarning(
                    code: 'extractor_threw',
                    message: sprintf('Extractor %s threw: %s', $name, $e->getMessage()),
                    file: $e->getFile(),
                    line: $e->getLine(),
                    context: ['extractor' => $name, 'exception' => $e::class],
                ));
                $context->progress->warn(sprintf('%s failed: %s', $name, $e->getMessage()));

                continue;
            }

            $context->progress->step($name, 'done');
        }
    }
}
