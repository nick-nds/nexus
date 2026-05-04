<?php

declare(strict_types=1);

namespace SampleApp\Policies;

use SampleApp\Models\Post;
use SampleApp\Models\User;

final class PostPolicy
{
    public function view(User $user, Post $post): bool
    {
        return true;
    }

    public function update(User $user, Post $post): bool
    {
        return $user->getKey() === $post->getAttribute('user_id');
    }
}
