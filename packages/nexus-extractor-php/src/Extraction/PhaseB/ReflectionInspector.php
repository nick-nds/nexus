<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseB;

use ReflectionAttribute;
use ReflectionClass;
use ReflectionEnum;
use ReflectionEnumBackedCase;
use ReflectionMethod;
use ReflectionNamedType;
use ReflectionParameter;
use ReflectionType;
use ReflectionUnionType;

/**
 * Produces a structured description of a class's reflection metadata.
 *
 * Captures: namespace, file, abstract/final flags, parents, interfaces,
 * traits, PHP 8 attributes, public/protected methods (with parameter types,
 * return type, attributes). Does NOT capture: method bodies, constants
 * unless documented, private methods (deemed implementation detail).
 *
 * Determinism: every list is sorted (alphabetically by name) so the same
 * class always produces the same JSON, which is critical for golden tests.
 */
final class ReflectionInspector
{
    /**
     * @param  ReflectionClass<object>  $reflection
     * @return array<string, mixed>
     */
    public function inspect(ReflectionClass $reflection): array
    {
        $methods = [];
        $isEnum = $reflection->isEnum();

        $visibility = ReflectionMethod::IS_PUBLIC
            | ReflectionMethod::IS_PROTECTED
            | ReflectionMethod::IS_PRIVATE;

        foreach ($reflection->getMethods($visibility) as $method) {
            // Skip inherited methods from Laravel base classes - they would
            // double the document size with framework noise. We keep methods
            // declared on the class itself or its non-vendor parents.
            //
            // Private methods are included so static-analysis findings
            // emitted inside private helpers (cache reads, dispatches
            // hidden behind a small abstraction layer, etc.) can attach
            // to a real method node. Without this their edges would be
            // dropped as dangling at SQLite-persist time.
            if ($method->getDeclaringClass()->getName() !== $reflection->getName()) {
                continue;
            }

            // Audit P0-2: skip PHP-synthesized enum methods (cases /
            // from / tryFrom). They have ``getDeclaringClass() ==
            // enum`` so the inherited-method filter above doesn't
            // catch them, but their ``line`` is ``null`` because they
            // aren't source-defined. Emitting them as if they were
            // real source methods misled agents into thinking they
            // were part of the application surface.
            if ($isEnum && $method->isStatic()
                && in_array($method->getName(), ['cases', 'from', 'tryFrom'], true)
                && $method->getFileName() === false) {
                continue;
            }

            $methods[] = $this->describeMethod($method);
        }

        usort($methods, static fn (array $a, array $b): int => strcmp((string) $a['name'], (string) $b['name']));

        // Audit P0-4: split interfaces into declared (the class's own
        // ``implements`` clause) vs inherited (transitively from parent
        // classes). ``Tenant extends Model`` was emitting 9 inherited
        // interfaces as if the model declared them; agents asking
        // "what contracts does Tenant promise?" got noise. The
        // ``interfaces`` field now means "declared on this class"; the
        // full set is the union of ``interfaces`` + ``interfaces_inherited``.
        $allInterfaces = $reflection->getInterfaceNames();
        $parentInterfaces = [];
        $parent = $reflection->getParentClass();
        if ($parent !== false) {
            $parentInterfaces = $parent->getInterfaceNames();
        }
        $interfaces = array_values(array_diff($allInterfaces, $parentInterfaces));
        sort($interfaces);
        $interfacesInherited = array_values(array_intersect($allInterfaces, $parentInterfaces));
        sort($interfacesInherited);

        $traits = $reflection->getTraitNames();
        sort($traits);

        return [
            'name' => $reflection->getName(),
            'short_name' => $reflection->getShortName(),
            'namespace' => $reflection->getNamespaceName(),
            'file' => $reflection->getFileName() ?: null,
            'abstract' => $reflection->isAbstract(),
            'final' => $reflection->isFinal(),
            // PHP 8.2+ added ``ReflectionClass::isReadOnly`` for the
            // ``final readonly class Foo`` form heavily used in DTOs.
            // Audit P0-5: the ``readonly`` modifier changes object
            // semantics (every property is implicitly readonly), so
            // dropping it loses information agents care about.
            'readonly' => $reflection->isReadOnly(),
            'parent' => $reflection->getParentClass() !== false ? $reflection->getParentClass()->getName() : null,
            'interfaces' => $interfaces,
            'interfaces_inherited' => $interfacesInherited,
            'traits' => $traits,
            'attributes' => $this->describeAttributes($reflection->getAttributes()),
            'methods' => $methods,
            // Audit P0-2: enum cases are the only meaningful content of
            // an enum declaration. Surfacing them lets agents answer
            // "what statuses can a Customer have?" without reading the
            // source. Empty list for non-enums so the field is always
            // present (Pydantic-side default keeps old indexes loading).
            'cases' => $isEnum ? $this->describeEnumCases($reflection) : [],
        ];
    }

