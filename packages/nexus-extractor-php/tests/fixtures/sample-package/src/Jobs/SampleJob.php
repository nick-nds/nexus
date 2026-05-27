<?php

declare(strict_types=1);

namespace NexusFixtures\Sample\Jobs;

use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;

final class SampleJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable;

    public function __construct(public readonly string $payload) {}

    public function handle(): void
    {
        // no-op
    }
}
