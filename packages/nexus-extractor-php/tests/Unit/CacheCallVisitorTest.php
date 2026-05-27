<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseC\AstAnalyzer;
use Nexus\Extractor\Extraction\PhaseC\Visitors\CacheCallVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\StaticAnalysisFinding;
use PHPUnit\Framework\TestCase;

final class CacheCallVisitorTest extends TestCase
{
    private function analyzer(): AstAnalyzer
    {
        return new AstAnalyzer([new CacheCallVisitor]);
    }

    public function test_get_with_string_literal_emits_cache_read(): void
    {
        $code = <<<'PHP'
        <?php
        use Illuminate\Support\Facades\Cache;
        Cache::get('user.profile.42');
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('cache_read', $findings[0]->kind);
        $this->assertSame('user.profile.42', $findings[0]->target);
        $this->assertSame(['method' => 'get', 'form' => 'literal'], $findings[0]->meta);
    }

    public function test_put_emits_cache_write(): void
    {
        $code = <<<'PHP'
        <?php
        Cache::put('site.config', $cfg, 3600);
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('cache_write', $findings[0]->kind);
        $this->assertSame('site.config', $findings[0]->target);
    }

    public function test_remember_emits_cache_write(): void
    {
        $code = <<<'PHP'
        <?php
        Cache::remember('feed.global', 60, fn () => []);
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('cache_write', $findings[0]->kind);
        $this->assertSame('feed.global', $findings[0]->target);
    }

    public function test_concat_key_captures_literal_prefix(): void
    {
        $code = <<<'PHP'
        <?php
        Cache::get('user.profile.' . $id);
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('cache_read', $findings[0]->kind);
        $this->assertSame('user.profile.', $findings[0]->target);
        $this->assertSame(['method' => 'get', 'form' => 'prefix'], $findings[0]->meta);
    }

    public function test_dynamic_key_emits_nothing(): void
    {
        $code = <<<'PHP'
        <?php
        Cache::get($key);   // bare variable - nothing static to capture
        PHP;

        $findings = $this->analyse($code);

        $this->assertSame([], $findings);
    }

    public function test_unrelated_class_method_calls_are_ignored(): void
    {
        $code = <<<'PHP'
        <?php
        // Different class, even though the method name matches.
        Repository::get('thing');
        Foo::put('x', 1);
        PHP;

        $findings = $this->analyse($code);

        $this->assertSame([], $findings);
    }

    public function test_qualified_facade_works(): void
    {
        $code = <<<'PHP'
        <?php
        \Illuminate\Support\Facades\Cache::get('x');
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('cache_read', $findings[0]->kind);
    }

    /**
     * @return list<StaticAnalysisFinding>
     */
    private function analyse(string $code): array
    {
        $result = $this->analyzer()->analyse('/tmp/cache-test.php', $code);
        $this->assertNull($result['error'], $result['error'] ?? '');

        return $result['findings'];
    }
}
