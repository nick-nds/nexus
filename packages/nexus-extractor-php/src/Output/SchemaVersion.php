<?php

declare(strict_types=1);

namespace Nexus\Extractor\Output;

/**
 * Reflection JSON schema version.
 *
 * The Python pipeline reads `schema_version` and refuses to load documents
 * whose major version it does not understand. Bump MAJOR for breaking changes
 * and MINOR for additive ones.
 */
final class SchemaVersion
{
    public const MAJOR = 2;

    public const MINOR = 4;

    public const PATCH = 0;

    public static function string(): string
    {
        return sprintf('%d.%d.%d', self::MAJOR, self::MINOR, self::PATCH);
    }
}
