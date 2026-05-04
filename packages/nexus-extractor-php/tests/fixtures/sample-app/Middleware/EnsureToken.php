<?php

declare(strict_types=1);

namespace SampleApp\Middleware;

use Closure;
use Illuminate\Http\Request;

final class EnsureToken
{
    public function handle(Request $request, Closure $next): mixed
    {
        return $next($request);
    }
}
