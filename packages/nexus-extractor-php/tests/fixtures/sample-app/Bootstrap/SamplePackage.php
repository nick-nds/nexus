<?php

declare(strict_types=1);

namespace SampleApp\Bootstrap;

/**
 * Fixture for audit P2-20: a package entry-point class with a public
 * static ``boot()`` method. Mirrors the shape of ``Synthesq\Relay\Relay``
 * and similar package facades. The classifier should tag this as
 * ``bootstrap`` to distinguish it from generic classes.
 */
class SamplePackage
{
    public static function boot(): void
    {
        // In real code this would register routes, providers,
        // event listeners, etc.
    }

    public static function version(): string
    {
        return '0.0.0';
    }
}
