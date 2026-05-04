<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Stmt\Class_;
use PhpParser\Node\Stmt\ClassMethod;
use PhpParser\Node\Stmt\Namespace_;
use PhpParser\NodeVisitorAbstract;

/**
 * Base visitor that maintains the current namespace, class, and method
 * during traversal so concrete visitors can attach findings to the right
 * lexical context.
 *
 * Concrete visitors override {@see emit()} and {@see processNode()}; this
 * base handles the bookkeeping.
 */
abstract class ContextTrackingVisitor extends NodeVisitorAbstract
{
    protected ?string $currentNamespace = null;

    protected ?string $currentClass = null;

    protected ?string $currentMethod = null;

    /** @var list<StaticAnalysisFinding> */
    protected array $findings = [];

    protected ?string $currentFile = null;

    public function setCurrentFile(?string $file): void
    {
        $this->currentFile = $file;
    }

    public function enterNode(Node $node): null
    {
        if ($node instanceof Namespace_) {
            $this->currentNamespace = $node->name?->toString();
        } elseif ($node instanceof Class_) {
            $this->currentClass = $this->currentNamespace !== null && $node->name !== null
                ? $this->currentNamespace.'\\'.$node->name->toString()
                : $node->name?->toString();
        } elseif ($node instanceof ClassMethod) {
            $this->currentMethod = $node->name->toString();
        }

        $this->processNode($node);

        return null;
    }

    public function leaveNode(Node $node): null
    {
        if ($node instanceof Namespace_) {
            $this->currentNamespace = null;
        } elseif ($node instanceof Class_) {
            $this->currentClass = null;
        } elseif ($node instanceof ClassMethod) {
            $this->currentMethod = null;
        }

        return null;
    }

    /**
     * @return list<StaticAnalysisFinding>
     */
    public function findings(): array
    {
        return $this->findings;
    }

    public function reset(): void
    {
        $this->findings = [];
        $this->currentNamespace = null;
        $this->currentClass = null;
        $this->currentMethod = null;
    }

    /**
     * @param  array<string, mixed>  $meta
     */
    protected function emit(string $kind, ?string $target, Node $node, array $meta = []): void
    {
        $this->findings[] = new StaticAnalysisFinding(
            kind: $kind,
            target: $this->resolveRelativeClassName($target),
            contextClass: $this->currentClass,
            contextMethod: $this->currentMethod,
            file: $this->currentFile,
            line: $node->getStartLine() > 0 ? $node->getStartLine() : null,
            meta: $meta,
        );
    }

    /**
     * Resolves `self`, `static`, and `parent` to a usable class name using
     * the enclosing class context. Returns the original value for normal
     * class names. Returns null (with a best-effort fallback) when the
     * context is unknown.
     */
    protected function resolveRelativeClassName(?string $target): ?string
    {
        if ($target === null) {
            return null;
        }

        $lower = strtolower($target);
        if (! in_array($lower, ['self', 'static', 'parent', '\\self', '\\static', '\\parent'], true)) {
            return $target;
        }

        // `parent::` cannot be resolved without reading the parent clause
        // of the class; tree-sitter already captured the parent in Phase B.
        // For static analysis purposes `self`/`static` → current class is
        // sufficient. Return the unresolved literal for `parent` so the
        // Python side can spot it.
        if (in_array($lower, ['parent', '\\parent'], true)) {
            return $this->currentClass !== null ? $this->currentClass.'::parent' : 'parent';
        }

        return $this->currentClass ?? $target;
    }

    abstract protected function processNode(Node $node): void;
}
