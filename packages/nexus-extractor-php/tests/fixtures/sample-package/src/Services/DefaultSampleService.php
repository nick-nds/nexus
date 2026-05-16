<?php

declare(strict_types=1);

namespace NexusFixtures\Sample\Services;

use NexusFixtures\Sample\Contracts\SampleService;

final class DefaultSampleService implements SampleService
{
    public function process(string $input): string
    {
        return strtoupper($input);
    }
}
