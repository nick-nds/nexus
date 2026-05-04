<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseA;

use Illuminate\Contracts\Http\Kernel as HttpKernelContract;
use Illuminate\Foundation\Http\Kernel as HttpKernel;
use Illuminate\Routing\Router;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use ReflectionProperty;
use Throwable;

/**
 * Extracts global middleware, route middleware aliases, and middleware
 * groups from the HTTP kernel and the Router.
 *
 * Why both kernel and router: Laravel splits middleware between the kernel
 * (global, groups, aliases) and the router (which actually applies them).
 * The router's `middlewareGroups` and `middleware` properties duplicate the
 * kernel's view in newer Laravel versions, but reading both keeps us robust
 * across versions.
 */
final class MiddlewareExtractor implements Extractor
{
    public function name(): string
    {
        return 'phase_a.middleware';
    }

    public function extract(ExtractionContext $context): void
    {
        $global = [];
        $groups = [];
        $aliases = [];

        if ($context->app->bound(HttpKernelContract::class)) {
            $kernel = $context->app->make(HttpKernelContract::class);

            if ($kernel instanceof HttpKernel) {
                $global = $this->stringList($this->readProperty($kernel, 'middleware'));
                $groups = $this->stringMatrix($this->readProperty($kernel, 'middlewareGroups'));
                $aliases = $this->stringMap($this->readProperty($kernel, 'middlewareAliases'));

                if ($aliases === []) {
                    // Older Laravel versions used `routeMiddleware` instead.
                    $aliases = $this->stringMap($this->readProperty($kernel, 'routeMiddleware'));
                }
            }
        }

        // Fallback / supplement from the router.
        try {
            /** @var Router $router */
            $router = $context->app->make('router');
            if ($groups === []) {
                $groups = $this->stringMatrix($router->getMiddlewareGroups());
            }
            if ($aliases === []) {
                $aliases = $this->stringMap($router->getMiddleware());
            }
        } catch (Throwable) {
            // Router unavailable in some test apps; tolerate silently.
        }

        $context->document->setSection('middleware', [
            'global' => $global,
            'groups' => $groups,
            'aliases' => $aliases,
        ]);
    }

    /**
     * @return array<int|string, mixed>
     */
    private function readProperty(object $instance, string $name): array
    {
        try {
            $rp = new ReflectionProperty($instance, $name);
            /** @var array<int|string, mixed> $value */
            $value = $rp->getValue($instance);

            return $value;
        } catch (Throwable) {
            return [];
        }
    }

    /**
     * @param  array<int|string, mixed>  $raw
     * @return list<string>
     */
    private function stringList(array $raw): array
    {
        $out = [];
        foreach ($raw as $value) {
            if (is_string($value)) {
                $out[] = $value;
            }
        }
        sort($out);

        return $out;
    }

    /**
     * @param  array<int|string, mixed>  $raw
     * @return array<string, list<string>>
     */
    private function stringMatrix(array $raw): array
    {
        $out = [];
        foreach ($raw as $key => $value) {
            if (is_array($value)) {
                $out[(string) $key] = $this->stringList($value);
            }
        }
        ksort($out);

        return $out;
    }

    /**
     * @param  array<int|string, mixed>  $raw
     * @return array<string, string>
     */
    private function stringMap(array $raw): array
    {
        $out = [];
        foreach ($raw as $key => $value) {
            if (is_string($value)) {
                $out[(string) $key] = $value;
            }
        }
        ksort($out);

        return $out;
    }
}
