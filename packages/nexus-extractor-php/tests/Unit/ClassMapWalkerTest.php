<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseB\ClassMapWalker;
use PHPUnit\Framework\TestCase;

/**
 * ClassMapWalker is tightly coupled to a Composer autoload.php structure.
 * These tests build a minimal fake project layout and a stubbed
 * vendor/autoload.php that returns a ClassLoader with a hand-crafted
 * class map.
 */
final class ClassMapWalkerTest extends TestCase
{
    private string $tmpDir;

    protected function setUp(): void
    {
        $this->tmpDir = sys_get_temp_dir().'/nexus-classmap-'.uniqid('', true);
        mkdir($this->tmpDir.'/app/Models', 0o755, true);
        mkdir($this->tmpDir.'/tests/Unit', 0o755, true);
        mkdir($this->tmpDir.'/vendor/composer', 0o755, true);
        mkdir($this->tmpDir.'/vendor/acme/lib/src', 0o755, true);

        // Create files so realpath() resolves. Contents don't matter - the
        // walker never requires them; it only maps class => file path.
        file_put_contents($this->tmpDir.'/app/Models/User.php', '<?php');
        file_put_contents($this->tmpDir.'/tests/Unit/UserTest.php', '<?php');
        file_put_contents($this->tmpDir.'/vendor/acme/lib/src/Thing.php', '<?php');

        // Build a minimal vendor/autoload.php that returns a ClassLoader
        // configured with our class map. Composer's own ClassLoader is
        // already on the include path of this test suite via our composer
        // dev dependencies, so we can import it directly.
        $classMapPhp = var_export([
            'App\\Models\\User' => $this->tmpDir.'/app/Models/User.php',
            'Tests\\Unit\\UserTest' => $this->tmpDir.'/tests/Unit/UserTest.php',
            'Acme\\Lib\\Thing' => $this->tmpDir.'/vendor/acme/lib/src/Thing.php',
        ], true);

        $autoloadSrc = <<<PHP
        <?php
        \$loader = new \Composer\Autoload\ClassLoader();
        \$loader->addClassMap({$classMapPhp});
        return \$loader;
        PHP;

        file_put_contents($this->tmpDir.'/vendor/autoload.php', $autoloadSrc);
    }

    protected function tearDown(): void
    {
        $this->rmdirRecursive($this->tmpDir);
    }

    public function test_returns_only_project_classes_by_default(): void
    {
        $result = (new ClassMapWalker)->walk($this->tmpDir, includeVendor: false, vendorAllowlist: []);

        $classes = array_map(static fn (array $e): string => $e['class'], $result);

        $this->assertContains('App\\Models\\User', $classes);
        $this->assertNotContains('Acme\\Lib\\Thing', $classes);
    }

    public function test_skips_tests_directory_by_default(): void
    {
        $result = (new ClassMapWalker)->walk($this->tmpDir, includeVendor: false, vendorAllowlist: []);

        $classes = array_map(static fn (array $e): string => $e['class'], $result);

        $this->assertNotContains('Tests\\Unit\\UserTest', $classes);
    }

    public function test_include_tests_flag_restores_tests_classes(): void
    {
        $result = (new ClassMapWalker)->walk(
            $this->tmpDir,
            includeVendor: false,
            vendorAllowlist: [],
            includeTests: true,
        );

        $classes = array_map(static fn (array $e): string => $e['class'], $result);

        $this->assertContains('Tests\\Unit\\UserTest', $classes);
    }

    public function test_include_vendor_flag_pulls_in_vendor_classes(): void
    {
        $result = (new ClassMapWalker)->walk(
            $this->tmpDir,
            includeVendor: true,
            vendorAllowlist: [],
        );

        $classes = array_map(static fn (array $e): string => $e['class'], $result);
        $sources = array_map(static fn (array $e): string => $e['source'], $result);

        $this->assertContains('Acme\\Lib\\Thing', $classes);
        $this->assertContains('vendor', $sources);
    }

    public function test_vendor_allowlist_includes_selected_package_only(): void
    {
        $result = (new ClassMapWalker)->walk(
            $this->tmpDir,
            includeVendor: false,
            vendorAllowlist: ['acme/lib'],
        );

        $classes = array_map(static fn (array $e): string => $e['class'], $result);
        $this->assertContains('Acme\\Lib\\Thing', $classes);
    }

