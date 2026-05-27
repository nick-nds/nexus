<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\Support;

/**
 * Drops Workbench/Testbench/Orchestra noise from the package extraction
 * output. The booted Testbench skeleton registers these providers so the
 * extraction can run; their classes/routes/listeners are not part of the
 * target package's surface and would mislead downstream queries if left
 * in the index.
 *
 * Filter is namespace-prefix based and only matches at namespace
 * boundaries - `MyOrg\Workbench\X` is NOT filtered because the filter
 * looks for the exact `Workbench\` root segment.
 */
final readonly class NamespaceExclusionFilter
{
    /**
     * @var list<string>
     */
    public const EXCLUDED_PREFIXES = [
        'Workbench\\',
        'Orchestra\\Testbench\\',
        'Orchestra\\Workbench\\',
        'Orchestra\\Sidekick\\',
        'Orchestra\\Canvas\\',
    ];

    public function matches(string $fullyQualifiedClassName): bool
    {
        foreach (self::EXCLUDED_PREFIXES as $prefix) {
            if (str_starts_with($fullyQualifiedClassName, $prefix)) {
                return true;
            }
        }

        return false;
    }

    /**
     * Returns true when the given absolute file path appears to be inside
     * a Workbench/Testbench/Orchestra source tree.
     *
     * The heuristic converts each namespace prefix to a path segment (replacing
     * `\` with `/`) and checks whether the normalised file path contains that
     * segment as a directory component. This handles both vendor installs and
     * local workbench app layouts.
     */
    public function matchesFilePath(string $filePath): bool
    {
        $normalised = str_replace('\\', '/', $filePath);

        foreach (self::EXCLUDED_PREFIXES as $prefix) {
            $pathSegment = str_replace('\\', '/', rtrim($prefix, '\\'));
            if (str_contains($normalised, '/'.$pathSegment.'/') || str_contains($normalised, '/'.$pathSegment)) {
                return true;
            }
        }

        return false;
    }

    /**
     * @template T of array<string, mixed>
     *
     * @param  list<T>  $items
     * @return list<T>
     */
    public function filterByName(array $items, string $key): array
    {
        return array_values(array_filter(
            $items,
            fn (array $item): bool => isset($item[$key])
                && is_string($item[$key])
                && ! $this->matches($item[$key]),
        ));
    }
}
