<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Expr\ClassConstFetch;
use PhpParser\Node\Expr\MethodCall;
use PhpParser\Node\Expr\New_;
use PhpParser\Node\Name;

/**
 * Detects CQRS command/query-bus dispatch sites:
 *
 *   $this->queryBus->ask(new EvaluateRoutingContextQuery(...))
 *   $this->commandBus->dispatch(new CreateUserCommand(...))
 *   $bus->handle(new ArchiveCommand(...))
 *
 * A bus resolves its handler by naming-convention reflection at runtime,
 * so there is no static reference from the dispatch site to the
 * handler's ``handle`` method for an LSP to follow - which is why
 * ``find_callers`` on a handler misses every real dispatch site.
 *
 * This visitor records the dispatched message class as a ``bus_dispatch``
 * finding. The Python graph builder resolves the message to its handler
 * (by short name) and synthesises the missing ``CALLS`` edge. Because the
 * Python side only links when a conventionally-named handler class
 * actually exists, this visitor can be deliberately permissive about
 * which instance method calls it reports: a false positive (e.g. a
 * non-CQRS ``->dispatch(new X)``) simply resolves to no handler and no
 * edge.
 *
 * We still gate on (a) a small set of dispatch-shaped method names and
 * (b) the first argument being a freshly-constructed object, so the
 * finding stream stays focused rather than emitting every
 * ``->method(new X)`` call in the codebase.
 */
final class BusDispatchVisitor extends ContextTrackingVisitor
{
    /**
     * Method names that idiomatically dispatch a message onto a bus.
     * Compared case-insensitively.
     *
     * @var list<string>
     */
    private const DISPATCH_METHODS = ['ask', 'query', 'dispatch', 'dispatchsync', 'dispatchnow', 'handle'];

    protected function processNode(Node $node): void
    {
        if (! ($node instanceof MethodCall)) {
            return;
        }

        if (! ($node->name instanceof Node\Identifier)) {
            return;
        }

        if (! in_array($node->name->toLowerString(), self::DISPATCH_METHODS, true)) {
            return;
        }

        if ($node->args === []) {
            return;
        }

        $arg = $node->args[0];
        if (! ($arg instanceof Node\Arg)) {
            return;
        }

        $message = $this->resolveMessage($arg->value);
        if ($message === null) {
            return;
        }

        $this->emit('bus_dispatch', $message, $node, ['method' => $node->name->toString()]);
    }

    private function resolveMessage(Node $value): ?string
    {
        if ($value instanceof New_ && $value->class instanceof Name) {
            return $value->class->toString();
        }

        if (
            $value instanceof ClassConstFetch
            && $value->class instanceof Name
            && $value->name instanceof Node\Identifier
            && $value->name->toLowerString() === 'class'
        ) {
            return $value->class->toString();
        }

        return null;
    }
}
