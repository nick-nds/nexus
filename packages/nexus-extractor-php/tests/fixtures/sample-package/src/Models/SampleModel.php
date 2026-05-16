<?php

declare(strict_types=1);

namespace NexusFixtures\Sample\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

final class SampleModel extends Model
{
    protected $casts = ['payload' => 'array'];

    public function children(): HasMany
    {
        return $this->hasMany(SampleModel::class, 'parent_id');
    }
}
