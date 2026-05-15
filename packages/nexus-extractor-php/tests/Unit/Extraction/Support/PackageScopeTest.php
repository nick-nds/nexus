<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit\Extraction\Support;

use Nexus\Extractor\Extraction\Support\PackageScope;
use PHPUnit\Framework\TestCase;

final class PackageScopeTest extends TestCase
{
    public function test_constructs_with_required_fields(): void
    {
        $scope = new PackageScope(
            vendor: 'spatie',
            name: 'laravel-permission',
            version: 'v6.18.0',
            vendorPath: '/scratch/vendor/spatie/laravel-permission',
            namespaces: ['Spatie\\Permission\\' => 'src/'],
        );

        $this->assertSame('spatie', $scope->vendor);
        $this->assertSame('laravel-permission', $scope->name);
        $this->assertSame('v6.18.0', $scope->version);
        $this->assertSame('/scratch/vendor/spatie/laravel-permission', $scope->vendorPath);
        $this->assertSame(['Spatie\\Permission\\' => 'src/'], $scope->namespaces);
    }

    public function test_full_name_returns_vendor_slash_name(): void
    {
        $scope = new PackageScope('spatie', 'laravel-permission', 'v6.18.0', '/p', []);
        $this->assertSame('spatie/laravel-permission', $scope->fullName());
    }

    public function test_matches_namespace_returns_true_for_namespace_under_psr4_prefix(): void
    {
        $scope = new PackageScope(
            'spatie',
            'laravel-permission',
            'v6.18.0',
            '/p',
            ['Spatie\\Permission\\' => 'src/'],
        );

        $this->assertTrue($scope->matchesNamespace('Spatie\\Permission\\Models\\Role'));
        $this->assertTrue($scope->matchesNamespace('Spatie\\Permission\\Middleware\\PermissionMiddleware'));
        $this->assertFalse($scope->matchesNamespace('Illuminate\\Database\\Eloquent\\Model'));
    }

    public function test_matches_namespace_handles_multiple_psr4_prefixes(): void
    {
        $scope = new PackageScope(
            'foo',
            'bar',
            '1.0',
            '/p',
            ['Foo\\Bar\\' => 'src/', 'Foo\\Bar\\Tests\\' => 'tests/'],
        );

        $this->assertTrue($scope->matchesNamespace('Foo\\Bar\\Whatever'));
        $this->assertTrue($scope->matchesNamespace('Foo\\Bar\\Tests\\Helper'));
        $this->assertFalse($scope->matchesNamespace('Foo\\Other'));
    }
}
