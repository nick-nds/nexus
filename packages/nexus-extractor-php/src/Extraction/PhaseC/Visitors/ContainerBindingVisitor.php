<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

use PhpParser\Node;
use PhpParser\Node\Expr\ArrowFunction;
use PhpParser\Node\Expr\ClassConstFetch;
use PhpParser\Node\Expr\Closure;
use PhpParser\Node\Expr\MethodCall;
use PhpParser\Node\Expr\New_;
use PhpParser\Node\Expr\PropertyFetch;
use PhpParser\Node\Expr\StaticCall;
use PhpParser\Node\Expr\Variable;
use PhpParser\Node\Name;
use PhpParser\Node\Stmt\Return_;

/**
 * Detects container-binding callsites that wrap their concrete in a
 * closure - the idiomatic Laravel ServiceProvider::register() pattern:
 *
 *   $this->app->singleton(SynthesQClient::class, fn ($app) => new SynthesQClient(...));
 *   $this->app->bind(MailerInterface::class, function () { return new SmtpMailer; });
 *   $this->app->scoped(TenantResolver::class, fn () => new EnvTenantResolver);
 *
 * Phase A's runtime ``BindingExtractor`` only captures the closure
 * itself, not the concrete it would return when invoked. Agents
 * asking ``resolve_binding(abstract="…")`` therefore got
 * ``binding_not_found`` even though the call site exists.
 *
 * Audit P1-18. The static-analysis pass walks the AST instead,
 * detecting the most common concrete shapes inside the closure:
 *
 *   1. ``return new X(...)`` - the most common pattern.
 *   2. ``return X::class`` - class const fetch (rare but valid).
 *   3. ``static fn () => new X`` / ``fn () => new X`` - arrow form.
 *   4. Closure return type ``function (): X`` declaration.
 *
 * Each detection emits a ``closure_binding`` finding with the
 * abstract FQN in ``meta.abstract``, the concrete FQN as the
 * finding's ``target``, and the binding flavour
 * (``bind``/``singleton``/``scoped``/``instance``) in
 * ``meta.binding_kind``. The Python graph builder upgrades a
 * ``closure``-flavoured binding node to a real ``BOUND_TO`` edge,
 * or synthesises a binding node when Phase A didn't see one.
 */
final class ContainerBindingVisitor extends ContextTrackingVisitor
{
    /**
     * Binding methods we recognise. Maps the method name to the
     * binding "flavour" stored in the finding for the Python side
     * to thread into the binding node's attributes.
     */
    private const BINDING_METHODS = [
        'bind' => 'bind',
        'singleton' => 'singleton',
        'scoped' => 'scoped',
        'instance' => 'instance',
        'bindif' => 'bind',
        'singletonif' => 'singleton',
        'scopedif' => 'scoped',
    ];

    protected function processNode(Node $node): void
    {
        // We handle both ``$this->app->bind(...)`` (MethodCall) and
        // ``App::bind(...)`` (StaticCall via the App facade). The
        // method name is the same; only the receiver shape differs.
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

        $methodName = $node->name->toLowerString();
        if (! array_key_exists($methodName, self::BINDING_METHODS)) {
            return;
        }

        // Receiver must be ``$this->app`` or a variable named
        // ``$app``. Other receivers (e.g. ``$someService->bind()``)
        // aren't container calls and would generate noise.
        if (! $this->isContainerReceiver($node->var)) {
            return;
        }

        $this->emitFromCall($node, $methodName, $node->args);
    }

    private function handleStaticCall(StaticCall $node): void
    {
        if (! ($node->name instanceof Node\Identifier)
            || ! ($node->class instanceof Name)) {
            return;
        }

        $methodName = $node->name->toLowerString();
        if (! array_key_exists($methodName, self::BINDING_METHODS)) {
            return;
        }

        // App::bind / App::singleton / etc. The fully-resolved name
        // tells us whether this is the Laravel App facade. We treat
        // anything ending in ``\App`` as the App facade - the
        // resolver in the upstream pass already handled the import.
        $className = $node->class->toString();
        if (! preg_match('#(^|\\\\)(App|Container)$#', $className)) {
            return;
        }

        $this->emitFromCall($node, $methodName, $node->args);
    }

