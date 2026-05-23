<?php

declare(strict_types=1);

namespace SampleApp\Concerns;

/**
 * Fixture for audit P0-1 (trait variant): a ``trait`` declaration.
 * The classifier should tag this as ``trait``, not ``abstract``.
 */
trait HasTimestamps
{
    public function touch(): void
    {
        // no-op
    }
}
