<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Expr\FuncCall;
use PhpParser\Node\Expr\StaticCall;
use PhpParser\Node\Name;
use PhpParser\Node\Scalar\String_;

/**
 * Detects calls that return Blade views, so the Python pipeline can build
 * controller → view edges:
 *
 *   view('home', [...])
 *   View::make('home', [...])
 *   response()->view('home', [...])  // detected via the view() chain only
 *
 * Only literal string view names are captured. Dynamic view names are
 * recorded as `dynamic` so the pipeline can still flag the call site.
 */
final class ViewReturnVisitor extends ContextTrackingVisitor
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
        if (! ($node->name instanceof Name) || $node->name->toLowerString() !== 'view') {
            return;
        }

        if ($node->args === []) {
            return;
        }

        $this->emitViewArg($node->args[0], $node);
    }

    private function handleStaticCall(StaticCall $node): void
    {
        if (! ($node->class instanceof Name) || ! ($node->name instanceof Node\Identifier)) {
            return;
        }

        $className = $node->class->toString();
        if (! in_array($className, ['View', '\\View', 'Illuminate\\Support\\Facades\\View'], true)) {
            return;
        }

        if ($node->name->toLowerString() !== 'make') {
            return;
        }

        if ($node->args === []) {
            return;
        }

        $this->emitViewArg($node->args[0], $node);
    }

    private function emitViewArg(Node\Arg|Node\VariadicPlaceholder $arg, Node $node): void
    {
        if (! ($arg instanceof Node\Arg)) {
            return;
        }

        if ($arg->value instanceof String_) {
            $this->emit('view_return', $arg->value->value, $node);

            return;
        }

        $this->emit('view_return', null, $node, ['dynamic' => true]);
    }
}
