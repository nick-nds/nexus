<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseB\ReflectionInspector;
use PHPUnit\Framework\TestCase;
use ReflectionClass;
use SampleApp\DTOs\CustomerDto;
use SampleApp\Enums\CustomerStatus;
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
        // and ``false`` for them - never absent - so the Python side
        // can distinguish "we know it isn't" from "we don't know".
        $data = $this->inspector->inspect(new ReflectionClass(User::class));

        $this->assertFalse($data['readonly']);
    }

    public function test_enum_cases_are_captured(): void
    {
        // Audit P0-2: enum cases are the only meaningful content of
        // an enum declaration. The inspector must surface them via a
        // ``cases`` field with name + backing value.
        $data = $this->inspector->inspect(new ReflectionClass(CustomerStatus::class));

        $this->assertSame(
            [
                ['name' => 'Active', 'value' => 'active'],
                ['name' => 'Inactive', 'value' => 'inactive'],
                ['name' => 'Churned', 'value' => 'churned'],
            ],
            $data['cases'],
        );
    }

    public function test_enum_synthetic_methods_are_suppressed(): void
    {
        // Audit P0-2: PHP synthesises ``cases``, ``from``, ``tryFrom``
        // on backed enums. Their declaring class is the enum itself
        // (so the inherited-method filter doesn't catch them) but
        // they have ``line: null`` because no source. Without an
        // explicit suppress they showed up as fake methods in
        // describe_class responses.
        $data = $this->inspector->inspect(new ReflectionClass(CustomerStatus::class));

        $names = array_map(static fn (array $m): string => $m['name'], $data['methods']);
        $this->assertNotContains('cases', $names);
        $this->assertNotContains('from', $names);
        $this->assertNotContains('tryFrom', $names);
    }

    public function test_cases_field_empty_for_non_enums(): void
    {
        // The ``cases`` field must always be present; for non-enums
        // it's just an empty list. Keeps the Python schema stable.
        $data = $this->inspector->inspect(new ReflectionClass(User::class));

        $this->assertSame([], $data['cases']);
    }

    public function test_interfaces_are_split_into_declared_and_inherited(): void
    {
        // Audit P0-4: ``interfaces`` is the class's own ``implements``
        // declarations only. The interfaces inherited from parent
        // classes (Eloquent\Model contributes ArrayAccess, Arrayable,
        // etc.) live in ``interfaces_inherited``.
        $data = $this->inspector->inspect(new ReflectionClass(User::class));

        // User extends Model and doesn't declare any ``implements``.
        $this->assertSame([], $data['interfaces']);
        // Model brings in several interfaces transitively - check at
        // least one to confirm the field is populated.
        $this->assertNotEmpty($data['interfaces_inherited']);
        $this->assertContains('ArrayAccess', $data['interfaces_inherited']);
    }
}
