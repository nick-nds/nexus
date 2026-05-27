<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseB;

use Composer\Autoload\ClassLoader;
use Nexus\Extractor\Extraction\Support\PackageScope;
use Throwable;

/**
 * Discovers project classes via Composer's autoload class map.
 *
 * Why the class map and not a directory glob: glob-based discovery requires
 * knowing where classes live (the v1 mistake we are explicitly fixing). The
 * class map is the authoritative list of every PSR-4/PSR-0 class Composer
 * has resolved, and it works for any project layout.
 *
 * Vendor classes are excluded by default. The caller can opt in via
 * `$includeVendor = true` or pass a `$vendorAllowlist` of vendor/package
 * prefixes to selectively include.
 */
final class ClassMapWalker
{
    /**
     * @param  list<string>  $vendorAllowlist  e.g. ['spatie/laravel-permission']
     * @return list<array{class: string, file: string, source: 'project'|'vendor'}>
     */
    public function walk(string $basePath, bool $includeVendor, array $vendorAllowlist, bool $includeTests = false, ?PackageScope $scope = null): array
    {
        $loader = $this->locateLoader($basePath);

        if ($loader === null) {
            return [];
        }

        $classMap = $loader->getClassMap();

        $items = [];
        $base = rtrim($basePath, '/');
        $vendorDir = $base.'/vendor/';
        $testsDirs = [$base.'/tests/', $base.'/Tests/'];

        foreach ($classMap as $class => $file) {
            try {
                $absolute = realpath($file);
                if ($absolute === false) {
                    continue;
                }
            } catch (Throwable) {
                continue;
            }

            // Vendor detection runs against the resolved ``realpath``
            // only. Composer's classmap stores entries as paths relative
            // to ``vendor/composer/`` (e.g.
            // ``/var/www/vendor/composer/../../app/Foo.php``), so the
            // original ``$file`` value almost always lives under
            // ``vendor/`` - using it for vendor detection would flag
            // every class in the project as vendor and exclude it.
            //
            // ``realpath()`` collapses the ``..`` segments and reveals
            // the actual source location; that's the right thing to
            // check against the project's ``vendor/`` directory. The
            // path-repository + ``symlink: true`` case (which earlier
            // motivated a dual check) resolves to OUTSIDE ``vendor/``,
            // so the linked package is correctly treated as project
            // code; the defensive ``/tests/fixtures/`` filter below
            // catches any test scaffolding that comes along for the
            // ride.
            $isVendor = str_starts_with($absolute, $vendorDir);

            // When a PackageScope is active, only entries under
            // ``$scope->vendorPath`` belong in the index. Compute the
            // in-scope flag once and use it as the authoritative
            // include signal - it overrides every project-mode noise
            // filter below so a target package whose realpath happens
            // to live under ``/tests/fixtures/`` (e.g. the synthetic
            // sample-package fixture, or any third-party clone the
            // user keeps in a test-shaped directory) is not dropped.
            $inScope = $scope !== null
                && str_starts_with($absolute, rtrim($scope->vendorPath, '/').'/');

            if ($scope !== null && ! $inScope) {
                continue;
            }

            // The project-mode noise filters (vendor/allowlist, project
            // tests, ``/tests/fixtures/`` defence) only apply when we
            // are NOT in package-extraction scope. Inside an active
            // scope, the scope filter above is the sole authority.
            if (! $inScope) {
                if ($scope === null && $isVendor && ! $includeVendor && ! $this->matchesAllowlist($absolute, $vendorDir, $vendorAllowlist)) {
                    continue;
                }

                // Skip the project's tests by default. Test classes often
                // include intentionally-broken fakes and non-loadable
                // stubs that would trigger PHP fatal errors at
                // class-declaration time (not catchable by try/catch).
                // The agent use case is also uninterested in test doubles
                // for production code understanding. Users can opt in
                // via --include-tests.
                if (! $includeTests && ! $isVendor && $this->isInTestsDir($absolute, $testsDirs)) {
                    continue;
                }

                // Defence in depth: drop any path containing
                // ``/tests/fixtures/`` even when the upstream source
                // lives outside ``vendor/``. Composer path repositories
                // with ``symlink: true`` resolve a package's classmap
                // to the package's actual location, so dev-only fixture
                // autoload entries (e.g. ``SampleApp\\`` mapped to a
                // package's ``tests/fixtures/sample-app/``) leak into
                // the consumer's classmap. We never want those in the
                // index regardless of how Composer routed them.
                if (! $includeTests && str_contains($absolute, '/tests/fixtures/')) {
                    continue;
                }
            }

            $items[] = [
                'class' => (string) $class,
                'file' => $absolute,
                'source' => $isVendor ? 'vendor' : 'project',
            ];
        }

        usort($items, static fn (array $a, array $b): int => strcmp($a['class'], $b['class']));

        return $items;
    }

    /**
     * @param  list<string>  $testsDirs
     */
    private function isInTestsDir(string $absolute, array $testsDirs): bool
    {
        foreach ($testsDirs as $dir) {
            if (str_starts_with($absolute, $dir)) {
                return true;
            }
        }

        return false;
    }

    private function locateLoader(string $basePath): ?ClassLoader
    {
        $candidate = rtrim($basePath, '/').'/vendor/autoload.php';

        if (! is_file($candidate)) {
            return null;
        }

        /** @var mixed $maybeLoader */
        $maybeLoader = require $candidate;

        return $maybeLoader instanceof ClassLoader ? $maybeLoader : null;
    }

    /**
     * @param  list<string>  $allowlist
     */
    private function matchesAllowlist(string $absolutePath, string $vendorDir, array $allowlist): bool
    {
        if ($allowlist === []) {
            return false;
        }

        $relative = substr($absolutePath, strlen($vendorDir));

        foreach ($allowlist as $package) {
            $prefix = rtrim($package, '/').'/';
            if (str_starts_with($relative, $prefix)) {
                return true;
            }
        }

        return false;
    }
}
