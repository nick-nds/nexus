<?php

declare(strict_types=1);

namespace SampleApp\Contracts;

/**
 * Fixture for audit P0-1: a plain ``interface`` declaration. The
 * classifier should tag this as ``interface``, not ``abstract``.
 */
interface Transformer
{
    public function transform(mixed $input): array;
}
