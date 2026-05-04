<?php

declare(strict_types=1);

namespace SampleApp\Observers;

use SampleApp\Models\Post;

final class PostObserver
{
    public function created(Post $post): void
    {
        // no-op for fixture
    }

    public function updated(Post $post): void
    {
        // no-op for fixture
    }
}
