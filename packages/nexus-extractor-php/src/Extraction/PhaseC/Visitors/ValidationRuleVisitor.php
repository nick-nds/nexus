<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Expr\Array_;
use PhpParser\Node\Expr\ArrayItem;
use PhpParser\Node\Expr\MethodCall;
use PhpParser\Node\Scalar\String_;
use PhpParser\Node\Stmt\ClassMethod;
use PhpParser\Node\Stmt\Return_;

/**
 * Detects validation rules in two places:
 *
 *   1. FormRequest::rules() - captured by walking a `rules` method that
 *      returns an array literal of string-to-rule mappings.
 *   2. Inline `$request->validate([...])` - captured by inspecting the
 *      first argument of any `validate` method call.
 *
 * Only literal array shapes are captured. Dynamic rules are recorded as
 * `dynamic_rules` so the call site is still visible to the pipeline.
 */
final class ValidationRuleVisitor extends ContextTrackingVisitor
{
    protected function processNode(Node $node): void
    {
        if ($node instanceof ClassMethod && $node->name->toLowerString() === 'rules') {
            $this->handleRulesMethod($node);

            return;
        }

        if ($node instanceof MethodCall && $node->name instanceof Node\Identifier && $node->name->toLowerString() === 'validate') {
            $this->handleInlineValidate($node);
        }
    }

    private function handleRulesMethod(ClassMethod $method): void
    {
        if ($method->stmts === null) {
            return;
        }

        foreach ($method->stmts as $stmt) {
            if ($stmt instanceof Return_ && $stmt->expr instanceof Array_) {
                $rules = $this->extractArrayRules($stmt->expr);
                $this->emit('form_request_rules', null, $stmt, ['rules' => $rules]);

                return;
            }
        }
    }

    private function handleInlineValidate(MethodCall $node): void
    {
        if ($node->args === []) {
            return;
        }

        $arg = $node->args[0];
        if (! ($arg instanceof Node\Arg)) {
            return;
        }

        if ($arg->value instanceof Array_) {
            $rules = $this->extractArrayRules($arg->value);
            $this->emit('inline_validation', null, $node, ['rules' => $rules]);

            return;
        }

        $this->emit('inline_validation', null, $node, ['dynamic_rules' => true]);
    }

    /**
     * @return array<string, string|list<string>>
     */
    private function extractArrayRules(Array_ $array): array
    {
        $out = [];

        foreach ($array->items as $item) {
            if (! ($item instanceof ArrayItem)) {
                continue;
            }

            if (! ($item->key instanceof String_)) {
                continue;
            }

            $field = $item->key->value;

            if ($item->value instanceof String_) {
                $out[$field] = $item->value->value;

                continue;
            }

            if ($item->value instanceof Array_) {
                $list = [];
                foreach ($item->value->items as $rule) {
                    if ($rule instanceof ArrayItem && $rule->value instanceof String_) {
                        $list[] = $rule->value->value;
                    }
                }
                $out[$field] = $list;
            }
        }

        return $out;
    }
}
