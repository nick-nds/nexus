<?php

declare(strict_types=1);

namespace NexusFixtures\Sample\Contracts;

interface SampleService
{
    public function process(string $input): string;
}
