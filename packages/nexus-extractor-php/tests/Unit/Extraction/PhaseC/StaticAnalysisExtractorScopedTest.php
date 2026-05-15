<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit\Extraction\PhaseC;

use Nexus\Extractor\Extraction\PhaseC\StaticAnalysisExtractor;
use Nexus\Extractor\Extraction\Support\PackageScope;
use PHPUnit\Framework\TestCase;

/**
 * Verifies that StaticAnalysisExtractor restricts its file-root set to the
 * package's PSR-4 source directories when a PackageScope is supplied, and
 * that the null-scope path is unaffected (Phase 1 golden snapshot parity).
 */
final class StaticAnalysisExtractorScopedTest extends TestCase
{
    private string $tmpRoot;

    protected function setUp(): void
    {
        $this->tmpRoot = sys_get_temp_dir().'/nexus-static-scoped-'.uniqid('', true);
        mkdir($this->tmpRoot.'/src', 0o755, true);
        mkdir($this->tmpRoot.'/tests', 0o755, true);
        file_put_contents($this->tmpRoot.'/src/A.php', "<?php\nnamespace Foo\\Bar; class A {}\n");
        file_put_contents($this->tmpRoot.'/tests/T.php', "<?php\nnamespace Foo\\Bar\\Tests; class T {}\n");
    }

    protected function tearDown(): void
    {
        @unlink($this->tmpRoot.'/src/A.php');
        @unlink($this->tmpRoot.'/tests/T.php');
        @rmdir($this->tmpRoot.'/src');
        @rmdir($this->tmpRoot.'/tests');
        @rmdir($this->tmpRoot);
    }

    public function test_when_scope_set_only_files_under_psr4_dirs_are_scanned(): void
    {
        $scope = new PackageScope(
            vendor: 'foo',
            name: 'bar',
            version: '1.0',
            vendorPath: $this->tmpRoot,
            namespaces: ['Foo\\Bar\\' => 'src/'],   // only src/, not tests/
        );

        $extractor = new StaticAnalysisExtractor;
        $files = $extractor->resolveFileRoots($scope);

        $this->assertCount(1, $files);
        $this->assertSame($this->tmpRoot.'/src', rtrim($files[0], '/'));
    }

    public function test_when_scope_null_returns_empty_array_project_roots(): void
    {
        // When scope is null, resolveFileRoots() returns an empty array
        // and the extractor falls back to the scanner using the context's
        // app basePath. This ensures the null path remains intact for
        // project-mode (Phase 1 golden snapshot parity).
        $extractor = new StaticAnalysisExtractor;
        $roots = $extractor->resolveFileRoots(null);

        $this->assertSame([], $roots);
    }
}
