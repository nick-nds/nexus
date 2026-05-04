<?php

declare(strict_types=1);

namespace Nexus\Extractor\Support;

/**
 * Tiny mutable holder for the class Phase B is currently about to load.
 *
 * This exists only so that the {@see FatalErrorHandler} registered at the
 * start of a run can, on shutdown, look up which class was being loaded
 * when a fatal error killed the process. PHP fatal errors during class
 * declaration are not catchable via try/catch; the shutdown handler is
 * the only hook we have.
 *
 * A cleaner architecture would pass the class name through exception
 * objects, but exceptions are bypassed entirely by fatal errors. A mutable
 * pointer is the least-bad mechanism available.
 */
final class CurrentClassTracker
{
    private ?string $current = null;

    public function set(?string $class): void
    {
        $this->current = $class;
    }

    public function current(): ?string
    {
        return $this->current;
    }

    public function clear(): void
    {
        $this->current = null;
    }
}