    /**
     * @param  array<int, Node\Arg|Node\VariadicPlaceholder>  $args
     */
    private function emitFromCall(Node $node, string $methodName, array $args): void
    {
        if (count($args) < 2) {
            // ``bind(X::class)`` without a concrete is "alias self to
            // self" - nothing to surface for resolution.
            return;
        }

        $abstractArg = $args[0];
        $concreteArg = $args[1];
        if (! ($abstractArg instanceof Node\Arg) || ! ($concreteArg instanceof Node\Arg)) {
            return;
        }

        $abstract = $this->extractClassConst($abstractArg->value);
        if ($abstract === null) {
            return;
        }

        $concrete = $this->resolveConcrete($concreteArg->value);
        if ($concrete === null) {
            return;
        }

        $this->emit(
            'closure_binding',
            $concrete,
            $node,
            [
                'abstract' => $abstract,
                'binding_kind' => self::BINDING_METHODS[$methodName],
            ],
        );
    }

    /**
     * The receiver is a container if it's ``$this->app`` or a
     * locally-bound ``$app`` parameter (the convention inside
     * provider closures and service-provider callbacks).
     */
    private function isContainerReceiver(Node $receiver): bool
    {
        if ($receiver instanceof PropertyFetch
            && $receiver->var instanceof Variable
            && $receiver->var->name === 'this'
            && $receiver->name instanceof Node\Identifier
            && $receiver->name->toLowerString() === 'app') {
            return true;
        }

        return $receiver instanceof Variable && $receiver->name === 'app';
    }

    /**
     * Extract a class FQN from a ``X::class`` constant fetch.
     */
    private function extractClassConst(Node $expr): ?string
    {
        if (! ($expr instanceof ClassConstFetch)) {
            return null;
        }
        if (! ($expr->name instanceof Node\Identifier)
            || $expr->name->toLowerString() !== 'class') {
            return null;
        }
        if (! ($expr->class instanceof Name)) {
            return null;
        }

        return $expr->class->toString();
    }

    /**
     * Resolve the binding's concrete argument to a class FQN.
     *
     * Handled shapes:
     *   - ``X::class`` (no closure - the simple form).
     *   - ``fn () => new X(...)``.
     *   - ``fn () => X::class`` (rare).
     *   - ``function () { return new X(...); }``.
     *   - ``function () { return resolve(X::class); }`` - the
     *     application asks the container to resolve the concrete;
     *     we surface the inner ``X``.
     *   - Closure return-type declaration: ``function (): X { ... }``.
     */
    private function resolveConcrete(Node $expr): ?string
    {
        // Plain class-const fetch: ``X::class``.
        $direct = $this->extractClassConst($expr);
        if ($direct !== null) {
            return $direct;
        }

        if ($expr instanceof ArrowFunction) {
            return $this->resolveClosureBody($expr->expr, $expr->returnType);
        }

        if ($expr instanceof Closure) {
            // Walk top-level return statements in the body. We don't
            // descend into nested closures - those are conditional
            // branches the agent has to read the source for.
            foreach ($expr->stmts as $stmt) {
                if ($stmt instanceof Return_ && $stmt->expr !== null) {
                    $resolved = $this->resolveClosureBody($stmt->expr, $expr->returnType);
                    if ($resolved !== null) {
                        return $resolved;
                    }
                }
            }

            // No return found - fall back to the return-type hint.
            return $this->resolveReturnType($expr->returnType);
        }

        return null;
    }

    /**
     * Resolve a single expression that could be the body of a
     * closure / arrow function.
     */
    private function resolveClosureBody(Node $expr, ?Node $returnType): ?string
    {
        if ($expr instanceof New_ && $expr->class instanceof Name) {
            return $expr->class->toString();
        }

        $direct = $this->extractClassConst($expr);
        if ($direct !== null) {
            return $direct;
        }

        // ``return $app->make(X::class)`` / ``resolve(X::class)`` -
        // the closure is telling the container to look up X. Surface
        // X as the concrete since that's what the agent wants to see.
        if ($expr instanceof MethodCall
            && $expr->name instanceof Node\Identifier
            && in_array($expr->name->toLowerString(), ['make', 'makewith'], true)) {
            if (isset($expr->args[0]) && $expr->args[0] instanceof Node\Arg) {
                $inner = $this->extractClassConst($expr->args[0]->value);
                if ($inner !== null) {
                    return $inner;
                }
            }
        }

        return $this->resolveReturnType($returnType);
    }

    private function resolveReturnType(?Node $returnType): ?string
    {
        if ($returnType instanceof Name) {
            return $returnType->toString();
        }

        return null;
    }
}
