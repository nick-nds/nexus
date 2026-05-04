<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Output\SchemaVersion;
use Nexus\Extractor\Support\ErrorCollector;
use Nexus\Extractor\Support\ExtractionWarning;
use PHPUnit\Framework\TestCase;

final class ReflectionDocumentTest extends TestCase
{
    public function test_to_array_includes_schema_version(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);

        $array = $doc->toArray();

        $this->assertSame(SchemaVersion::string(), $array['schema_version']);
        $this->assertArrayHasKey('generated_at', $array);
        $this->assertSame([], $array['project']);
        $this->assertSame([], $array['sections']);
    }

    public function test_set_section_round_trips(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);
        $doc->setSection('routes', ['count' => 1, 'items' => [['uri' => '/']]]);

        $this->assertSame(['count' => 1, 'items' => [['uri' => '/']]], $doc->section('routes'));

        $array = $doc->toArray();
        $this->assertSame($doc->section('routes'), $array['sections']['routes']);
    }

    public function test_summary_reflects_warning_count(): void
    {
        $errors = new ErrorCollector;
        $errors->warn(new ExtractionWarning('x', 'y'));
        $doc = new ReflectionDocument($errors);
        $doc->setSection('a', ['k' => 'v']);

        $array = $doc->toArray();

        $this->assertSame(['a'], $array['summary']['sections']);
        $this->assertSame(1, $array['summary']['warning_count']);
        $this->assertSame(0, $array['summary']['error_count']);
        $this->assertCount(1, $array['warnings']);
    }
}