    /**
     * Collect a backed or unit enum's case declarations.
     *
     * Backed enums (``enum CustomerStatus: string``) carry a ``value``
     * for each case; unit enums (``enum Color``) have only a name.
     * Both shapes are surfaced uniformly with ``value`` falling back
     * to ``null`` when absent.
     *
     * @param  ReflectionClass<object>  $reflection
     * @return list<array{name: string, value: int|string|null}>
     */
    private function describeEnumCases(ReflectionClass $reflection): array
    {
        $cases = [];
        $enumReflection = new ReflectionEnum($reflection->getName());
        foreach ($enumReflection->getCases() as $case) {
            $value = null;
            if ($case instanceof ReflectionEnumBackedCase) {
                $backing = $case->getBackingValue();
                if (is_int($backing) || is_string($backing)) {
                    $value = $backing;
                }
            }
            $cases[] = [
                'name' => $case->getName(),
                'value' => $value,
            ];
        }

        return $cases;
    }

    /**
     * @return array<string, mixed>
     */
    private function describeMethod(ReflectionMethod $method): array
    {
        $params = [];
        foreach ($method->getParameters() as $param) {
            $params[] = $this->describeParameter($param);
        }

        return [
            'name' => $method->getName(),
            // Three-way classification - until we started including
            // private methods this was a binary public-or-protected
            // check that mis-labeled private methods. Agents reading
            // ``visibility`` rely on it to decide whether a method is
            // a candidate API surface or an internal helper.
            'visibility' => match (true) {
                $method->isPrivate() => 'private',
                $method->isProtected() => 'protected',
                default => 'public',
            },
            'static' => $method->isStatic(),
            'abstract' => $method->isAbstract(),
            'final' => $method->isFinal(),
            'parameters' => $params,
            'return_type' => $this->describeType($method->getReturnType()),
            'attributes' => $this->describeAttributes($method->getAttributes()),
            'line' => $method->getStartLine() ?: null,
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private function describeParameter(ReflectionParameter $param): array
    {
        return [
            'name' => $param->getName(),
            'type' => $this->describeType($param->getType()),
            'optional' => $param->isOptional(),
            'variadic' => $param->isVariadic(),
            'by_reference' => $param->isPassedByReference(),
        ];
    }

    private function describeType(?ReflectionType $type): ?string
    {
        if ($type === null) {
            return null;
        }

        if ($type instanceof ReflectionNamedType) {
            return ($type->allowsNull() && $type->getName() !== 'mixed' && $type->getName() !== 'null' ? '?' : '').$type->getName();
        }

        if ($type instanceof ReflectionUnionType) {
            $parts = [];
            foreach ($type->getTypes() as $t) {
                if ($t instanceof ReflectionNamedType) {
                    $parts[] = $t->getName();
                }
            }

            return implode('|', $parts);
        }

        return (string) $type;
    }

    /**
     * @param  array<int, ReflectionAttribute<object>>  $attributes
     * @return list<array<string, mixed>>
     */
    private function describeAttributes(array $attributes): array
    {
        $items = [];

        foreach ($attributes as $attr) {
            $items[] = [
                'name' => $attr->getName(),
                'arguments' => $this->serialiseArguments($attr->getArguments()),
            ];
        }

        return $items;
    }

    /**
     * @param  array<int|string, mixed>  $args
     * @return array<int|string, mixed>
     */
    private function serialiseArguments(array $args): array
    {
        $out = [];
        foreach ($args as $key => $value) {
            $out[$key] = match (true) {
                is_scalar($value), $value === null => $value,
                is_array($value) => $this->serialiseArguments($value),
                default => '«object»',
            };
        }

        return $out;
    }
}
