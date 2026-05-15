<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit\Extraction\Support;

use Nexus\Extractor\Extraction\Support\NamespaceExclusionFilter;
use PHPUnit\Framework\TestCase;

final class NamespaceExclusionFilterTest extends TestCase
{
    public function test_excludes_workbench_namespaces(): void
    {
        $filter = new NamespaceExclusionFilter;

        $this->assertTrue($filter->matches('Workbench\\App\\Providers\\WorkbenchServiceProvider'));
        $this->assertTrue($filter->matches('Workbench\\App\\Models\\User'));
    }

    public function test_excludes_orchestra_namespaces(): void
    {
        $filter = new NamespaceExclusionFilter;

        $this->assertTrue($filter->matches('Orchestra\\Testbench\\TestCase'));
        $this->assertTrue($filter->matches('Orchestra\\Workbench\\Actions\\CopyFile'));
        $this->assertTrue($filter->matches('Orchestra\\Sidekick\\PHP'));
        $this->assertTrue($filter->matches('Orchestra\\Canvas\\GeneratorPreset'));
    }

    public function test_does_not_exclude_user_or_laravel_classes(): void
    {
        $filter = new NamespaceExclusionFilter;

        $this->assertFalse($filter->matches('Spatie\\Permission\\Models\\Role'));
        $this->assertFalse($filter->matches('App\\Models\\User'));
        $this->assertFalse($filter->matches('Illuminate\\Database\\Eloquent\\Model'));
    }

    public function test_does_not_match_partial_namespace_collisions(): void
    {
        $filter = new NamespaceExclusionFilter;

        $this->assertFalse($filter->matches('MyOrg\\Workbench\\Service'));
        $this->assertFalse($filter->matches('SomeOrchestraImpostor\\Foo'));
    }

    public function test_filter_array_of_classes_drops_matches(): void
    {
        $filter = new NamespaceExclusionFilter;
        $classes = [
            ['name' => 'Spatie\\Permission\\Role'],
            ['name' => 'Workbench\\App\\Models\\User'],
            ['name' => 'Orchestra\\Testbench\\TestCase'],
            ['name' => 'App\\Models\\User'],
        ];

        $result = $filter->filterByName($classes, 'name');

        $this->assertCount(2, $result);
        $this->assertSame('Spatie\\Permission\\Role', $result[0]['name']);
        $this->assertSame('App\\Models\\User', $result[1]['name']);
    }
}
