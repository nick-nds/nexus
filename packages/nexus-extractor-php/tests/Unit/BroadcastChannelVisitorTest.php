<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseC\AstAnalyzer;
use Nexus\Extractor\Extraction\PhaseC\Visitors\BroadcastChannelVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\StaticAnalysisFinding;
use PHPUnit\Framework\TestCase;

final class BroadcastChannelVisitorTest extends TestCase
{
    private function analyzer(): AstAnalyzer
    {
        return new AstAnalyzer([new BroadcastChannelVisitor]);
    }

    public function test_public_channel_in_broadcast_on_emits_finding(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Events;
        use Illuminate\Broadcasting\Channel;

        class OrderPlaced {
            public function broadcastOn(): array {
                return [new Channel('orders')];
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('broadcast_channel', $findings[0]->kind);
        $this->assertSame('orders', $findings[0]->target);
        $this->assertSame(
            ['channel_kind' => 'Channel', 'form' => 'literal'],
            $findings[0]->meta,
        );
    }

    public function test_private_channel_kind_is_recorded(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Events;
        use Illuminate\Broadcasting\PrivateChannel;

        class UserUpdated {
            public function broadcastOn(): array {
                return [new PrivateChannel('user.42')];
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('PrivateChannel', $findings[0]->meta['channel_kind']);
    }

    public function test_multiple_channels_each_emit_a_finding(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Events;
        use Illuminate\Broadcasting\Channel;
        use Illuminate\Broadcasting\PresenceChannel;

        class GameTick {
            public function broadcastOn(): array {
                return [new Channel('global'), new PresenceChannel('lobby')];
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(2, $findings);
        $names = array_map(static fn (StaticAnalysisFinding $f): string => $f->target ?? '', $findings);
        sort($names);
        $this->assertSame(['global', 'lobby'], $names);
    }

    public function test_interpolated_channel_name_captures_prefix(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Events;
        use Illuminate\Broadcasting\PrivateChannel;

        class UserNotice {
            public int $userId = 0;
            public function broadcastOn(): array {
                return [new PrivateChannel("user.{$this->userId}")];
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('user.', $findings[0]->target);
        $this->assertSame('prefix', $findings[0]->meta['form']);
    }

    public function test_channel_outside_broadcast_on_is_ignored(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Services;
        use Illuminate\Broadcasting\Channel;

        class Bootstrapper {
            public function init(): void {
                $c = new Channel('not.a.broadcast');
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertSame([], $findings);
    }

    public function test_non_channel_classes_in_broadcast_on_are_ignored(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Events;

        class Anything {
            public function broadcastOn(): array {
                // ``new SomeOther('x')`` is unrelated to broadcasting
                return [new SomeOther('x')];
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertSame([], $findings);
    }

    /**
     * @return list<StaticAnalysisFinding>
     */
    private function analyse(string $code): array
    {
        $result = $this->analyzer()->analyse('/tmp/broadcast-test.php', $code);
        $this->assertNull($result['error'], $result['error'] ?? '');

        return $result['findings'];
    }
}