    public function test_returns_deterministic_ordering(): void
    {
        $first = (new ClassMapWalker)->walk($this->tmpDir, includeVendor: true, vendorAllowlist: []);
        $second = (new ClassMapWalker)->walk($this->tmpDir, includeVendor: true, vendorAllowlist: []);

        $this->assertSame($first, $second);
    }

    public function test_returns_empty_when_no_autoload_file(): void
    {
        $bare = sys_get_temp_dir().'/nexus-bare-'.uniqid('', true);
        mkdir($bare);

        $result = (new ClassMapWalker)->walk($bare, includeVendor: false, vendorAllowlist: []);

        $this->assertSame([], $result);

        @rmdir($bare);
    }

    public function test_skips_classes_in_a_packages_tests_fixtures_directory(): void
    {
        // Simulates the path-repository-with-symlink case: a package's
        // dev-autoload registers a fixture namespace pointing into its
        // own ``tests/fixtures/`` directory. Composer's classmap stores
        // the resolved real path (outside the consumer's ``vendor/``),
        // so the original vendor check misses it. The defensive
        // ``/tests/fixtures/`` filter must catch it regardless.
        $upstream = sys_get_temp_dir().'/nexus-pkg-'.uniqid('', true);
        mkdir($upstream.'/tests/fixtures/sample-app/Http/Controllers', 0o755, true);
        $fixtureFile = $upstream.'/tests/fixtures/sample-app/Http/Controllers/PostController.php';
        file_put_contents($fixtureFile, '<?php');

        // Append the fake fixture class to the autoload classmap.
        $classMapPhp = var_export([
            'App\\Models\\User' => $this->tmpDir.'/app/Models/User.php',
            'SampleApp\\Http\\Controllers\\PostController' => $fixtureFile,
        ], true);
        $autoloadSrc = <<<PHP
        <?php
        \$loader = new \Composer\Autoload\ClassLoader();
        \$loader->addClassMap({$classMapPhp});
        return \$loader;
        PHP;
        file_put_contents($this->tmpDir.'/vendor/autoload.php', $autoloadSrc);

        $result = (new ClassMapWalker)->walk($this->tmpDir, includeVendor: false, vendorAllowlist: []);
        $classes = array_map(static fn (array $e): string => $e['class'], $result);

        $this->assertContains('App\\Models\\User', $classes, 'project class still indexed');
        $this->assertNotContains(
            'SampleApp\\Http\\Controllers\\PostController',
            $classes,
            'a package’s test-fixture class must not leak into the consumer’s index',
        );

        $this->rmdirRecursive($upstream);
    }

    public function test_include_tests_lets_fixture_classes_through(): void
    {
        // The defensive ``/tests/fixtures/`` filter is also gated on
        // ``$includeTests``; explicit opt-in restores the fixture so
        // a maintainer running ``--include-tests`` against the
        // package itself can still index its test scaffolding.
        $upstream = sys_get_temp_dir().'/nexus-pkg-'.uniqid('', true);
        mkdir($upstream.'/tests/fixtures/sample-app', 0o755, true);
        $fixtureFile = $upstream.'/tests/fixtures/sample-app/Foo.php';
        file_put_contents($fixtureFile, '<?php');

        $classMapPhp = var_export([
            'SampleApp\\Foo' => $fixtureFile,
        ], true);
        $autoloadSrc = <<<PHP
        <?php
        \$loader = new \Composer\Autoload\ClassLoader();
        \$loader->addClassMap({$classMapPhp});
        return \$loader;
        PHP;
        file_put_contents($this->tmpDir.'/vendor/autoload.php', $autoloadSrc);

        $result = (new ClassMapWalker)->walk(
            $this->tmpDir,
            includeVendor: false,
            vendorAllowlist: [],
            includeTests: true,
        );
        $classes = array_map(static fn (array $e): string => $e['class'], $result);

        $this->assertContains('SampleApp\\Foo', $classes);

        $this->rmdirRecursive($upstream);
    }

    private function rmdirRecursive(string $dir): void
    {
        if (! is_dir($dir)) {
            return;
        }
        foreach (scandir($dir) ?: [] as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $path = $dir.'/'.$entry;
            is_dir($path) ? $this->rmdirRecursive($path) : @unlink($path);
        }
        @rmdir($dir);
    }
}
