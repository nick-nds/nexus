<?php

declare(strict_types=1);

namespace Nexus\Extractor\Support;

/**
 * A fatal problem that prevented a phase from running to completion.
 *
 * Errors are recorded in the document and cause the command to exit with a
 * non-zero status code. Use {@see ExtractionWarning} for partial-failure cases.
 */
final class ExtractionError
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
