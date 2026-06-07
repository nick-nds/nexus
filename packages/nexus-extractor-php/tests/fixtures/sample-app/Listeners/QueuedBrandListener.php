<?php

declare(strict_types=1);

namespace SampleApp\Listeners;

use Illuminate\Contracts\Queue\ShouldQueue;
use SampleApp\Events\BrandCreated;

final class QueuedBrandListener implements ShouldQueue
{
    public function handle(BrandCreated $event): void
    {
        // no-op for fixture
    }
}
