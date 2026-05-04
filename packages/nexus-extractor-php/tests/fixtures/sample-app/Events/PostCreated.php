<?php

declare(strict_types=1);

namespace SampleApp\Events;

use Illuminate\Foundation\Events\Dispatchable;
use SampleApp\Models\Post;

final class PostCreated
{
    use Dispatchable;

    public function __construct(public readonly Post $post) {}
}
