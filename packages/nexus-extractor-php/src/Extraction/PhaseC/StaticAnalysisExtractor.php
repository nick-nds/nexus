<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC;

use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\BroadcastChannelVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\CacheCallVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\ContainerBindingVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\EventDispatchVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\JobDispatchVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\ObserverRegistrationVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\PolicyUseVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\ValidationRuleVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\ViewReturnVisitor;
use Nexus\Extractor\Extraction\Support\PackageScope;
use Nexus\Extractor\Support\ExtractionWarning;
use Throwable;

/**
 * Runs the AST visitors over every project PHP file and emits a flat list
 * of findings into the reflection document under the `static_analysis`
 * section.
 *
 * Performance note: this is the slowest pass. Each file is parsed once via
 * nikic and traversed once with all visitors attached, so we pay the parse
 * cost a single time per file regardless of how many visitors are added.
 */
final class StaticAnalysisExtractor implements Extractor
{
    public function __construct(
        private readonly FileScanner $scanner = new FileScanner,
        private readonly ?AstAnalyzer $analyzer = null,
    ) {}

    public function name(): string
    {
        return 'phase_c.static_analysis';
    }

    public function extract(ExtractionContext $context): void
    {
        $analyzer = $this->analyzer ?? new AstAnalyzer([
            new EventDispatchVisitor,
            new JobDispatchVisitor,
            new ViewReturnVisitor,
            new ValidationRuleVisitor,
            new PolicyUseVisitor,
            new ObserverRegistrationVisitor,
            new CacheCallVisitor,
            new BroadcastChannelVisitor,
            new ContainerBindingVisitor,
        ]);

        $roots = $this->resolveFileRoots($context->package);
        $files = $roots !== []
            ? $this->scanner->scanRoots($roots)
            : $this->scanner->scan($context->app->basePath());
        $context->progress->info(sprintf('Scanning %d PHP files for static analysis findings.', count($files)));

        $findings = [];
        $byKind = [];

        foreach ($files as $file) {
            try {
                $source = @file_get_contents($file);
            } catch (Throwable $e) {
                $context->errors->warn(new ExtractionWarning(
                    code: 'file_read_failed',
                    message: $e->getMessage(),
                    file: $file,
                ));

                continue;
            }

            if ($source === false) {
                $context->errors->warn(new ExtractionWarning(
                    code: 'file_read_failed',
                    message: 'Could not read file.',
                    file: $file,
                ));

                continue;
            }

            $result = $analyzer->analyse($file, $source);

            if ($result['error'] !== null) {
                $context->errors->warn(new ExtractionWarning(
                    code: 'ast_parse_failed',
                    message: $result['error'],
                    file: $file,
                ));

                continue;
            }

            foreach ($result['findings'] as $finding) {
                $findings[] = $finding->toArray();
                $byKind[$finding->kind] = ($byKind[$finding->kind] ?? 0) + 1;
            }
        }

        // Stable ordering: group by kind, then file, then line.
        usort($findings, static function (array $a, array $b): int {
            return [(string) $a['kind'], (string) ($a['file'] ?? ''), (int) ($a['line'] ?? 0)]
                <=> [(string) $b['kind'], (string) ($b['file'] ?? ''), (int) ($b['line'] ?? 0)];
        });

        ksort($byKind);

        $context->document->setSection('static_analysis', [
            'file_count' => count($files),
            'finding_count' => count($findings),
            'by_kind' => $byKind,
            'findings' => $findings,
        ]);
    }

    /**
     * Returns the list of absolute directory paths to scan for PHP files.
     *
     * When a PackageScope is provided, only the PSR-4 source directories
     * declared by the package (vendor_path/<rel_path> for each entry in
     * scope.namespaces) are returned - restricting the AST scan to the
     * package's own source tree and nothing else.
     *
     * When scope is null (project-mode), an empty array is returned and the
     * caller falls back to the full project scan via FileScanner::scan().
     *
     * @return list<string> absolute directory paths that exist on disk
     */
    public function resolveFileRoots(?PackageScope $scope): array
    {
        if ($scope === null) {
            return [];
        }

        $roots = [];

        foreach ($scope->namespaces as $relPath) {
            $abs = rtrim($scope->vendorPath, '/').'/'.ltrim($relPath, '/');

            if (is_dir($abs)) {
                $roots[] = $abs;
            }
        }

        return $roots;
    }
}
