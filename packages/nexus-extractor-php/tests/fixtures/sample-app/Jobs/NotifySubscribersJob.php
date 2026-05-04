<?php

declare(strict_types=1);

namespace SampleApp\Jobs;

use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use SampleApp\Models\Post;

final class NotifySubscribersJob implements ShouldQueue
{
    use Dispatchable, Queueable;

    public function __construct(public readonly Post $post) {}

    public function handle(): void
    {
        // no-op for fixture
    }
}
