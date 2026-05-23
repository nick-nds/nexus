<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Output\JsonWriter;
use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\ErrorCollector;
use PHPUnit\Framework\TestCase;
use RuntimeException;

final class JsonWriterTest extends TestCase
{
    private string $tmpDir;

    protected function setUp(): void
    {
        $this->tmpDir = sys_get_temp_dir().'/nexus-extractor-test-'.uniqid('', true);
        mkdir($this->tmpDir, 0o755, true);
    }

    protected function tearDown(): void
    {
        $this->rmdirRecursive($this->tmpDir);
    }

    public function test_writes_pretty_json_with_schema_version(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);
        $doc->setSection('demo', ['hello' => 'world']);

        $path = $this->tmpDir.'/out/reflection.json';
        (new JsonWriter)->write($doc, $path);

        $this->assertFileExists($path);

        /** @var string $contents */
        $contents = file_get_contents($path);
        $decoded = json_decode($contents, true);

        $this->assertIsArray($decoded);
        $this->assertSame('2.4.0', $decoded['schema_version']);
        $this->assertSame(['hello' => 'world'], $decoded['sections']['demo']);
    }

    public function test_creates_missing_parent_directories(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);
        $path = $this->tmpDir.'/a/b/c/reflection.json';

        (new JsonWriter)->write($doc, $path);

        $this->assertFileExists($path);
    }

    public function test_atomic_rename_leaves_no_tmp_file(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);
        $path = $this->tmpDir.'/reflection.json';

        (new JsonWriter)->write($doc, $path);

        $this->assertFileDoesNotExist($path.'.tmp');
    }

    public function test_throws_when_directory_not_writable(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);

        // /proc is not writable on Linux.
        $this->expectException(RuntimeException::class);
        (new JsonWriter)->write($doc, '/proc/nexus-test/reflection.json');
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
