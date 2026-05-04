<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseA;

use Closure;
use Illuminate\Auth\Access\Gate;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use ReflectionFunction;
use ReflectionProperty;
use Throwable;

/**
 * Extracts Gate definitions and Model → Policy bindings from Laravel's Gate.
 *
 * Why both: agents need both authorisation primitives.
 *  - Gates: ability name → callable (controller-style or closure-style)
 *  - Policies: model class → policy class (with method list resolved by
 *    Phase B's class classifier from the policy class itself)
 */
final class GatePolicyExtractor implements Extractor
{
    public function name(): string
    {
        return 'phase_a.gates_policies';
    }

    public function extract(ExtractionContext $context): void
    {
        $gate = $context->app->make(\Illuminate\Contracts\Auth\Access\Gate::class);

        if (! $gate instanceof Gate) {
            $context->document->setSection('gates_policies', [
                'gates' => [],
                'policies' => [],
                'note' => 'Gate is not Illuminate\\Auth\\Access\\Gate; skipped.',
            ]);

            return;
        }

        $context->document->setSection('gates_policies', [
            'gates' => $this->extractGates($gate),
            'policies' => $this->extractPolicies($gate),
        ]);
    }

    /**
     * @return list<array<string, mixed>>
     */
    private function extractGates(Gate $gate): array
    {
        $abilities = $this->readProperty($gate, 'abilities');

        $items = [];
        foreach ($abilities as $ability => $callback) {
            $items[] = [
                'ability' => (string) $ability,
                'callback' => $this->describeCallback($callback),
            ];
        }

        usort($items, static fn (array $a, array $b): int => strcmp((string) $a['ability'], (string) $b['ability']));

        return $items;
    }

    /**
     * @return list<array<string, string>>
     */
    private function extractPolicies(Gate $gate): array
    {
        $policies = $this->readProperty($gate, 'policies');

        $items = [];
        foreach ($policies as $model => $policy) {
            $items[] = ['model' => (string) $model, 'policy' => (string) $policy];
        }

        usort($items, static fn (array $a, array $b): int => strcmp($a['model'], $b['model']));

        return $items;
    }

    /**
     * @return array<string, mixed>
     */
    private function readProperty(Gate $gate, string $name): array
    {
        try {
            $rp = new ReflectionProperty($gate, $name);
            /** @var array<string, mixed> $value */
            $value = $rp->getValue($gate);

            return $value;
        } catch (Throwable) {
            return [];
        }
    }

    /**
     * @return array<string, mixed>
     */
    private function describeCallback(mixed $callback): array
    {
        if (is_string($callback)) {
            [$class, $method] = str_contains($callback, '@')
                ? explode('@', $callback, 2)
                : [$callback, '__invoke'];

            return ['kind' => 'class', 'class' => $class, 'method' => $method];
        }

        if ($callback instanceof Closure) {
            try {
                $rf = new ReflectionFunction($callback);

                return [
                    'kind' => 'closure',
                    'file' => $rf->getFileName() ?: null,
                    'line' => $rf->getStartLine() ?: null,
                ];
            } catch (Throwable) {
                return ['kind' => 'closure'];
            }
        }

        return ['kind' => 'unknown'];
    }
}
