<?php

declare(strict_types=1);

namespace SampleApp\Models;

use Illuminate\Database\Eloquent\Model;

final class Post extends Model
{
    protected $fillable = ['title', 'body'];
}
