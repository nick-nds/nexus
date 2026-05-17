<?php

declare(strict_types=1);

namespace Nexus\Extractor;

use Illuminate\Support\ServiceProvider;
use Nexus\Extractor\Console\ExtractCommand;
use Nexus\Extractor\Console\ExtractPackageCommand;

/**
 * Registers the Nexus extractor's Artisan command.
 *
 * The provider is auto-discovered via composer.json's `extra.laravel.providers`.
 * It registers no bindings beyond the command itself; every extractor receives
 * its collaborators directly when the pipeline is constructed inside the
 * command, keeping the container free of stateful Nexus singletons.
 */
final class NexusExtractorServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        // No container bindings. The command builds its own dependency graph
        // so that the package is trivially testable without service rebinding.
    }

    public function boot(): void
    {
        if ($this->app->runningInConsole()) {
            $this->commands([
                ExtractCommand::class,
                ExtractPackageCommand::class,
            ]);
        }
    }
}
