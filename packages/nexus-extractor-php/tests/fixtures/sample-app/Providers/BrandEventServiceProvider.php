<?php

declare(strict_types=1);

namespace SampleApp\Providers;

use Illuminate\Foundation\Support\Providers\EventServiceProvider;
use SampleApp\Events\BrandCreated;
use SampleApp\Listeners\SyncBrandListener;

/**
 * Registers SyncBrandListener via the explicit $listen map so the
 * extractor can tag it source="listen". QueuedBrandListener is wired
 * separately (Event::listen) in the test to exercise source="discovered".
 */
final class BrandEventServiceProvider extends EventServiceProvider
{
    /**
     * @var array<class-string, list<class-string>>
     */
    protected $listen = [
        BrandCreated::class => [
            SyncBrandListener::class,
        ],
    ];
}
