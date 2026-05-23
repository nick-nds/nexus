<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseB;

use ReflectionAttribute;
use ReflectionClass;
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

        $visibility = ReflectionMethod::IS_PUBLIC
            | ReflectionMethod::IS_PROTECTED
            | ReflectionMethod::IS_PRIVATE;

        foreach ($reflection->getMethods($visibility) as $method) {
            // Skip inherited methods from Laravel base classes — they would
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

            $methods[] = $this->describeMethod($method);
        }

        usort($methods, static fn (array $a, array $b): int => strcmp((string) $a['name'], (string) $b['name']));

        $interfaces = $reflection->getInterfaceNames();
        sort($interfaces);

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
            'traits' => $traits,
            'attributes' => $this->describeAttributes($reflection->getAttributes()),
            'methods' => $methods,
        ];
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
            // Three-way classification — until we started including
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
