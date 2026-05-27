<?php

declare(strict_types=1);

namespace NexusFixtures\Sample;

use Illuminate\Console\Scheduling\Schedule;
use Illuminate\Contracts\Events\Dispatcher;
use Illuminate\Support\ServiceProvider;
use NexusFixtures\Sample\Console\Commands\SampleCommand;
use NexusFixtures\Sample\Contracts\SampleService;
use NexusFixtures\Sample\Events\SampleEvent;
use NexusFixtures\Sample\Listeners\SampleListener;
use NexusFixtures\Sample\Services\DefaultSampleService;

final class SamplePackageServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        $this->app->bind(SampleService::class, DefaultSampleService::class);
    }

    public function boot(Dispatcher $events, Schedule $schedule): void
    {
        $this->loadRoutesFrom(__DIR__.'/../routes/package.php');

        $events->listen(SampleEvent::class, SampleListener::class);

        $schedule->command(SampleCommand::class)->daily();

        if ($this->app->runningInConsole()) {
            $this->commands([SampleCommand::class]);
        }
    }
}
