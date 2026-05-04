<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseA;

use Illuminate\Contracts\Config\Repository;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;

/**
 * Extracts a *redacted* slice of the framework config.
 *
 * We do NOT dump the entire config repository because it contains every
 * environment value, secret, and key the user has set. Instead we extract a
 * curated allowlist of structural keys (database connection drivers, queue
 * connections, broadcast drivers, mail transport, filesystem disks).
 *
 * Anything that could be a secret (`password`, `secret`, `token`, `key`,
 * `dsn`, `url`) is redacted to the literal string '«redacted»'.
 */
final class ConfigExtractor implements Extractor
{
    private const STRUCTURAL_PATHS = [
        'database.default',
        'database.connections',
        'queue.default',
        'queue.connections',
        'broadcasting.default',
        'broadcasting.connections',
        'mail.default',
        'mail.mailers',
        'filesystems.default',
        'filesystems.disks',
        'cache.default',
        'cache.stores',
        'session.driver',
        'session.connection',
        'auth.defaults',
        'auth.guards',
        'auth.providers',
    ];

    private const REDACT_KEYS = [
        'password', 'secret', 'token', 'key', 'dsn', 'url',
        'private_key', 'public_key', 'app_key', 'access_key',
    ];

    public function name(): string
    {
        return 'phase_a.config';
    }

    public function extract(ExtractionContext $context): void
    {
        /** @var Repository $config */
        $config = $context->app->make('config');

        $extracted = [];
        foreach (self::STRUCTURAL_PATHS as $path) {
            $value = $config->get($path);
            $extracted[$path] = $this->redact($value);
        }

        $context->document->setSection('config', $extracted);
    }

    private function redact(mixed $value): mixed
    {
        if (is_array($value)) {
            $out = [];
            foreach ($value as $k => $v) {
                $out[$k] = $this->shouldRedact((string) $k) ? '«redacted»' : $this->redact($v);
            }

            return $out;
        }

        return $value;
    }

    private function shouldRedact(string $key): bool
    {
        $lower = strtolower($key);

        foreach (self::REDACT_KEYS as $needle) {
            if (str_contains($lower, $needle)) {
                return true;
            }
        }

        return false;
    }
}
