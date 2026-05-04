<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseC\FileScanner;
use PHPUnit\Framework\TestCase;

final class FileScannerTest extends TestCase
{
    private string $tmpDir;

    protected function setUp(): void
    {
        $this->tmpDir = sys_get_temp_dir().'/nexus-scan-'.uniqid('', true);
        mkdir($this->tmpDir.'/app/Http', 0o755, true);
        mkdir($this->tmpDir.'/vendor/whatever', 0o755, true);
        mkdir($this->tmpDir.'/storage/logs', 0o755, true);
        mkdir($this->tmpDir.'/node_modules/x', 0o755, true);

        file_put_contents($this->tmpDir.'/app/Http/Foo.php', '<?php class Foo {}');
        file_put_contents($this->tmpDir.'/app/Bar.php', '<?php class Bar {}');
        file_put_contents($this->tmpDir.'/app/note.txt', 'plain');
        file_put_contents($this->tmpDir.'/vendor/whatever/Inside.php', '<?php class Inside {}');
        file_put_contents($this->tmpDir.'/storage/logs/log.php', '<?php');
        file_put_contents($this->tmpDir.'/node_modules/x/y.php', '<?php');
    }

    protected function tearDown(): void
    {
        $this->rmdirRecursive($this->tmpDir);
    }

    public function test_collects_only_project_php_files(): void
    {
        $files = (new FileScanner)->scan($this->tmpDir);

        $this->assertCount(2, $files);

        $names = array_map(static fn (string $p): string => basename($p), $files);
        sort($names);

        $this->assertSame(['Bar.php', 'Foo.php'], $names);
    }

    public function test_results_are_sorted(): void
    {
        $files = (new FileScanner)->scan($this->tmpDir);
        $sorted = $files;
        sort($sorted);

        $this->assertSame($sorted, $files);
    }

    public function test_does_not_descend_into_excluded_directories(): void
    {
        // A nested file deep inside vendor must NOT be returned. The walker
        // must prune the directory entry, not just filter at the file level.
        mkdir($this->tmpDir.'/vendor/some/pkg/src', 0o755, true);
        file_put_contents($this->tmpDir.'/vendor/some/pkg/src/Deep.php', '<?php class Deep {}');

        $files = (new FileScanner)->scan($this->tmpDir);

        $this->assertCount(2, $files, 'Should still return only Bar.php and Foo.php');
        foreach ($files as $f) {
            $this->assertStringNotContainsString('vendor', $f);
        }
    }

    public function test_prunes_nested_vendor_directories_at_any_depth(): void
    {
        // Real-world case: monorepos have nested packages with their own
        // vendor/ directory under packages/<pkg>/vendor. The walker must
        // prune those too, not just the project's top-level vendor.
        mkdir($this->tmpDir.'/packages/relay/vendor/foo/bar/src', 0o755, true);
        mkdir($this->tmpDir.'/packages/relay/src', 0o755, true);
        file_put_contents($this->tmpDir.'/packages/relay/src/Real.php', '<?php class Real {}');
        file_put_contents($this->tmpDir.'/packages/relay/vendor/foo/bar/src/Buried.php', '<?php class Buried {}');

        $files = (new FileScanner)->scan($this->tmpDir);

        $names = array_map(static fn (string $p): string => basename($p), $files);
        $this->assertContains('Real.php', $names);
        $this->assertNotContains('Buried.php', $names);
    }

    public function test_prunes_node_modules_at_any_depth(): void
    {
        mkdir($this->tmpDir.'/packages/inner/node_modules/x', 0o755, true);
        file_put_contents($this->tmpDir.'/packages/inner/node_modules/x/y.php', '<?php');

        $files = (new FileScanner)->scan($this->tmpDir);

        foreach ($files as $f) {
            $this->assertStringNotContainsString('node_modules', $f);
        }
    }

    public function test_prunes_tests_fixtures_directories_at_any_depth(): void
    {
        // A package installed via a Composer ``path`` repository with
        // ``symlink: true`` lives at its real source location. Its
        // ``tests/fixtures/`` directory contains demo PHP that is NOT
        // application code; without this guard the AST visitors would
        // emit findings tagged with the fixture's namespace
        // (e.g. ``SampleApp\\...``) into the consumer's index.
        mkdir($this->tmpDir.'/packages/inner/tests/fixtures/sample-app/Http', 0o755, true);
        mkdir($this->tmpDir.'/packages/inner/src', 0o755, true);
        file_put_contents(
            $this->tmpDir.'/packages/inner/tests/fixtures/sample-app/Http/Fixture.php',
            '<?php class Fixture {}',
        );
        file_put_contents(
            $this->tmpDir.'/packages/inner/src/Real.php',
            '<?php class Real {}',
        );

        $files = (new FileScanner)->scan($this->tmpDir);

        $names = array_map(static fn (string $p): string => basename($p), $files);
        $this->assertContains('Real.php', $names);
        $this->assertNotContains('Fixture.php', $names);
        // And nothing should claim a path under ``tests/fixtures/``.
        foreach ($files as $f) {
            $this->assertStringNotContainsString('/tests/fixtures/', $f);
        }
    }

    public function test_does_not_prune_other_fixtures_directories(): void
    {
        // A directory named ``fixtures`` whose parent isn't ``tests``
        // is unrelated app code (e.g. a feature module called
        // ``Fixtures``). Make sure the test-specific guard doesn't
        // overreach.
        mkdir($this->tmpDir.'/app/Domain/fixtures', 0o755, true);
        file_put_contents(
            $this->tmpDir.'/app/Domain/fixtures/SampleData.php',
            '<?php class SampleData {}',
        );

        $files = (new FileScanner)->scan($this->tmpDir);

        $names = array_map(static fn (string $p): string => basename($p), $files);
        $this->assertContains('SampleData.php', $names);
    }

    public function test_skips_symlinks_to_avoid_cycles(): void
    {
        // Symlink the project root back into itself via a subdirectory.
        // Without symlink rejection this would create an infinite walk.
        mkdir($this->tmpDir.'/app/sub', 0o755, true);
        symlink($this->tmpDir, $this->tmpDir.'/app/sub/loop');

        $files = (new FileScanner)->scan($this->tmpDir);

        // Should still terminate and return only the non-symlinked PHP files.
        $this->assertCount(2, $files);
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
