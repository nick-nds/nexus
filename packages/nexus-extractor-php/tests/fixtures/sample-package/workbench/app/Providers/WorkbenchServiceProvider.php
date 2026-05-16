<?php

declare(strict_types=1);

namespace Workbench\App\Providers;

use Illuminate\Support\Facades\Route;
use Illuminate\Support\ServiceProvider;

final class WorkbenchServiceProvider extends ServiceProvider
{
    public function boot(): void
    {
        Route::get('/workbench-fixture', fn () => 'fixture')->name('workbench.fixture');
    }
}
