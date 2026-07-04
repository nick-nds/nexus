<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Expr\New_;
use PhpParser\Node\InterpolatedStringPart;
use PhpParser\Node\Name;
use PhpParser\Node\Scalar\InterpolatedString;
use PhpParser\Node\Scalar\String_;

/**
 * Detects broadcast channels referenced inside an event's
 * ``broadcastOn()`` method:
 *
 *   public function broadcastOn(): array
 *   {
 *       return [
 *           new Channel('orders'),
 *           new PrivateChannel("user.{$this->user->id}"),
 *           new PresenceChannel('lobby'),
 *       ];
 *   }
 *
 * Each ``new <Channel-class>(<name-arg>)`` expression *anywhere
 * inside* a ``broadcastOn`` method emits a ``broadcast_channel``
 * finding tagged with the channel name as ``$target`` and the
 * channel-class flavour in ``meta.channel_kind``.
 *
 * Why not bind to a return statement: events sometimes wrap the
 * channel construction in helpers, ternaries, or array literals;
 * walking the whole method body for ``new``-of-channel-classes
 * captures every case without trying to model PHP's expression tree.
 *
 * Dynamic channel names (interpolated variables only, no literal
 * prefix) are skipped - there's nothing static to record.
 */
final class BroadcastChannelVisitor extends ContextTrackingVisitor
{
    private const CHANNEL_CLASSES = [
        'Channel',
        'PrivateChannel',
        'PresenceChannel',
        'EncryptedPrivateChannel',
        'Illuminate\\Broadcasting\\Channel',
        'Illuminate\\Broadcasting\\PrivateChannel',
        'Illuminate\\Broadcasting\\PresenceChannel',
        'Illuminate\\Broadcasting\\EncryptedPrivateChannel',
    ];

    protected function processNode(Node $node): void
    {
        if (! ($node instanceof New_)) {
            return;
        }

        // Only inside a method literally named ``broadcastOn`` - keeps
        // the visitor scoped and avoids treating a random
        // ``new Channel('x')`` in unrelated code as a broadcast.
        if (! $this->insideBroadcastOnMethod()) {
            return;
        }

        if (! ($node->class instanceof Name)) {
            return;
        }

        $className = $node->class->toString();
        $kind = $this->classifyChannel($className);
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

        [$channelName, $form] = $this->resolveChannelName($first->value);
        if ($channelName === null) {
            return;
        }

        $this->emit(
            'broadcast_channel',
            $channelName,
            $node,
            ['channel_kind' => $kind, 'form' => $form],
        );
    }

    private function classifyChannel(string $className): ?string
    {
        // Return the bare flavour (Channel / PrivateChannel / …) so
        // the meta is consistent regardless of import style.
        foreach (self::CHANNEL_CLASSES as $candidate) {
            if ($className === $candidate || str_ends_with($candidate, '\\'.$className)) {
                return $this->shortName($candidate);
            }
        }

        return null;
    }

    private function shortName(string $fqn): string
    {
        $pos = strrpos($fqn, '\\');

        return $pos === false ? $fqn : substr($fqn, $pos + 1);
    }

    private function insideBroadcastOnMethod(): bool
    {
        return $this->currentMethod !== null
            && strtolower($this->currentMethod) === 'broadcaston';
    }

    /**
     * @return array{0: string|null, 1: string|null}
     */
    private function resolveChannelName(Node $value): array
    {
        if ($value instanceof String_) {
            return [$value->value, 'literal'];
        }

        if ($value instanceof Node\Expr\BinaryOp\Concat) {
            $head = $value;
            while ($head->left instanceof Node\Expr\BinaryOp\Concat) {
                $head = $head->left;
            }
            if ($head->left instanceof String_) {
                return [$head->left->value, 'prefix'];
            }
        }

        // Double-quoted strings with interpolations (``"user.{$id}"``)
        // parse to an InterpolatedString. Capture the leading literal
        // segment so the name still surfaces as ``"user."`` with a
        // prefix hint. (nikic/php-parser is pinned to ^5.0, so the
        // pre-5.0 ``Encapsed`` node names no longer apply.)
        if ($value instanceof InterpolatedString) {
            $first = $value->parts[0] ?? null;
            if ($first instanceof InterpolatedStringPart && is_string($first->value)) {
                return [$first->value, 'prefix'];
            }
        }

        return [null, null];
    }
}
