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
 * Detects job and notification dispatches:
 *
 *   dispatch(new SomeJob(...))
 *   dispatch(SomeJob::class)
 *   SomeJob::dispatch(...)
 *   Bus::dispatch(new SomeJob(...))
 *   Notification::send(...)
 *   $user->notify(new SomeNotification(...))
 *
 * Notification recipient analysis is intentionally skipped here because the
 * recipient type often comes from the runtime model graph; the Python side
 * resolves it during graph construction.
 */
final class JobDispatchVisitor extends ContextTrackingVisitor
{
    protected function processNode(Node $node): void
    {
        if ($node instanceof FuncCall) {
            $this->handleFuncCall($node);

            return;
        }

        if ($node instanceof StaticCall) {
            $this->handleStaticCall($node);

            return;
        }

        if ($node instanceof Node\Expr\MethodCall) {
            $this->handleMethodCall($node);
        }
    }

    private function handleFuncCall(FuncCall $node): void
    {
        if (! ($node->name instanceof Name)) {
            return;
        }

        $name = $node->name->toLowerString();
        if (! in_array($name, ['dispatch', 'dispatch_sync', 'dispatch_now'], true)) {
            return;
        }

        if ($node->args === []) {
            return;
        }

        $arg = $node->args[0];
        if (! ($arg instanceof Node\Arg)) {
            return;
        }

        $target = $this->resolveTarget($arg->value);
        if ($target !== null) {
            $this->emit('job_dispatch', $target, $node, ['form' => 'helper', 'helper' => $name]);
        }
    }

    private function handleStaticCall(StaticCall $node): void
    {
        if (! ($node->class instanceof Name) || ! ($node->name instanceof Node\Identifier)) {
            return;
        }

        $className = $node->class->toString();
        $method = $node->name->toLowerString();

        if (in_array($className, ['Bus', '\\Bus', 'Illuminate\\Support\\Facades\\Bus'], true) && $method === 'dispatch') {
            if ($node->args !== []) {
                $arg = $node->args[0];
                if ($arg instanceof Node\Arg) {
                    $target = $this->resolveTarget($arg->value);
                    if ($target !== null) {
                        $this->emit('job_dispatch', $target, $node, ['form' => 'facade']);
                    }
                }
            }

            return;
        }

        if (in_array($className, ['Notification', '\\Notification', 'Illuminate\\Support\\Facades\\Notification'], true) && $method === 'send') {
            // Notification::send($recipient, new SomeNotification(...))
            if (count($node->args) >= 2) {
                $arg = $node->args[1];
                if ($arg instanceof Node\Arg) {
                    $target = $this->resolveTarget($arg->value);
                    if ($target !== null) {
                        $this->emit('notification_dispatch', $target, $node, ['form' => 'facade']);
                    }
                }
            }

            return;
        }

        // SomeJob::dispatch(...) and ::dispatchSync, ::dispatchAfterResponse
        // Skip Laravel facades - a call like `Event::dispatch(Foo::class)`
        // is handled by EventDispatchVisitor, not this one.
        if (in_array($method, ['dispatch', 'dispatchsync', 'dispatchafterresponse', 'dispatchnow'], true)
            && ! LaravelFacades::isDispatchFacade($className)) {
            $this->emit('job_dispatch', $className, $node, ['form' => 'trait', 'method' => $method]);
        }
    }

    private function handleMethodCall(Node\Expr\MethodCall $node): void
    {
        if (! ($node->name instanceof Node\Identifier)) {
            return;
        }

        if ($node->name->toLowerString() !== 'notify') {
            return;
        }

        if ($node->args === []) {
            return;
        }

        $arg = $node->args[0];
        if (! ($arg instanceof Node\Arg)) {
            return;
        }

        $target = $this->resolveTarget($arg->value);
        if ($target !== null) {
            $this->emit('notification_dispatch', $target, $node, ['form' => 'instance']);
        }
    }

    private function resolveTarget(Node $value): ?string
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
