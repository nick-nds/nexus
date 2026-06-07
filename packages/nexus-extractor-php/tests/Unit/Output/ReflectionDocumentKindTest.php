<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit\Output;

use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\ErrorCollector;
use PHPUnit\Framework\TestCase;

final class ReflectionDocumentKindTest extends TestCase
{
    public function test_kind_defaults_to_project(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);
        $array = $doc->toArray();

        $this->assertSame('project', $array['kind']);
        $this->assertNull($array['package']);
    }

    public function test_set_package_sets_kind_to_package(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);
        $doc->setPackage([
            'vendor' => 'spatie',
            'name' => 'laravel-permission',
            'version' => 'v6.18.0',
            'description' => 'Associate users with roles and permissions',
            'authors' => [['name' => 'Spatie', 'email' => null, 'homepage' => null, 'role' => null]],
            'license' => 'MIT',
            'homepage' => 'https://spatie.be',
        ]);

        $array = $doc->toArray();
        $this->assertSame('package', $array['kind']);
        $this->assertSame([
            'vendor' => 'spatie',
            'name' => 'laravel-permission',
            'version' => 'v6.18.0',
            'description' => 'Associate users with roles and permissions',
            'authors' => [['name' => 'Spatie', 'email' => null, 'homepage' => null, 'role' => null]],
            'license' => 'MIT',
            'homepage' => 'https://spatie.be',
        ], $array['package']);
    }

    public function test_set_package_defaults_attribution_fields_to_null(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);
        $doc->setPackage([
            'vendor' => 'spatie',
            'name' => 'laravel-permission',
            'version' => 'v6.18.0',
            'description' => null,
            'authors' => [],
            'license' => null,
            'homepage' => null,
        ]);

        $array = $doc->toArray();
        $this->assertNull($array['package']['description']);
        $this->assertSame([], $array['package']['authors']);
        $this->assertNull($array['package']['license']);
        $this->assertNull($array['package']['homepage']);
    }

    public function test_schema_version_is_2_4_0(): void
    {
        $doc = new ReflectionDocument(new ErrorCollector);
        $array = $doc->toArray();
        $this->assertSame('2.5.0', $array['schema_version']);
    }
}
