<?php

declare(strict_types=1);

namespace NexusFixtures\Sample\Policies;

use NexusFixtures\Sample\Models\SampleModel;

final class SampleModelPolicy
{
    public function view(mixed $user, SampleModel $model): bool
    {
        return true;
    }

    public function update(mixed $user, SampleModel $model): bool
    {
        return false;
    }
}
