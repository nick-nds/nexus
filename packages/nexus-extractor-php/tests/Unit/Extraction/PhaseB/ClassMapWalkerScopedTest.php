<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit\Extraction\PhaseB;

use Nexus\Extractor\Extraction\PhaseB\ClassMapWalker;
use Nexus\Extractor\Extraction\Support\PackageScope;
use PHPUnit\Framework\TestCase;

/**
 * Verifies that ClassMapWalker filters to scope.vendorPath when a PackageScope
 * is supplied, and that existing behaviour is unchanged when scope is null.
 *
 * Follows the same temp-filesystem pattern as ClassMapWalkerTest: we build a
 * minimal fake project layout with a stub vendor/autoload.php that returns a
 * ClassLoader configured with a hand-crafted classmap.
 */
final class ClassMapWalkerScopedTest extends TestCase
{
    private string $tmpDir;

    protected function setUp(): void
    {
        $this->tmpDir = sys_get_temp_dir().'/nexus-walker-scoped-'.uniqid('', true);

        // Package source files (the package we are indexing)
        mkdir($this->tmpDir.'/vendor/spatie/laravel-permission/src', 0o755, true);
        file_put_contents(
            $this->tmpDir.'/vendor/spatie/laravel-permission/src/Role.php',
            '<?php',
        );
        file_put_contents(
            $this->tmpDir.'/vendor/spatie/laravel-permission/src/Permission.php',
            '<?php',
        );

        // A different vendor package (should be excluded when scope is set)
        mkdir($this->tmpDir.'/vendor/laravel/framework/src/Illuminate/Database', 0o755, true);
        file_put_contents(
            $this->tmpDir.'/vendor/laravel/framework/src/Illuminate/Database/Model.php',
            '<?php',
        );

        // A project class (should be excluded when scope is set)
        mkdir($this->tmpDir.'/app', 0o755, true);
        file_put_contents($this->tmpDir.'/app/Foo.php', '<?php');

        // Composer vendor/composer stub so the loader can be instantiated
        mkdir($this->tmpDir.'/vendor/composer', 0o755, true);

        $classMapPhp = var_export([
            'Spatie\\Permission\\Role' => $this->tmpDir.'/vendor/spatie/laravel-permission/src/Role.php',
            'Spatie\\Permission\\Permission' => $this->tmpDir.'/vendor/spatie/laravel-permission/src/Permission.php',
            'Illuminate\\Database\\Model' => $this->tmpDir.'/vendor/laravel/framework/src/Illuminate/Database/Model.php',
            'App\\Foo' => $this->tmpDir.'/app/Foo.php',
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

    public function test_when_scope_set_only_classes_under_vendor_path_are_yielded(): void
    {
        $scope = new PackageScope(
            vendor: 'spatie',
            name: 'laravel-permission',
            version: 'v6.18.0',
            vendorPath: $this->tmpDir.'/vendor/spatie/laravel-permission',
            namespaces: ['Spatie\\Permission\\' => 'src/'],
        );

        $results = (new ClassMapWalker)->walk(
            basePath: $this->tmpDir,
            includeVendor: false,
            vendorAllowlist: [],
            includeTests: false,
            scope: $scope,
        );

        $this->assertCount(2, $results);
        $this->assertContains('Spatie\\Permission\\Role', array_column($results, 'class'));
        $this->assertContains('Spatie\\Permission\\Permission', array_column($results, 'class'));
    }

    public function test_when_scope_null_existing_behavior_unchanged(): void
    {
        // With scope null and includeVendor false, only project classes come through.
        $results = (new ClassMapWalker)->walk(
            basePath: $this->tmpDir,
            includeVendor: false,
            vendorAllowlist: [],
            includeTests: false,
            scope: null,
        );

        $classes = array_column($results, 'class');
        $this->assertCount(1, $results);
        $this->assertContains('App\\Foo', $classes);
    }

    // -------------------------------------------------------------------------

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
