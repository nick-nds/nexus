<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseA;

use Closure;
use Illuminate\Routing\Route;
use Illuminate\Routing\Router;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use Nexus\Extractor\Support\ExtractionWarning;
use ReflectionFunction;
use Throwable;

/**
 * Extracts the live route table from Laravel's Router.
 *
 * Why runtime, not file scanning: route declarations can live anywhere
 * (web.php, api.php, package providers, runtime registrations). The Router
 * is the only authoritative source.
 *
 * Each route is captured with the HTTP methods it handles, its URI, name,
 * middleware list, where-clauses (parameter constraints), domain, action
 * (controller@method or closure description), and any route parameters.
 */
final class RouteExtractor implements Extractor
{
    public function name(): string
    {
        return 'phase_a.routes';
    }

    public function extract(ExtractionContext $context): void
    {
        /** @var Router $router */
        $router = $context->app->make('router');
        $routes = $router->getRoutes();

        $items = [];

        // RouteCollectionInterface only guarantees getRoutes() as an array.
        /** @var array<int, Route> $iterable */
        $iterable = $routes->getRoutes();

        foreach ($iterable as $route) {
            try {
                $items[] = $this->describeRoute($route);
            } catch (Throwable $e) {
                $context->errors->warn(new ExtractionWarning(
                    code: 'route_describe_failed',
                    message: $e->getMessage(),
                    context: ['uri' => $route->uri()],
                ));
            }
        }

        // Deterministic ordering for stable golden snapshots.
        usort($items, static function (array $a, array $b): int {
            return [$a['uri'], $a['methods'][0] ?? ''] <=> [$b['uri'], $b['methods'][0] ?? ''];
        });

        $context->document->setSection('routes', [
            'count' => count($items),
            'items' => $items,
        ]);
    }

    /**
     * @return array<string, mixed>
     */
    private function describeRoute(Route $route): array
    {
        $action = $route->getAction();

        /** @var array<int, string> $methods */
        $methods = array_values(array_filter(
            $route->methods(),
            static fn (string $m): bool => $m !== 'HEAD',
        ));

        return [
            'uri' => '/'.ltrim($route->uri(), '/'),
            'methods' => $methods,
            'name' => $route->getName(),
            'domain' => $route->getDomain(),
            'middleware' => array_values(array_map('strval', $route->middleware())),
            'wheres' => $this->normaliseWheres($route->wheres),
            'parameters' => array_values($route->parameterNames()),
            'action' => $this->describeAction($action),
        ];
    }

    /**
     * @param  array<string, string>  $wheres
     * @return array<string, string>
     */
    private function normaliseWheres(array $wheres): array
    {
        $out = [];
        foreach ($wheres as $key => $value) {
            $out[(string) $key] = (string) $value;
        }
        ksort($out);

        return $out;
    }

    /**
     * @param  array<string, mixed>  $action
     * @return array<string, mixed>
     */
    private function describeAction(array $action): array
    {
        $uses = $action['uses'] ?? null;

        if (is_string($uses)) {
            [$controller, $method] = str_contains($uses, '@')
                ? explode('@', $uses, 2)
                : [$uses, '__invoke'];

            return [
                'kind' => 'controller',
                'controller' => $controller,
                'method' => $method,
            ];
        }

        if ($uses instanceof Closure) {
            try {
                $rf = new ReflectionFunction($uses);

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
