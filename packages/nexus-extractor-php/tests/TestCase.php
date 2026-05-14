<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests;

use Illuminate\Foundation\Application;
use Nexus\Extractor\NexusExtractorServiceProvider;
use Orchestra\Testbench\TestCase as OrchestraTestCase;

/**
 * Base TestCase for tests that need a real Laravel Application instance.
 */
abstract class TestCase extends OrchestraTestCase
{
    /**
     * @param  Application  $app
     * @return list<class-string>
     */
    protected function getPackageProviders($app): array
    {
        return [NexusExtractorServiceProvider::class];
    }
}
