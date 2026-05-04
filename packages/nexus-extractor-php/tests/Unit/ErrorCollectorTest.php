<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Support\ErrorCollector;
use Nexus\Extractor\Support\ExtractionError;
use Nexus\Extractor\Support\ExtractionWarning;
use PHPUnit\Framework\TestCase;

final class ErrorCollectorTest extends TestCase
{
    public function test_starts_empty(): void
    {
        $c = new ErrorCollector;

        $this->assertFalse($c->hasErrors());
        $this->assertFalse($c->hasWarnings());
        $this->assertSame(0, $c->errorCount());
        $this->assertSame(0, $c->warningCount());
    }

    public function test_collects_warnings(): void
    {
        $c = new ErrorCollector;
        $c->warn(new ExtractionWarning('w1', 'message a'));
        $c->warn(new ExtractionWarning('w2', 'message b'));

        $this->assertSame(2, $c->warningCount());
        $this->assertTrue($c->hasWarnings());
        $this->assertFalse($c->hasErrors());
        $this->assertSame('message a', $c->warnings()[0]->message);
    }

    public function test_collects_errors(): void
    {
        $c = new ErrorCollector;
        $c->fail(new ExtractionError('e1', 'boom'));

        $this->assertTrue($c->hasErrors());
        $this->assertSame(1, $c->errorCount());
        $this->assertSame('boom', $c->errors()[0]->message);
    }

    public function test_warning_to_array_shape(): void
    {
        $w = new ExtractionWarning('code', 'message', '/tmp/x.php', 42, ['k' => 'v']);

        $this->assertSame(
            [
                'code' => 'code',
                'message' => 'message',
                'file' => '/tmp/x.php',
                'line' => 42,
                'context' => ['k' => 'v'],
            ],
            $w->toArray(),
        );
    }
}
