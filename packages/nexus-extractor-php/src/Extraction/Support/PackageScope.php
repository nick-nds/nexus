<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\Support;

/**
 * Immutable value object describing the package being indexed in
 * package-extraction mode. Resolved from the target's composer.json
 * and the location it was installed to under the booted skeleton's
 * vendor/ directory.
 *
 * @phpstan-type Psr4Map array<string, string>
 */
final readonly class PackageScope
{
    /**
     * @param  Psr4Map  $namespaces  PSR-4 prefix → relative source dir, from the target's composer.json autoload.psr-4
     */
    public function __construct(
        public string $vendor,
        public string $name,
        public string $version,
        public string $vendorPath,
        public array $namespaces,
    ) {}

    public function fullName(): string
    {
        return $this->vendor.'/'.$this->name;
    }

    public function matchesNamespace(string $fullyQualifiedClassName): bool
    {
        foreach ($this->namespaces as $prefix => $_) {
            if (str_starts_with($fullyQualifiedClassName, $prefix)) {
                return true;
            }
        }

        return false;
    }
}
