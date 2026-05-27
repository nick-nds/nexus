<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseA;

use Closure;
use Illuminate\Container\Container;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use Nexus\Extractor\Support\ExtractionWarning;
use ReflectionFunction;
use ReflectionProperty;
use Throwable;

/**
 * Extracts service container bindings, singletons, instances, and aliases.
 *
 * Why runtime: bindings declared inside service providers are invisible to
 * static analysis. We capture the live container state after the framework
 * has fully booted.
 *
 * Closure bindings are reported by file:line so the Python pipeline can map
 * them back to the defining provider in the call graph (LSP enrichment in
 * Phase 3 will resolve callers).
 */
final class BindingExtractor implements Extractor
{
    public function name(): string
    {
        return 'phase_a.bindings';
    }

    public function extract(ExtractionContext $context): void
    {
        $container = Container::getInstance();

        $bindings = $this->extractBindings($container, $context);
        $aliases = $this->extractAliases($container);
        $instances = $this->extractInstances($container);

        $context->document->setSection('bindings', [
            'bindings' => $bindings,
            'aliases' => $aliases,
            'instances' => $instances,
            'summary' => [
                'binding_count' => count($bindings),
                'alias_count' => count($aliases),
                'instance_count' => count($instances),
            ],
        ]);
    }

    /**
     * @return list<array<string, mixed>>
     */
    private function extractBindings(Container $container, ExtractionContext $context): array
    {
        $items = [];

        foreach ($container->getBindings() as $abstract => $binding) {
            try {
                /** @var Closure $concrete */
                $concrete = $binding['concrete'];
                $shared = (bool) ($binding['shared'] ?? false);

                $items[] = [
                    'abstract' => (string) $abstract,
                    'shared' => $shared,
                    'concrete' => $this->describeConcrete($concrete),
                ];
            } catch (Throwable $e) {
                $context->errors->warn(new ExtractionWarning(
                    code: 'binding_describe_failed',
                    message: $e->getMessage(),
                    context: ['abstract' => (string) $abstract],
                ));
            }
        }

        usort($items, static fn (array $a, array $b): int => strcmp((string) $a['abstract'], (string) $b['abstract']));

        return $items;
    }

    /**
     * @return list<array<string, string>>
     */
    private function extractAliases(Container $container): array
    {
        $items = [];

        // Container's aliases property is protected; reach via reflection.
        try {
            $rp = new ReflectionProperty($container, 'aliases');
            /** @var array<string, string> $aliases */
            $aliases = $rp->getValue($container);
        } catch (Throwable) {
            $aliases = [];
        }

        foreach ($aliases as $alias => $abstract) {
            $items[] = ['alias' => (string) $alias, 'abstract' => (string) $abstract];
        }

        usort($items, static fn (array $a, array $b): int => strcmp($a['alias'], $b['alias']));

        return $items;
    }

    /**
     * @return list<array<string, mixed>>
     */
    private function extractInstances(Container $container): array
    {
        $items = [];

        try {
            $rp = new ReflectionProperty($container, 'instances');
            /** @var array<string, object|scalar> $instances */
            $instances = $rp->getValue($container);
        } catch (Throwable) {
            $instances = [];
        }

        foreach ($instances as $abstract => $instance) {
            $items[] = [
                'abstract' => (string) $abstract,
                'class' => is_object($instance) ? $instance::class : null,
            ];
        }

        usort($items, static fn (array $a, array $b): int => strcmp((string) $a['abstract'], (string) $b['abstract']));

        return $items;
    }

    /**
     * @return array<string, mixed>
     */
    private function describeConcrete(Closure $concrete): array
    {
        try {
            $rf = new ReflectionFunction($concrete);

            // Closures inside Container::resolve() are typically generated
            // bindings; the captured class is the target type.
            $static = $rf->getStaticVariables();

            if (isset($static['concrete']) && is_string($static['concrete'])) {
                return ['kind' => 'class', 'class' => $static['concrete']];
            }

            // A bound class - try to inspect what the closure returns by
            // looking at its source range.
            return [
                'kind' => 'closure',
                'file' => $rf->getFileName() ?: null,
                'line' => $rf->getStartLine() ?: null,
            ];
        } catch (Throwable) {
            return ['kind' => 'closure'];
        }
    }
}
