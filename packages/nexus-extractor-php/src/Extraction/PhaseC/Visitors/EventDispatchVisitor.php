<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Expr\ClassConstFetch;
use PhpParser\Node\Expr\FuncCall;
use PhpParser\Node\Expr\New_;
use PhpParser\Node\Expr\StaticCall;
use PhpParser\Node\Name;

/**
 * Detects event dispatches in three idiomatic forms:
 *
 *   event(SomeEvent::class)
 *   event(new SomeEvent(...))
 *   Event::dispatch(SomeEvent::class)
 *   SomeEvent::dispatch(...)   // via Dispatchable trait
 *
 * v1 used regex and missed cross-line patterns. The AST visitor catches all
 * forms uniformly because we look at typed nodes, not text.
 */
final class EventDispatchVisitor extends ContextTrackingVisitor
{
    protected function processNode(Node $node): void
    {
        if ($node instanceof FuncCall) {
            $this->handleFuncCall($node);

            return;
        }

        if ($node instanceof StaticCall) {
            $this->handleStaticCall($node);
        }
    }

    private function handleFuncCall(FuncCall $node): void
    {
        if (! ($node->name instanceof Name)) {
            return;
        }

        if ($node->name->toLowerString() !== 'event') {
            return;
        }

        if ($node->args === []) {
            return;
        }

        $arg = $node->args[0];
        if (! ($arg instanceof Node\Arg)) {
            return;
        }

        $target = $this->resolveEventTarget($arg->value);
        if ($target !== null) {
            $this->emit('event_dispatch', $target, $node);
        }
    }

    private function handleStaticCall(StaticCall $node): void
    {
        if (! ($node->class instanceof Name) || ! ($node->name instanceof Node\Identifier)) {
            return;
        }

        $methodName = $node->name->toLowerString();
        $className = $node->class->toString();

        // Event::dispatch(...) - Laravel facade
        if ($methodName === 'dispatch' && in_array($className, ['Event', '\\Event', 'Illuminate\\Support\\Facades\\Event'], true)) {
            if ($node->args !== []) {
                $arg = $node->args[0];
                if ($arg instanceof Node\Arg) {
                    $target = $this->resolveEventTarget($arg->value);
                    if ($target !== null) {
                        $this->emit('event_dispatch', $target, $node, ['form' => 'facade']);
                    }
                }
            }

            return;
        }

        // SomeEvent::dispatch(...) - Dispatchable trait
        // Skip Laravel facades so we don't double-count `Bus::dispatch` etc.
        if ($methodName === 'dispatch' && ! LaravelFacades::isDispatchFacade($className)) {
            $this->emit('event_dispatch', $className, $node, ['form' => 'trait']);
        }
    }

    private function resolveEventTarget(Node $value): ?string
    {
        if ($value instanceof ClassConstFetch && $value->class instanceof Name && $value->name instanceof Node\Identifier && $value->name->toLowerString() === 'class') {
            return $value->class->toString();
        }

        if ($value instanceof New_ && $value->class instanceof Name) {
            return $value->class->toString();
        }

        return null;
    }
}
