<?php

declare(strict_types=1);

namespace SampleApp\Listeners;

use SampleApp\Events\BrandCreated;

final class SyncBrandListener
{
    public function handle(BrandCreated $event): void
    {
        // no-op for fixture
    }
}
