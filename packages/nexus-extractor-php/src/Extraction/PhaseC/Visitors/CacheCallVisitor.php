<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Expr\StaticCall;
use PhpParser\Node\Name;
use PhpParser\Node\Scalar\String_;

/**
 * Detects Cache facade reads and writes:
 *
 *   Cache::get('user.profile.42')           → cache_read
 *   Cache::has('rate-limit:'.$ip)           → cache_read (literal prefix only)
 *   Cache::pull('once-only')                → cache_read
 *   Cache::put('user.profile.42', $payload) → cache_write
 *   Cache::set('user.profile.42', $payload) → cache_write
 *   Cache::forever('site.config', $cfg)     → cache_write
 *   Cache::remember('feed', 60, fn () => …) → cache_write
 *   Cache::rememberForever('feed', fn …)    → cache_write
 *   Cache::increment('hits')                → cache_write
 *   Cache::decrement('hits')                → cache_write
 *   Cache::forget('user.profile.42')        → cache_write
 *
 * Only the *literal* portion of the cache key is captured. Patterns
 * like ``"user.{$id}"`` resolve to the prefix ``"user."`` plus a
 * meta hint that the key is composed; agents looking for "what
 * reads/writes user.* keys" still find them via prefix match. Fully
 * dynamic keys (a bare variable) are skipped - there's no useful
 * static target.
 */
final class CacheCallVisitor extends ContextTrackingVisitor
{
    private const READ_METHODS = ['get', 'has', 'pull', 'many', 'add', 'sear'];

    private const WRITE_METHODS = [
        'put',
        'set',
        'forever',
        'remember',
        'rememberforever',
        'increment',
        'decrement',
        'forget',
        'flush',
        'putmany',
    ];

    private const CACHE_FACADES = [
        'Cache',
        '\\Cache',
        'Illuminate\\Support\\Facades\\Cache',
    ];

    protected function processNode(Node $node): void
    {
        if (! ($node instanceof StaticCall)) {
            return;
        }

        if (! ($node->class instanceof Name) || ! ($node->name instanceof Node\Identifier)) {
            return;
        }

        $className = $node->class->toString();
        if (! in_array($className, self::CACHE_FACADES, true)) {
            return;
        }

        $method = $node->name->toLowerString();
        $kind = $this->classifyMethod($method);
        if ($kind === null) {
            return;
        }

        if ($node->args === []) {
            return;
        }

        $first = $node->args[0];
        if (! ($first instanceof Node\Arg)) {
            return;
        }

        [$key, $form] = $this->resolveKey($first->value);
        if ($key === null) {
            // Fully dynamic key - nothing structural to record.
            return;
        }

        $this->emit($kind, $key, $node, ['method' => $method, 'form' => $form]);
    }

    private function classifyMethod(string $method): ?string
    {
        if (in_array($method, self::READ_METHODS, true)) {
            return 'cache_read';
        }
        if (in_array($method, self::WRITE_METHODS, true)) {
            return 'cache_write';
        }

        return null;
    }

    /**
     * Extract the static part of a cache-key expression.
     *
     * Returns ``[$key, $form]`` where ``$form`` is one of:
     *
     * * ``"literal"`` - a plain string literal
     * * ``"prefix"`` - a concatenation whose left operand is literal;
     *   the captured key is just the literal prefix and the agent
     *   should treat it as a glob ``<prefix>*``.
     *
     * Returns ``[null, null]`` when nothing static is recoverable.
     *
     * @return array{0: string|null, 1: string|null}
     */
    private function resolveKey(Node $value): array
    {
        if ($value instanceof String_) {
            return [$value->value, 'literal'];
        }

        if ($value instanceof Node\Expr\BinaryOp\Concat) {
            // Walk the left chain looking for the longest literal head.
            $head = $value;
            while ($head->left instanceof Node\Expr\BinaryOp\Concat) {
                $head = $head->left;
            }
            if ($head->left instanceof String_) {
                return [$head->left->value, 'prefix'];
            }
        }

        return [null, null];
    }
}
