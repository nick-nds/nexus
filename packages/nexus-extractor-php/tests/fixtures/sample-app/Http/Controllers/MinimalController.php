<?php

declare(strict_types=1);

namespace SampleApp\Http\Controllers;

/**
 * A Laravel 11+ style controller that does NOT extend
 * Illuminate\Routing\Controller. Used to test that ClassClassifier detects
 * controllers by namespace convention, not by base class.
 */
final class MinimalController
{
    public function __invoke(): string
    {
        return 'ok';
    }
}
