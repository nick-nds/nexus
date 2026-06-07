<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseA;

use Closure;
use Illuminate\Contracts\Foundation\Application;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Events\Dispatcher;
use Illuminate\Foundation\Support\Providers\EventServiceProvider;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use ReflectionClass;
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
        $registered = $this->registeredListenMap($context->app);

        $context->document->setSection('events', [
            'listeners' => $this->normaliseListeners($listeners, $registered),
            'wildcards' => $this->normaliseListeners($wildcards, $registered),
        ]);
    }

    /**
     * Build the set of listeners declared in every EventServiceProvider's
     * explicit ``$listen`` map, so we can distinguish them from listeners
     * wired some other way (auto-discovery, ``Event::listen``, subscribers).
     *
     * @return array<string, array<string, true>> event => set of "class@method"
     */
    private function registeredListenMap(Application $app): array
    {
        $map = [];

        foreach ($app->getProviders(EventServiceProvider::class) as $provider) {
            if (! $provider instanceof EventServiceProvider) {
                continue;
            }

            foreach ($provider->listens() as $event => $listeners) {
                foreach ((array) $listeners as $listener) {
                    if (is_string($listener)) {
                        $map[(string) $event][$this->listenerKey($listener)] = true;
                    }
                }
            }
        }

        return $map;
    }

    /**
     * Normalise a listener reference to a "class@method" key, defaulting
     * the method to ``handle`` and stripping any leading backslash so both
     * sides of the registration comparison agree.
     */
    private function listenerKey(string $listener): string
    {
        [$class, $method] = str_contains($listener, '@')
            ? explode('@', $listener, 2)
            : [$listener, 'handle'];

        return ltrim($class, '\\').'@'.$method;
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
     * @param  array<string, array<string, true>>  $registered
     * @return list<array<string, mixed>>
     */
    private function normaliseListeners(array $raw, array $registered): array
    {
        $items = [];

        foreach ($raw as $event => $callbacks) {
            $entries = [];

            if (is_array($callbacks)) {
                foreach ($callbacks as $callback) {
                    $described = $this->describeCallback($callback);

                    if ($described['kind'] === 'class') {
                        $key = $this->listenerKey(
                            (string) $described['class'].'@'.(string) $described['method'],
                        );
                        $described['source'] = isset($registered[(string) $event][$key])
                            ? 'listen'
                            : 'discovered';
                    }

                    $entries[] = $described;
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

            return $this->describeClassListener($class, $method);
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

            return $this->describeClassListener($class, $method);
        }

        return ['kind' => 'unknown'];
    }

    /**
     * Describe a class listener, recording whether it runs on a queue
     * (implements ``ShouldQueue``) and the file it's declared in.
     *
     * @return array<string, mixed>
     */
    private function describeClassListener(string $class, string $method): array
    {
        $normalised = ltrim($class, '\\');
        $queued = false;
        $file = null;

        if (class_exists($normalised)) {
            try {
                $reflection = new ReflectionClass($normalised);
                $queued = $reflection->implementsInterface(ShouldQueue::class);
                $file = $reflection->getFileName() ?: null;
            } catch (Throwable) {
                // Reflection failed unexpectedly; fall back to defaults
                // rather than failing the run.
            }
        }

        return [
            'kind' => 'class',
            'class' => $normalised,
            'method' => $method,
            'queued' => $queued,
            'file' => $file,
        ];
    }
}
