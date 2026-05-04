<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

/**
 * A single AST finding emitted by a visitor.
 *
 * Findings are intentionally simple value objects: kind tags the type of
 * thing found (event_dispatch, job_dispatch, view_return, validation_rules,
 * authorize), target is the resolved class or string identifier, and meta
 * carries kind-specific extra fields.
 */
final class StaticAnalysisFinding
{
    /**
     * @param  array<string, mixed>  $meta
     */
    public function __construct(
        public readonly string $kind,
        public readonly ?string $target,
        public readonly ?string $contextClass,
        public readonly ?string $contextMethod,
        public readonly ?string $file,
        public readonly ?int $line,
        public readonly array $meta = [],
    ) {}

    /**
     * @return array<string, mixed>
     */
    public function toArray(): array
    {
        return [
            'kind' => $this->kind,
            'target' => $this->target,
            'in_class' => $this->contextClass,
            'in_method' => $this->contextMethod,
            'file' => $this->file,
            'line' => $this->line,
            'meta' => $this->meta,
        ];
    }
}
