<?php

declare(strict_types=1);

namespace NexusFixtures\Sample\Listeners;

use NexusFixtures\Sample\Events\SampleEvent;
use NexusFixtures\Sample\Jobs\SampleJob;

final class SampleListener
{
    public function handle(SampleEvent $event): void
    {
        SampleJob::dispatch($event->payload);
    }
}
