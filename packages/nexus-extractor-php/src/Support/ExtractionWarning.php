<?php

declare(strict_types=1);

namespace Nexus\Extractor\Support;

/**
 * A non-fatal problem encountered during extraction.
 *
 * Warnings never abort the run; they are collected and emitted in the
 * reflection document so the Python pipeline can surface them to the user.
 */
final class ExtractionWarning
{
    /**
     * @param  array<string, scalar|array<mixed>|null>  $context
     */
    public function __construct(
        public readonly string $code,
        public readonly string $message,
        public readonly ?string $file = null,
        public readonly ?int $line = null,
        public readonly array $context = [],
    ) {}

    /**
     * @return array<string, mixed>
     */
    public function toArray(): array
    {
        return [
            'code' => $this->code,
            'message' => $this->message,
            'file' => $this->file,
            'line' => $this->line,
            'context' => $this->context,
        ];
    }
}
