<?php

declare(strict_types=1);

namespace NexusFixtures\Sample\Http\Controllers;

use NexusFixtures\Sample\Contracts\SampleService;

final class SampleController
{
    public function __construct(private readonly SampleService $service) {}

    public function show(): array
    {
        return ['result' => $this->service->process('hello')];
    }
}
