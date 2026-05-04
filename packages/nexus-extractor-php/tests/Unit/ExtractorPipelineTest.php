<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Illuminate\Contracts\Foundation\Application;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use Nexus\Extractor\Extraction\ExtractorPipeline;
use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\ErrorCollector;
use Nexus\Extractor\Support\ProgressReporter;
use PHPUnit\Framework\TestCase;
use RuntimeException;
use Symfony\Component\Console\Output\NullOutput;

final class ExtractorPipelineTest extends TestCase
{
    public function test_runs_extractors_in_order(): void
    {
        $log = [];
        $a = $this->makeExtractor('a', static function () use (&$log): void {
            $log[] = 'a';
        });
        $b = $this->makeExtractor('b', static function () use (&$log): void {
            $log[] = 'b';
        });

        (new ExtractorPipeline([$a, $b]))->run($this->context());

        $this->assertSame(['a', 'b'], $log);
    }

    public function test_thrown_exception_is_recorded_as_warning_and_pipeline_continues(): void
    {
        $context = $this->context();

        $boom = $this->makeExtractor('boom', static function (): void {
            throw new RuntimeException('kaboom');
        });
        $after = $this->makeExtractor('after', static function () use ($context): void {
            $context->document->setSection('after', ['ran' => true]);
        });

        (new ExtractorPipeline([$boom, $after]))->run($context);

        $this->assertSame(1, $context->errors->warningCount());
        $this->assertStringContainsString('boom', $context->errors->warnings()[0]->message);
        $this->assertSame(['ran' => true], $context->document->section('after'));
    }

    private function context(): ExtractionContext
    {
        $errors = new ErrorCollector;

        return new ExtractionContext(
            app: $this->createMock(Application::class),
            document: new ReflectionDocument($errors),
            errors: $errors,
            progress: new ProgressReporter(new NullOutput, quiet: true),
            includeVendor: false,
            vendorAllowlist: [],
            profileHint: null,
        );
    }

    private function makeExtractor(string $name, \Closure $body): Extractor
    {
        return new class($name, $body) implements Extractor
        {
            public function __construct(private readonly string $name, private readonly \Closure $body) {}

            public function name(): string
            {
                return $this->name;
            }

            public function extract(ExtractionContext $context): void
            {
                ($this->body)($context);
            }
        };
    }
}
