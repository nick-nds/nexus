<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit\Extraction;

use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\ExtractionRunner;
use Nexus\Extractor\Extraction\ExtractorPipeline;
use Nexus\Extractor\Output\JsonWriter;
use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\CurrentClassTracker;
use Nexus\Extractor\Support\ErrorCollector;
use Nexus\Extractor\Support\ProgressReporter;
use Nexus\Extractor\Tests\TestCase;
use Symfony\Component\Console\Output\BufferedOutput;

final class ExtractionRunnerTest extends TestCase
{
    public function test_runs_pipeline_and_writes_document(): void
    {
        $tmp = tempnam(sys_get_temp_dir(), 'nexus-ext-runner-').'.json';
        try {
            $errors = new ErrorCollector;
            $document = new ReflectionDocument($errors);
            $document->setProject([
                'name' => 'Test',
                'environment' => 'testing',
                'laravel_version' => '11.0.0',
                'php_version' => PHP_VERSION,
                'base_path' => sys_get_temp_dir(),
                'profile_hint' => null,
            ]);

            $reporter = new ProgressReporter(new BufferedOutput, quiet: true);
            $tracker = new CurrentClassTracker;
            $context = new ExtractionContext(
                app: $this->app,
                document: $document,
                errors: $errors,
                progress: $reporter,
                includeVendor: false,
                vendorAllowlist: [],
                profileHint: null,
                includeTests: false,
                classTracker: $tracker,
            );

            $runner = new ExtractionRunner(
                pipeline: new ExtractorPipeline([]),
                writer: new JsonWriter,
            );

            $exit = $runner->run($context, $document, $tmp);

            $this->assertSame(0, $exit);
            $this->assertFileExists($tmp);
            $contents = json_decode(file_get_contents($tmp), associative: true);
            $this->assertSame('Test', $contents['project']['name']);
        } finally {
            @unlink($tmp);
        }
    }

    public function test_returns_exit_1_when_writer_fails(): void
    {
        $errors = new ErrorCollector;
        $document = new ReflectionDocument($errors);
        $document->setProject([
            'name' => 'Test',
            'environment' => 'testing',
            'laravel_version' => '11.0.0',
            'php_version' => PHP_VERSION,
            'base_path' => sys_get_temp_dir(),
            'profile_hint' => null,
        ]);
        $reporter = new ProgressReporter(new BufferedOutput, quiet: true);
        $tracker = new CurrentClassTracker;
        $context = new ExtractionContext(
            app: $this->app,
            document: $document,
            errors: $errors,
            progress: $reporter,
            includeVendor: false,
            vendorAllowlist: [],
            profileHint: null,
            includeTests: false,
            classTracker: $tracker,
        );

        $runner = new ExtractionRunner(
            pipeline: new ExtractorPipeline([]),
            writer: new JsonWriter,
        );

        $exit = $runner->run($context, $document, '/dev/null/cannot-write/file.json');

        $this->assertSame(1, $exit);
    }
}
