<?php

declare(strict_types=1);

namespace Nexus\Extractor\Support;

/**
 * Collects warnings and errors emitted by extractors during a single run.
 *
 * Designed as a tiny mutable accumulator deliberately injected into every
 * extractor. We do NOT use exceptions for partial-failure reporting because
 * the pipeline must continue past one bad file or one missing class.
 */
final class ErrorCollector
{
    /** @var list<ExtractionWarning> */
    private array $warnings = [];

    /** @var list<ExtractionError> */
    private array $errors = [];

    public function warn(ExtractionWarning $warning): void
    {
        $this->warnings[] = $warning;
    }

    public function fail(ExtractionError $error): void
    {
        $this->errors[] = $error;
    }

    /**
     * @return list<ExtractionWarning>
     */
    public function warnings(): array
    {
        return $this->warnings;
    }

    /**
     * @return list<ExtractionError>
     */
    public function errors(): array
    {
        return $this->errors;
    }

    public function hasErrors(): bool
    {
        return $this->errors !== [];
    }

    public function hasWarnings(): bool
    {
        return $this->warnings !== [];
    }

    public function warningCount(): int
    {
        return count($this->warnings);
    }

    public function errorCount(): int
    {
        return count($this->errors);
    }
}
