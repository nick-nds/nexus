<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Expr\MethodCall;
use PhpParser\Node\Expr\StaticCall;
use PhpParser\Node\Name;
use PhpParser\Node\Scalar\String_;

/**
 * Detects authorisation calls so the Python pipeline can build edges from
 * call sites to gates and policies:
 *
 *   $this->authorize('update', $post)
 *   Gate::allows('update', $post)
 *   Gate::denies('update', $post)
 *
 * The ability name is captured if it is a literal string. The target model
 * argument is captured as a class hint when it is a typed `new` expression
 * or a `Foo::class` reference.
 */
final class PolicyUseVisitor extends ContextTrackingVisitor
{
    protected function processNode(Node $node): void
    {
        if ($node instanceof MethodCall) {
            $this->handleMethodCall($node);

            return;
        }

        if ($node instanceof StaticCall) {
            $this->handleStaticCall($node);
        }
    }

    private function handleMethodCall(MethodCall $node): void
    {
        if (! ($node->name instanceof Node\Identifier)) {
            return;
        }

        if ($node->name->toLowerString() !== 'authorize') {
            return;
        }

        $ability = $this->literalString($node->args[0] ?? null);
        $this->emit('authorize', $ability, $node, ['form' => 'method']);
    }

    private function handleStaticCall(StaticCall $node): void
    {
        if (! ($node->class instanceof Name) || ! ($node->name instanceof Node\Identifier)) {
            return;
        }

        $className = $node->class->toString();
        if (! in_array($className, ['Gate', '\\Gate', 'Illuminate\\Support\\Facades\\Gate'], true)) {
            return;
        }

        $method = $node->name->toLowerString();
        if (! in_array($method, ['allows', 'denies', 'check', 'any', 'none'], true)) {
            return;
        }

        $ability = $this->literalString($node->args[0] ?? null);
        $this->emit('gate_check', $ability, $node, ['form' => 'facade', 'method' => $method]);
    }

    private function literalString(Node\Arg|Node\VariadicPlaceholder|null $arg): ?string
    {
        if (! ($arg instanceof Node\Arg)) {
            return null;
        }

        if ($arg->value instanceof String_) {
            return $arg->value->value;
        }

        return null;
    }
}
