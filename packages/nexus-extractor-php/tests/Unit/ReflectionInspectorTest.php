<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseB\ReflectionInspector;
use PHPUnit\Framework\TestCase;
use ReflectionClass;
use SampleApp\DTOs\CustomerDto;
use SampleApp\Http\Controllers\PostController;
use SampleApp\Http\Requests\StorePostRequest;
use SampleApp\Models\User;

final class ReflectionInspectorTest extends TestCase
{
    private ReflectionInspector $inspector;

    protected function setUp(): void
    {
        $this->inspector = new ReflectionInspector;
    }

    public function test_captures_class_metadata(): void
    {
        $data = $this->inspector->inspect(new ReflectionClass(User::class));

        $this->assertSame(User::class, $data['name']);
        $this->assertSame('User', $data['short_name']);
        $this->assertSame('SampleApp\\Models', $data['namespace']);
        $this->assertTrue($data['final']);
        $this->assertFalse($data['abstract']);
        $this->assertSame('Illuminate\\Database\\Eloquent\\Model', $data['parent']);
    }

    public function test_methods_are_sorted_alphabetically(): void
    {
        $data = $this->inspector->inspect(new ReflectionClass(PostController::class));
        /** @var list<array{name: string}> $methods */
        $methods = $data['methods'];

        $names = array_map(static fn (array $m): string => $m['name'], $methods);
        $sorted = $names;
        sort($sorted);

        $this->assertSame($sorted, $names);
        $this->assertContains('store', $names);
        $this->assertContains('show', $names);
        $this->assertContains('index', $names);
    }

    public function test_method_parameters_carry_types(): void
    {
        $data = $this->inspector->inspect(new ReflectionClass(PostController::class));
        /** @var list<array{name: string, parameters: list<array{name: string, type: ?string}>}> $methods */
        $methods = $data['methods'];

        $store = null;
        foreach ($methods as $method) {
            if ($method['name'] === 'store') {
                $store = $method;
                break;
            }
        }

        $this->assertNotNull($store);
        $this->assertSame('request', $store['parameters'][0]['name']);
        $this->assertSame(StorePostRequest::class, $store['parameters'][0]['type']);
    }

    public function test_skips_inherited_methods(): void
    {
        $data = $this->inspector->inspect(new ReflectionClass(User::class));
        /** @var list<array{name: string}> $methods */
        $methods = $data['methods'];

        $names = array_map(static fn (array $m): string => $m['name'], $methods);
        $this->assertContains('posts', $names);
        // The inherited save() method from Eloquent\Model must NOT appear.
        $this->assertNotContains('save', $names);
    }

    public function test_captures_readonly_modifier_on_dtos(): void
    {
        // Pins audit P0-5: ``final readonly class`` modifier must
        // surface in the reflection output so downstream consumers
        // can distinguish DTOs from mutable models.
        $data = $this->inspector->inspect(new ReflectionClass(CustomerDto::class));

        $this->assertTrue($data['readonly']);
        $this->assertTrue($data['final']);
    }

    public function test_readonly_false_for_non_readonly_class(): void
    {
        // Most classes are NOT readonly. The field must be present
        // and ``false`` for them — never absent — so the Python side
        // can distinguish "we know it isn't" from "we don't know".
        $data = $this->inspector->inspect(new ReflectionClass(User::class));

        $this->assertFalse($data['readonly']);
    }
}
