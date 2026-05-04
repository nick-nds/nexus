<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Support\CurrentClassTracker;
use PHPUnit\Framework\TestCase;

final class CurrentClassTrackerTest extends TestCase
{
    public function test_starts_null(): void
    {
        $this->assertNull((new CurrentClassTracker)->current());
    }

    public function test_set_and_current_round_trip(): void
    {
        $t = new CurrentClassTracker;
        $t->set('App\\Models\\User');

        $this->assertSame('App\\Models\\User', $t->current());
    }

    public function test_clear_resets_to_null(): void
    {
        $t = new CurrentClassTracker;
        $t->set('Foo');
        $t->clear();

        $this->assertNull($t->current());
    }
}
