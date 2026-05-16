<?php

declare(strict_types=1);

namespace NexusFixtures\Sample\Events;

final class SampleEvent
{
    public function __construct(public readonly string $payload) {}
}
