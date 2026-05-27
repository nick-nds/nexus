<?php

declare(strict_types=1);

namespace Nexus\Extractor\Output;

use Nexus\Extractor\Extraction\Support\NamespaceExclusionFilter;
use Nexus\Extractor\Extraction\Support\PackageScope;
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
     * @param  array{vendor: string, name: string, version: string, description: string|null, authors: list<array<string, string|null>>, license: string|null, homepage: string|null}  $info
     */
    public function setPackage(array $info): void
    {
        $this->package = [
            'vendor' => $info['vendor'],
            'name' => $info['name'],
            'version' => $info['version'],
            'description' => $info['description'] ?? null,
            'authors' => $info['authors'] ?? [],
            'license' => $info['license'] ?? null,
            'homepage' => $info['homepage'] ?? null,
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
     * Removes Workbench/Testbench/Orchestra noise from every section that
     * carries class names. Call this after the pipeline finishes in
     * package-extraction mode.
     *
     * Section shapes handled:
     *   - classes       → items[*].reflection.name
     *   - routes        → items[*].action.controller   (controller routes only)
     *   - events        → listeners[*].listeners[*].class (kind=class listeners)
     *   - gates_policies→ gates[*].callback.class, policies[*].policy
     *   - bindings      → bindings[*].concrete.class   (kind=class concretes)
     *   - static_analysis → findings[*].in_class
     *   - schedule      → events[*] - no class names (command string / closure)
     */
    public function applyNamespaceFilter(NamespaceExclusionFilter $filter): void
    {
        // classes section
        if (isset($this->sections['classes']['items']) && is_array($this->sections['classes']['items'])) {
            $this->sections['classes']['items'] = array_values(array_filter(
                $this->sections['classes']['items'],
                fn (array $item): bool => ! $filter->matches((string) ($item['reflection']['name'] ?? '')),
            ));
            $this->sections['classes']['count'] = count($this->sections['classes']['items']);
        }

        // routes section - drop routes registered by excluded code:
        //   - controller routes: filter by controller class name
        //   - closure routes:    filter by the closure's source file path
        if (isset($this->sections['routes']['items']) && is_array($this->sections['routes']['items'])) {
            $this->sections['routes']['items'] = array_values(array_filter(
                $this->sections['routes']['items'],
                function (array $item) use ($filter): bool {
                    $action = $item['action'] ?? [];
                    $kind = (string) ($action['kind'] ?? '');

                    if ($kind === 'controller') {
                        return ! $filter->matches((string) ($action['controller'] ?? ''));
                    }

                    if ($kind === 'closure' && isset($action['file']) && is_string($action['file'])) {
                        return ! $filter->matchesFilePath($action['file']);
                    }

                    return true;
                },
            ));
            $this->sections['routes']['count'] = count($this->sections['routes']['items']);
        }

        // events section - filter individual listeners
        if (isset($this->sections['events']['listeners']) && is_array($this->sections['events']['listeners'])) {
            $filtered = [];
            foreach ($this->sections['events']['listeners'] as $entry) {
                if (! is_array($entry)) {
                    continue;
                }

                $listeners = array_values(array_filter(
                    $entry['listeners'] ?? [],
                    function (array $l) use ($filter): bool {
                        if (($l['kind'] ?? '') === 'class') {
                            return ! $filter->matches((string) ($l['class'] ?? ''));
                        }

                        return true;
                    },
                ));

                // Drop entries for excluded event classes too
                $event = (string) ($entry['event'] ?? '');
                if ($filter->matches($event)) {
                    continue;
                }

                $filtered[] = array_merge($entry, ['listeners' => $listeners]);
            }

            $this->sections['events']['listeners'] = $filtered;
        }

        // gates_policies section
        if (isset($this->sections['gates_policies'])) {
            if (isset($this->sections['gates_policies']['gates']) && is_array($this->sections['gates_policies']['gates'])) {
                $this->sections['gates_policies']['gates'] = array_values(array_filter(
                    $this->sections['gates_policies']['gates'],
                    function (array $gate) use ($filter): bool {
                        $cb = $gate['callback'] ?? [];
                        if (($cb['kind'] ?? '') === 'class') {
                            return ! $filter->matches((string) ($cb['class'] ?? ''));
                        }

                        return true;
                    },
                ));
            }

            if (isset($this->sections['gates_policies']['policies']) && is_array($this->sections['gates_policies']['policies'])) {
                $this->sections['gates_policies']['policies'] = array_values(array_filter(
                    $this->sections['gates_policies']['policies'],
                    fn (array $p): bool => ! $filter->matches((string) ($p['policy'] ?? '')),
                ));
            }
        }

        // bindings section - filter kind=class concretes
        if (isset($this->sections['bindings']['bindings']) && is_array($this->sections['bindings']['bindings'])) {
            $this->sections['bindings']['bindings'] = array_values(array_filter(
                $this->sections['bindings']['bindings'],
                function (array $b) use ($filter): bool {
                    $concrete = $b['concrete'] ?? [];
                    if (($concrete['kind'] ?? '') === 'class') {
                        return ! $filter->matches((string) ($concrete['class'] ?? ''));
                    }

                    return true;
                },
            ));
            $this->sections['bindings']['summary']['binding_count'] = count($this->sections['bindings']['bindings']);
        }

        // static_analysis section
        if (isset($this->sections['static_analysis']['findings']) && is_array($this->sections['static_analysis']['findings'])) {
            $this->sections['static_analysis']['findings'] = array_values(array_filter(
                $this->sections['static_analysis']['findings'],
                fn (array $f): bool => ! $filter->matches((string) ($f['in_class'] ?? '')),
            ));
            $this->sections['static_analysis']['finding_count'] = count($this->sections['static_analysis']['findings']);
        }
    }

    /**
     * Restrict every section to entries that belong to the target package.
     *
     * The Phase A extractors (RouteExtractor, BindingExtractor,
     * EventListenerExtractor, GatePolicyExtractor, MiddlewareExtractor)
     * read Laravel's runtime state - they see every binding, route,
     * listener and gate the booted skeleton has registered, which
     * includes Laravel core (``Illuminate\…``), the framework's own
     * commands and middlewares, and any provider registered by
     * Testbench. None of that belongs in a package index.
     *
     * This filter is the inclusive counterpart of
     * :meth:`applyNamespaceFilter`. When a ``PackageScope`` is active,
     * the extractor calls both:
     *
     *   1. ``applyScopeFilter($scope)`` - keep only entries whose
     *      class lives under one of the scope's PSR-4 prefixes, and
     *      whose source file (for closure routes / static analysis)
     *      lives under the scope's ``vendorPath``.
     *   2. ``applyNamespaceFilter()`` - drop Workbench/Orchestra
     *      noise that survived (1) (e.g. when the user package
     *      legitimately references such a class).
     *
     * Closure routes without a file reference, and bindings whose
     * concrete is a closure or eager-resolved instance, pass through
     * - there is no class to match against. They are uncommon in
     * package-shaped code and treated as low-noise.
     */
    public function applyScopeFilter(PackageScope $scope): void
    {
        $vendorPathPrefix = rtrim($scope->vendorPath, '/').'/';
        $matchesFile = static fn (?string $file): bool => is_string($file)
            && $file !== ''
            && str_starts_with($file, $vendorPathPrefix);

        // classes section
        if (isset($this->sections['classes']['items']) && is_array($this->sections['classes']['items'])) {
            $this->sections['classes']['items'] = array_values(array_filter(
                $this->sections['classes']['items'],
                fn (array $item): bool => $scope->matchesNamespace(
                    (string) ($item['reflection']['name'] ?? ''),
                ),
            ));
            $this->sections['classes']['count'] = count($this->sections['classes']['items']);
        }

        // routes section - keep controller routes whose controller is
        // in scope; keep closure routes whose source file is in scope.
        if (isset($this->sections['routes']['items']) && is_array($this->sections['routes']['items'])) {
            $this->sections['routes']['items'] = array_values(array_filter(
                $this->sections['routes']['items'],
                function (array $item) use ($scope, $matchesFile): bool {
                    $action = $item['action'] ?? [];
                    $kind = (string) ($action['kind'] ?? '');

                    if ($kind === 'controller') {
                        return $scope->matchesNamespace((string) ($action['controller'] ?? ''));
                    }

                    if ($kind === 'closure') {
                        return $matchesFile($action['file'] ?? null);
                    }

                    return false;  // unknown kinds - drop conservatively
                },
            ));
            $this->sections['routes']['count'] = count($this->sections['routes']['items']);
        }

        // events section - keep events whose class is in scope; within
        // each, keep listeners whose handler class is in scope.
        if (isset($this->sections['events']['listeners']) && is_array($this->sections['events']['listeners'])) {
            $filtered = [];
            foreach ($this->sections['events']['listeners'] as $entry) {
                if (! is_array($entry)) {
                    continue;
                }
                $event = (string) ($entry['event'] ?? '');
                if (! $scope->matchesNamespace($event)) {
                    continue;
                }
                $listeners = array_values(array_filter(
                    $entry['listeners'] ?? [],
                    function (array $l) use ($scope): bool {
                        if (($l['kind'] ?? '') === 'class') {
                            return $scope->matchesNamespace((string) ($l['class'] ?? ''));
                        }

                        return false;
                    },
                ));
                $filtered[] = array_merge($entry, ['listeners' => $listeners]);
            }
            $this->sections['events']['listeners'] = $filtered;
        }

        // gates_policies section
        if (isset($this->sections['gates_policies'])) {
            if (isset($this->sections['gates_policies']['gates']) && is_array($this->sections['gates_policies']['gates'])) {
                $this->sections['gates_policies']['gates'] = array_values(array_filter(
                    $this->sections['gates_policies']['gates'],
                    function (array $gate) use ($scope): bool {
                        $cb = $gate['callback'] ?? [];
                        if (($cb['kind'] ?? '') === 'class') {
                            return $scope->matchesNamespace((string) ($cb['class'] ?? ''));
                        }

                        return false;
                    },
                ));
            }
            if (isset($this->sections['gates_policies']['policies']) && is_array($this->sections['gates_policies']['policies'])) {
                $this->sections['gates_policies']['policies'] = array_values(array_filter(
                    $this->sections['gates_policies']['policies'],
                    fn (array $p): bool => $scope->matchesNamespace(
                        (string) ($p['policy'] ?? ''),
                    ),
                ));
            }
        }

        // bindings section - keep entries whose concrete class is in
        // scope. Non-class concretes (closures, eager instances) have
        // no class to check; drop them so Laravel-core eager
        // instances (``Illuminate\\…``) don't leak in.
        if (isset($this->sections['bindings']['bindings']) && is_array($this->sections['bindings']['bindings'])) {
            $this->sections['bindings']['bindings'] = array_values(array_filter(
                $this->sections['bindings']['bindings'],
                function (array $b) use ($scope): bool {
                    $concrete = $b['concrete'] ?? [];
                    if (($concrete['kind'] ?? '') === 'class') {
                        return $scope->matchesNamespace((string) ($concrete['class'] ?? ''));
                    }

                    return false;
                },
            ));
            if (isset($this->sections['bindings']['summary'])) {
                $this->sections['bindings']['summary']['binding_count'] = count($this->sections['bindings']['bindings']);
            }
        }

        // middleware section - drop everything not in scope. Middleware
        // entries reference a class. Closure middleware passes through
        // when bindings did because there's nothing to check; the
        // scope filter would over-prune in that case, but Laravel-core
        // middleware (``Illuminate\\…``) is the dominant noise we
        // want gone.
        if (isset($this->sections['middleware']['items']) && is_array($this->sections['middleware']['items'])) {
            $this->sections['middleware']['items'] = array_values(array_filter(
                $this->sections['middleware']['items'],
                fn (array $m): bool => $scope->matchesNamespace((string) ($m['class'] ?? '')),
            ));
            $this->sections['middleware']['count'] = count($this->sections['middleware']['items']);
        }

        // static_analysis findings - keep only findings whose
        // containing class is in scope.
        if (isset($this->sections['static_analysis']['findings']) && is_array($this->sections['static_analysis']['findings'])) {
            $this->sections['static_analysis']['findings'] = array_values(array_filter(
                $this->sections['static_analysis']['findings'],
                fn (array $f): bool => $scope->matchesNamespace(
                    (string) ($f['in_class'] ?? ''),
                ),
            ));
            $this->sections['static_analysis']['finding_count'] = count($this->sections['static_analysis']['findings']);
        }
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
