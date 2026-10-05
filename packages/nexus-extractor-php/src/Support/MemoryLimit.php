<?php

declare(strict_types=1);

namespace Nexus\Extractor\Support;

/**
 * Raises PHP's memory_limit for the extraction run.
 *
 * The CLI default (128M on many distros) is too small for real projects:
 * the whole reflection document is held in memory and encoded in one
 * json_encode call. A limit the user set higher, or unlimited, is kept.
 */
final class MemoryLimit
{
    public static function ensureAtLeast(string $minimum): void
    {
        $current = (string) ini_get('memory_limit');

        if ($current === '-1' || ini_parse_quantity($current) >= ini_parse_quantity($minimum)) {
            return;
        }

        ini_set('memory_limit', $minimum);
    }
}
