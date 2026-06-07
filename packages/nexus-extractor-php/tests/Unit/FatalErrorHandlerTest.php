<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Output\JsonWriter;
use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\CurrentClassTracker;
use Nexus\Extractor\Support\ErrorCollector;
use Nexus\Extractor\Support\FatalErrorHandler;
use PHPUnit\Framework\TestCase;

final class FatalErrorHandlerTest extends TestCase
{
    private string $tmpDir;

    private string $outputPath;

    protected function setUp(): void
    {
        $this->tmpDir = sys_get_temp_dir().'/nexus-fatal-'.uniqid('', true);
        mkdir($this->tmpDir, 0o755, true);
        $this->outputPath = $this->tmpDir.'/reflection.json';
    }

    protected function tearDown(): void
    {
        if (is_file($this->outputPath)) {
            @unlink($this->outputPath);
        }
        @rmdir($this->tmpDir);
    }

    public function test_ignores_non_fatal_error_types(): void
    {
        [$doc, $tracker, $handler] = $this->build();

        $captured = $handler->handleError([
            'type' => E_WARNING,
            'message' => 'just a warning',
            'file' => '/tmp/x.php',
            'line' => 1,
        ]);

        $this->assertFalse($captured);
        $this->assertFalse($doc->errors()->hasErrors());
        $this->assertFileDoesNotExist($this->outputPath);
    }

    public function test_captures_e_error_and_writes_partial_document(): void
    {
        [$doc, $tracker, $handler] = $this->build();
        $tracker->set('App\\Broken\\Thing');

        $captured = $handler->handleError([
            'type' => E_ERROR,
            'message' => 'Class contains 2 abstract methods',
            'file' => '/app/Broken/Thing.php',
            'line' => 19,
        ]);

        $this->assertTrue($captured);
        $this->assertTrue($doc->errors()->hasErrors());
        $this->assertFileExists($this->outputPath);

        /** @var array<string, mixed> $written */
        $written = json_decode((string) file_get_contents($this->outputPath), true, 512, JSON_THROW_ON_ERROR);
        $this->assertSame('2.5.0', $written['schema_version']);
        $this->assertNotEmpty($written['errors']);
        $this->assertSame('fatal_during_class_load', $written['errors'][0]['code']);
        $this->assertStringContainsString('App\\Broken\\Thing', $written['errors'][0]['message']);
        $this->assertSame('/app/Broken/Thing.php', $written['errors'][0]['file']);
        $this->assertSame(19, $written['errors'][0]['line']);
    }

    public function test_captures_parse_error_and_compile_error(): void
    {
        foreach ([E_PARSE, E_COMPILE_ERROR, E_CORE_ERROR, E_USER_ERROR] as $type) {
            [$doc, $tracker, $handler] = $this->build();
            $tracker->set('App\\X');

            $this->assertTrue($handler->handleError([
                'type' => $type,
                'message' => "error of type $type",
                'file' => '/x.php',
                'line' => 1,
            ]));

            $this->assertTrue($doc->errors()->hasErrors(), "type $type should be captured");
        }
    }

    public function test_produces_generic_message_when_tracker_is_empty(): void
    {
        [$doc, $tracker, $handler] = $this->build();
        // No tracker->set() - simulate a fatal before Phase B began.

        $captured = $handler->handleError([
            'type' => E_ERROR,
            'message' => 'boot failed',
            'file' => '/bootstrap/app.php',
            'line' => 10,
        ]);

        $this->assertTrue($captured);
        /** @var array<string, mixed> $written */
        $written = json_decode((string) file_get_contents($this->outputPath), true);
        $this->assertStringContainsString('boot failed', $written['errors'][0]['message']);
        $this->assertStringNotContainsString('while loading class', $written['errors'][0]['message']);
    }

    public function test_writer_failure_does_not_propagate(): void
    {
        // Point at a definitely-unwritable location; writer will throw,
        // but handleError must still return true and not propagate.
        $doc = new ReflectionDocument(new ErrorCollector);
        $tracker = new CurrentClassTracker;
        $handler = new FatalErrorHandler($doc, $tracker, new JsonWriter, '/proc/nonexistent/out.json');

        $tracker->set('App\\X');

        $captured = $handler->handleError([
            'type' => E_ERROR,
            'message' => 'some fatal',
            'file' => '/x.php',
            'line' => 1,
        ]);

        $this->assertTrue($captured);
        $this->assertTrue($doc->errors()->hasErrors());
    }

    /**
     * @return array{0: ReflectionDocument, 1: CurrentClassTracker, 2: FatalErrorHandler}
     */
    private function build(): array
    {
        $doc = new ReflectionDocument(new ErrorCollector);
        $tracker = new CurrentClassTracker;
        $handler = new FatalErrorHandler($doc, $tracker, new JsonWriter, $this->outputPath);

        return [$doc, $tracker, $handler];
    }
}
