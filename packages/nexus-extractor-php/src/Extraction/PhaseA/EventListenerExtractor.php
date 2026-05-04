<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseA;

use Closure;
use Illuminate\Events\Dispatcher;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use ReflectionFunction;
use ReflectionProperty;
use Throwable;

/**
 * Extracts the live event → listener map from Laravel's event dispatcher.
 *
 * Listener kinds:
 *   - class: 'App\Listeners\Foo' or 'App\Listeners\Foo@handle'
 *   - closure: anonymous function (recorded with file:line for traceability)
 *   - wildcard: registered via Event::listen('foo.*', ...)
 *
 * Closures inside Laravel's dispatcher are wrapped in `makeListener` so we
 * unwrap one level to find the original target string when possible.
 */
final class EventListenerExtractor implements Extractor
{
    public function name(): string
    {
        return 'phase_a.events';
    }

    public function extract(ExtractionContext $context): void
    {
        $events = $context->app->make('events');

        if (! $events instanceof Dispatcher) {
            $context->document->setSection('events', [
                'listeners' => [],
                'wildcards' => [],
                'note' => 'Dispatcher is not Illuminate\\Events\\Dispatcher; skipped.',
            ]);

            return;
        }

        $listeners = $this->readProperty($events, 'listeners');
        $wildcards = $this->readProperty($events, 'wildcards');

        $context->document->setSection('events', [
            'listeners' => $this->normaliseListeners($listeners),
            'wildcards' => $this->normaliseListeners($wildcards),
        ]);
    }

    /**
     * @return array<string, mixed>
     */
    private function readProperty(Dispatcher $dispatcher, string $name): array
    {
        try {
            $rp = new ReflectionProperty($dispatcher, $name);
            /** @var array<string, mixed> $value */
            $value = $rp->getValue($dispatcher);

            return $value;
        } catch (Throwable) {
            return [];
        }
    }

    /**
     * @param  array<string, mixed>  $raw
     * @return list<array<string, mixed>>
     */
    private function normaliseListeners(array $raw): array
    {
        $items = [];

        foreach ($raw as $event => $callbacks) {
            $entries = [];

            if (is_array($callbacks)) {
                foreach ($callbacks as $callback) {
                    $entries[] = $this->describeCallback($callback);
                }
            }

            $items[] = [
                'event' => (string) $event,
                'listeners' => $entries,
            ];
        }

        usort($items, static fn (array $a, array $b): int => strcmp((string) $a['event'], (string) $b['event']));

        return $items;
    }

    /**
     * @return array<string, mixed>
     */
    private function describeCallback(mixed $callback): array
    {
        if (is_string($callback)) {
            [$class, $method] = str_contains($callback, '@')
                ? explode('@', $callback, 2)
                : [$callback, 'handle'];

            return ['kind' => 'class', 'class' => $class, 'method' => $method];
        }

        if ($callback instanceof Closure) {
            try {
                $rf = new ReflectionFunction($callback);

                // Laravel wraps listeners in `makeListener`. Unwrap one level
                // to recover the original string when possible.
                $statics = $rf->getStaticVariables();
                if (isset($statics['listener']) && is_string($statics['listener'])) {
                    return $this->describeCallback($statics['listener']);
                }

                return [
                    'kind' => 'closure',
                    'file' => $rf->getFileName() ?: null,
                    'line' => $rf->getStartLine() ?: null,
                ];
            } catch (Throwable) {
                return ['kind' => 'closure'];
            }
        }

        if (is_array($callback) && count($callback) === 2) {
            $target = $callback[0];
            $method = (string) $callback[1];
            $class = is_object($target) ? $target::class : (string) $target;

            return ['kind' => 'class', 'class' => $class, 'method' => $method];
        }

        return ['kind' => 'unknown'];
    }
}
