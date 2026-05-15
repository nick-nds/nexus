<?php

declare(strict_types=1);

namespace Nexus\Extractor\Output;

use Nexus\Extractor\Support\ErrorCollector;

/**
 * The mutable, in-memory model of the reflection.json document.
 *
 * Each phase populates a section. Persistence happens once at the end via
 * {@see JsonWriter}, which serialises the result through {@see toArray()}.
 *
 * Sections are stored as raw arrays rather than typed objects: the document
 * is a transport format, not a domain model. The Python pipeline owns the
 * downstream typed representation.
 *
 * @phpstan-type SectionData array<string, mixed>
 */
final class ReflectionDocument
{
    /** @var array<string, mixed> */
    private array $project = [];

    /** @var array<string, array<string, mixed>> */
    private array $sections = [];

    private string $kind = 'project';

    /** @var array{vendor: string, name: string, version: string}|null */
    private ?array $package = null;

    public function __construct(
        private readonly ErrorCollector $errors,
    ) {}

    /**
     * @param  array<string, mixed>  $project
     */
    public function setProject(array $project): void
    {
        $this->project = $project;
    }

    /**
     * @param  array{vendor: string, name: string, version: string}  $info
     */
    public function setPackage(array $info): void
    {
        $this->package = [
            'vendor' => $info['vendor'],
            'name' => $info['name'],
            'version' => $info['version'],
        ];
        $this->kind = 'package';
    }

    /**
     * @param  array<string, mixed>  $data
     */
    public function setSection(string $name, array $data): void
    {
        $this->sections[$name] = $data;
    }

    /**
     * @return array<string, mixed>|null
     */
    public function section(string $name): ?array
    {
        return $this->sections[$name] ?? null;
    }

    public function errors(): ErrorCollector
    {
        return $this->errors;
    }

    /**
     * @return array<string, mixed>
     */
    public function toArray(): array
    {
        return [
            'schema_version' => SchemaVersion::string(),
            'generated_at' => gmdate('c'),
            'kind' => $this->kind,
            'project' => $this->project,
            'package' => $this->package,
            'sections' => $this->sections,
            'warnings' => array_map(
                static fn ($w) => $w->toArray(),
                $this->errors->warnings(),
            ),
            'errors' => array_map(
                static fn ($e) => $e->toArray(),
                $this->errors->errors(),
            ),
            'summary' => [
                'sections' => array_keys($this->sections),
                'warning_count' => $this->errors->warningCount(),
                'error_count' => $this->errors->errorCount(),
            ],
        ];
    }
}
