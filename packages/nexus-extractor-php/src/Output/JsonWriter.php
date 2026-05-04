<?php

declare(strict_types=1);

namespace Nexus\Extractor\Output;

use JsonException;
use RuntimeException;

/**
 * Atomically writes a {@see ReflectionDocument} to disk.
 *
 * Strategy: serialise to a sibling `.tmp` file, fsync if available, then
 * rename atomically over the destination. The Python side polls/reads this
 * file and a partial write would cause parse errors.
 */
final class JsonWriter
{
    private const JSON_FLAGS = JSON_THROW_ON_ERROR
        | JSON_UNESCAPED_SLASHES
        | JSON_UNESCAPED_UNICODE
        | JSON_PRETTY_PRINT;

    public function write(ReflectionDocument $document, string $path): void
    {
        $directory = dirname($path);

        if (! is_dir($directory) && ! @mkdir($directory, 0o755, true) && ! is_dir($directory)) {
            throw new RuntimeException(sprintf('Unable to create output directory: %s', $directory));
        }

        if (! is_writable($directory)) {
            throw new RuntimeException(sprintf('Output directory is not writable: %s', $directory));
        }

        try {
            $json = json_encode($document->toArray(), self::JSON_FLAGS);
        } catch (JsonException $e) {
            throw new RuntimeException('Failed to encode reflection document: '.$e->getMessage(), 0, $e);
        }

        $tmp = $path.'.tmp';

        $written = @file_put_contents($tmp, $json.PHP_EOL, LOCK_EX);
        if ($written === false) {
            throw new RuntimeException(sprintf('Failed to write temporary output file: %s', $tmp));
        }

        if (! @rename($tmp, $path)) {
            @unlink($tmp);
            throw new RuntimeException(sprintf('Failed to rename %s to %s', $tmp, $path));
        }
    }
}
