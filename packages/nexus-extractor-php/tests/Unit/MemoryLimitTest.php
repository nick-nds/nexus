<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Support\MemoryLimit;
use PHPUnit\Framework\TestCase;

final class MemoryLimitTest extends TestCase
{
    private string $original;

    protected function setUp(): void
    {
        $this->original = (string) ini_get('memory_limit');
    }

    protected function tearDown(): void
    {
        ini_set('memory_limit', $this->original);
    }

    public function test_raises_a_lower_limit_to_the_minimum(): void
    {
        ini_set('memory_limit', '512M');

        MemoryLimit::ensureAtLeast('1G');

        $this->assertSame('1G', ini_get('memory_limit'));
    }

    public function test_leaves_a_higher_limit_alone(): void
    {
        ini_set('memory_limit', '2G');

        MemoryLimit::ensureAtLeast('1G');

        $this->assertSame('2G', ini_get('memory_limit'));
    }

    public function test_leaves_an_unlimited_limit_alone(): void
    {
        ini_set('memory_limit', '-1');

        MemoryLimit::ensureAtLeast('1G');

        $this->assertSame('-1', ini_get('memory_limit'));
    }
}
