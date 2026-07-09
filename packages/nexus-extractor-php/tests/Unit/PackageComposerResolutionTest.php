<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Console\ExtractPackageCommand;
use PHPUnit\Framework\TestCase;

/**
 * Package scope resolution: which composer.json the extractor reads to
 * derive the package's PSR-4 map and source directory.
 *
 * The in-repo bug: a self-developed package checkout is NOT installed
 * under its own ``vendor/<vendor>/<name>``, so the extractor fell back
 * to the Testbench skeleton's composer.json and scoped extraction to the
 * wrong tree (0 classes). Resolution must fall back to the working
 * directory (the checkout) when it names the target package.
 */
final class PackageComposerResolutionTest extends TestCase
{
    private string $tmp;

    protected function setUp(): void
    {
        $this->tmp = sys_get_temp_dir().'/nexus-scope-'.uniqid();
        mkdir($this->tmp, 0o777, true);
    }

    protected function tearDown(): void
    {
        $this->rrmdir($this->tmp);
    }

    private function writeComposer(string $dir, ?string $name): string
    {
        mkdir($dir, 0o777, true);
        file_put_contents(
            $dir.'/composer.json',
            (string) json_encode($name === null ? [] : ['name' => $name]),
        );

        return $dir;
    }

    public function test_picks_vendor_install_when_name_matches(): void
    {
        // nexus-driven: the package IS installed under vendor/.
        $vendor = $this->writeComposer($this->tmp.'/vendor/acme/foo', 'acme/foo');

        $r = ExtractPackageCommand::selectPackageComposer([$vendor, $this->tmp.'/cwd', null], 'acme/foo');

        $this->assertNotNull($r);
        $this->assertSame(realpath($vendor), $r['dir']);
        $this->assertSame('acme/foo', $r['composer']['name']);
    }

    public function test_falls_back_to_cwd_when_not_vendored(): void
    {
        // in-repo self-developed: vendor/<vendor>/<name> does NOT exist,
        // but the working directory IS the package checkout.
        $missingVendor = $this->tmp.'/pkg/vendor/acme/foo';
        $cwd = $this->writeComposer($this->tmp.'/pkg', 'acme/foo');

        $r = ExtractPackageCommand::selectPackageComposer([$missingVendor, $cwd], 'acme/foo');

        $this->assertNotNull($r);
        $this->assertSame(realpath($cwd), $r['dir']);
    }

    public function test_name_guard_skips_non_matching_composer(): void
    {
        // The Testbench skeleton's composer.json (laravel/framework) must
        // never be accepted for a different target package.
        $skeleton = $this->writeComposer($this->tmp.'/skeleton', 'laravel/framework');

        $r = ExtractPackageCommand::selectPackageComposer([$this->tmp.'/nope', $skeleton], 'acme/foo');

        $this->assertNull($r);
    }

    public function test_returns_null_when_no_candidate_matches(): void
    {
        $r = ExtractPackageCommand::selectPackageComposer([$this->tmp.'/nope', null], 'acme/foo');

        $this->assertNull($r);
    }

    private function rrmdir(string $dir): void
    {
        if (! is_dir($dir)) {
            return;
        }
        foreach (scandir($dir) ?: [] as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $path = $dir.'/'.$entry;
            is_dir($path) ? $this->rrmdir($path) : @unlink($path);
        }
        @rmdir($dir);
    }
}
