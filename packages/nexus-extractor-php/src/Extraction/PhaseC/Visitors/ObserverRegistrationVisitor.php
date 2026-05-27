<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Expr\Array_;
use PhpParser\Node\Expr\ClassConstFetch;
use PhpParser\Node\Expr\StaticCall;
use PhpParser\Node\Name;

/**
 * Detects Eloquent observer registrations:
 *
 *   User::observe(UserObserver::class);
 *   Order::observe([OrderObserver::class, AuditObserver::class]);
 *
 * Each registration emits an ``observer_registration`` finding tagged
 * with the observer FQN as the target (``$target``) and the model
 * FQN in the ``meta.model`` slot. The Python graph builder consumes
 * these to populate ``OBSERVES`` edges.
 *
 * The attribute-based form (``#[ObservedBy([UserObserver::class])]``)
 * lives on the Model class itself, not in a method body - it's caught
 * by the class-level reflection in Phase B, not by this AST visitor.
 *
 * Why a static call and not a generic method call: the observe() form
 * always reaches the model class statically because Laravel's
 * ``HasEvents`` trait declares it as a static method. A method call
 * shape (``$user->observe(...)``) doesn't exist for this pattern.
 */
final class ObserverRegistrationVisitor extends ContextTrackingVisitor
{
    protected function processNode(Node $node): void
    {
        if (! ($node instanceof StaticCall)) {
            return;
        }

        if (! ($node->class instanceof Name) || ! ($node->name instanceof Node\Identifier)) {
            return;
        }

        if ($node->name->toLowerString() !== 'observe') {
            return;
        }

        if ($node->args === []) {
            return;
        }

        $modelClass = $node->class->toString();

        $arg = $node->args[0];
        if (! ($arg instanceof Node\Arg)) {
            return;
        }

        $observers = $this->resolveObservers($arg->value);
        foreach ($observers as $observerFqn) {
            $this->emit(
                'observer_registration',
                $observerFqn,
                $node,
                ['model' => $modelClass],
            );
        }
    }

    /**
     * Resolve the observer argument to one or more class FQNs.
     *
     * Accepts a bare ``Observer::class`` or an array of them. Skips
     * dynamic forms (variables, function-call results) - those would
     * require runtime tracing that the AST pass can't do.
     *
     * @return list<string>
     */
    private function resolveObservers(Node $value): array
    {
        if ($value instanceof ClassConstFetch && $this->isClassConstant($value)) {
            $name = $value->class;
            if ($name instanceof Name) {
                return [$name->toString()];
            }
        }

        if ($value instanceof Array_) {
            $items = [];
            foreach ($value->items as $item) {
                if ($item === null) {
                    continue;
                }
                if ($item->value instanceof ClassConstFetch
                    && $this->isClassConstant($item->value)
                    && $item->value->class instanceof Name) {
                    $items[] = $item->value->class->toString();
                }
            }

            return $items;
        }

        return [];
    }

    private function isClassConstant(ClassConstFetch $node): bool
    {
        return $node->name instanceof Node\Identifier
            && $node->name->toLowerString() === 'class';
    }
}
