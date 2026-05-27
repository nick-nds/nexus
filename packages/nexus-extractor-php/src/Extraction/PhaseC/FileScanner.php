<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC;

use FilesystemIterator;
use RecursiveCallbackFilterIterator;
use RecursiveDirectoryIterator;
use RecursiveIteratorIterator;
use SplFileInfo;
use Throwable;

/**
 * Recursively enumerates `.php` files under a project's source directories.
 *
 * Why not just walk the class map: AST analysis needs the *physical files*
 * including those that contain multiple classes, top-level statements, or
 * helpers. The class map only knows declared classes.
 *
 * Excluded by default: anything under `vendor/`, `node_modules/`,
 * `storage/`, `bootstrap/cache/`, and any directory whose basename starts
 * with `.`.
 */
final class FileScanner
{
    /**
     * Directory basenames that are pruned wherever they appear in the tree.
     *
     * `vendor` and `node_modules` are dependency directories - a Laravel
     * monorepo may have nested ones inside `packages/<pkg>/vendor`. Pruning
     * at any depth is the only way to keep scans bounded on real projects.
     * VCS / editor directories follow the same rule.
     */
    private const EXCLUDED_DIR_NAMES = [
        'vendor',
        'node_modules',
        '.git',
        '.idea',
        '.vscode',
        '.svn',
        '.hg',
    ];

    /**
     * Project paths (relative to base) pruned only at the top level. These
     * are Laravel-specific conventions; we don't want to also prune a feature
     * module that happens to be called `Storage` or `Public`.
     */
    private const EXCLUDED_TOP_PATHS = [
        'storage',
        'public',
        'bootstrap/cache',
        'build',
    ];

    /**
     * Scans each directory in $roots independently and returns a merged,
     * sorted, deduplicated list of absolute PHP file paths.
     *
     * Used by StaticAnalysisExtractor when a PackageScope is set: the roots
     * are the package's PSR-4 source directories (vendor_path/<rel> for each
     * namespace entry). The same exclusion rules as scan() apply within each
     * root, but EXCLUDED_TOP_PATHS are evaluated relative to each root rather
     * than a single project base.
     *
     * @param  list<string>  $roots  absolute directory paths
     * @return list<string> absolute file paths
     */
    public function scanRoots(array $roots): array
    {
        $all = [];

        foreach ($roots as $root) {
            foreach ($this->scan($root) as $file) {
                $all[] = $file;
            }
        }

        $all = array_values(array_unique($all));
        sort($all);

        return $all;
    }

    /**
     * @return list<string> absolute file paths
     */
    public function scan(string $basePath): array
    {
        $base = rtrim($basePath, DIRECTORY_SEPARATOR);
        $excludedTopAbsolute = array_map(
            static fn (string $p): string => $base.DIRECTORY_SEPARATOR.str_replace('/', DIRECTORY_SEPARATOR, $p),
            self::EXCLUDED_TOP_PATHS,
        );

        try {
            // Important:
            //  - SKIP_DOTS: never see "." or ".."
            //  - NO FOLLOW_SYMLINKS: a symlinked package in vendor/ that
            //    points back at the project would create an infinite walk;
            //    even outside that case, symlink-following can cycle.
            $directory = new RecursiveDirectoryIterator(
                $base,
                FilesystemIterator::SKIP_DOTS | FilesystemIterator::CURRENT_AS_FILEINFO,
            );

            $filtered = new RecursiveCallbackFilterIterator(
                $directory,
                function (SplFileInfo $current) use ($excludedTopAbsolute): bool {
                    // Reject anything that resolves through a symlink - both
                    // regular files and directories. Prevents cycles and
                    // keeps the scan deterministic across machines.
                    if ($current->isLink()) {
                        return false;
                    }

                    if ($current->isDir()) {
                        $basename = $current->getFilename();
                        if (in_array($basename, self::EXCLUDED_DIR_NAMES, true)) {
                            return false;
                        }

                        // Prune ``tests/fixtures/`` at any depth. A package
                        // installed via Composer's ``path`` repository with
                        // ``symlink: true`` sits at its real source location
                        // (often outside ``vendor/``); its dev-only fixture
                        // PHP files would otherwise be scanned as if they
                        // belonged to the consumer's project. The pattern
                        // matches "directory named ``fixtures`` whose parent
                        // is named ``tests``".
                        if ($basename === 'fixtures' && basename(dirname($current->getPathname())) === 'tests') {
                            return false;
                        }

                        return ! in_array(rtrim($current->getPathname(), DIRECTORY_SEPARATOR), $excludedTopAbsolute, true);
                    }

                    return $current->getExtension() === 'php';
                },
            );

            $iterator = new RecursiveIteratorIterator(
                $filtered,
                RecursiveIteratorIterator::SELF_FIRST,
            );
        } catch (Throwable) {
            return [];
        }

        $files = [];

        /** @var SplFileInfo $info */
        foreach ($iterator as $info) {
            if (! $info->isFile()) {
                continue;
            }

            $files[] = $info->getPathname();
        }

        sort($files);

        return $files;
    }
}
