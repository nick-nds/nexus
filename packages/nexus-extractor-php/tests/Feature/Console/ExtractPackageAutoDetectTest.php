<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Feature\Console;

use Illuminate\Foundation\Application;
use Nexus\Extractor\NexusExtractorServiceProvider;
use Nexus\Extractor\Tests\TestCase;
use NexusFixtures\Sample\SamplePackageServiceProvider;

/**
 * Tests the auto-detect path of nexus:extract-package (no --package flag).
 *
 * The application base path is overridden to the installed nexus-fixtures/sample
 * directory so that $app->basePath('composer.json') resolves to the fixture's
 * own composer.json containing `"name": "nexus-fixtures/sample"`. This simulates
 * running the command directly inside a package's own environment (e.g. via
 * `vendor/bin/testbench nexus:extract-package`).
 *
 * Class extraction is expected to be empty (the fixture has no vendor/autoload.php)
 * but the document metadata is fully resolved from the fixture's composer.json.
 */
final class ExtractPackageAutoDetectTest extends TestCase
{
    /**
     * Point the app base path at the installed fixture so the command's
     * auto-detect reads the fixture's composer.json.
     */
    public static function applicationBasePath(): string
    {
        return dirname(__DIR__, 3).'/vendor/nexus-fixtures/sample';
    }

    /**
     * @param  Application  $app
     * @return list<class-string>
     */
    protected function getPackageProviders($app): array
    {
        return [
            NexusExtractorServiceProvider::class,
            SamplePackageServiceProvider::class,
        ];
    }

    public function test_auto_detects_package_from_composer_json_when_flag_omitted(): void
    {
        $tmp = tempnam(sys_get_temp_dir(), 'nexus-pkg-').'.json';
        $this->artisan('nexus:extract-package', [
            '--output' => $tmp,
            '--quiet-progress' => true,
        ])->assertExitCode(0);

        /** @var string $raw */
        $raw = file_get_contents($tmp);
        /** @var array<string, mixed> $doc */
        $doc = json_decode($raw, associative: true);
        $this->assertSame(
            'nexus-fixtures/sample',
            $doc['package']['vendor'].'/'.$doc['package']['name'],
        );

        @unlink($tmp);
    }
}
