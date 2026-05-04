<?php

declare(strict_types=1);

namespace SampleApp\Listeners;

use SampleApp\Events\PostCreated;

final class SendPostCreatedEmail
{
    public function handle(PostCreated $event): void
    {
        // no-op for fixture
    }
}
