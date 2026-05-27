<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseB;

use ReflectionClass;

/**
 * Classifies a class into one or more Laravel primitive kinds via
 * inheritance and interface checks (no name-based heuristics).
 *
 * Why instanceof, not naming or paths: a project's `App\Foo\BarController`
 * may live anywhere; what makes it a controller is extending Laravel's base
 * controller. Likewise for every other primitive. This is the single
 * convention-agnostic move that frees us from per-project profiles.
 *
 * The classifier returns the *set* of kinds because some classes legitimately
 * fit multiple categories (e.g. a Job that is also ShouldQueue is both `job`
 * and `queueable`).
 */
final class ClassClassifier
{
    /**
     * Map of base type → emitted kind. Order is significant only for the
     * order in which kinds appear in the output; classification is set-based.
     *
     * Each entry is [base_class_or_interface, kind_label].
     *
     * @var list<array{0: string, 1: string}>
     */
    private const TYPE_MAP = [
        ['Illuminate\\Database\\Eloquent\\Model', 'model'],
        ['Illuminate\\Routing\\Controller', 'controller'],
        ['Illuminate\\Foundation\\Http\\FormRequest', 'form_request'],
        ['Illuminate\\Http\\Resources\\Json\\JsonResource', 'resource'],
        ['Illuminate\\Http\\Resources\\Json\\ResourceCollection', 'resource_collection'],
        ['Illuminate\\Contracts\\Queue\\ShouldQueue', 'queueable'],
        // `Illuminate\Bus\Queueable` (the trait) is intentionally NOT a job
        // marker: Mailables and Notifications also use it to get queueing
        // fluency (`->onQueue(...)` etc). The authoritative job marker is
        // `Illuminate\Foundation\Bus\Dispatchable` - that's what Laravel's
        // own documentation uses and what `make:job` stubs include.
        ['Illuminate\\Notifications\\Notification', 'notification'],
        ['Illuminate\\Mail\\Mailable', 'mailable'],
        ['Illuminate\\Foundation\\Events\\Dispatchable', 'event'],
        ['Illuminate\\Contracts\\Events\\ShouldBroadcast', 'broadcastable_event'],
        ['Illuminate\\Auth\\Access\\HandlesAuthorization', 'policy_helper'],
        ['Illuminate\\Support\\ServiceProvider', 'service_provider'],
        ['Illuminate\\Console\\Command', 'command'],
        ['Illuminate\\Contracts\\Console\\Kernel', 'console_kernel'],
        ['Illuminate\\Contracts\\Http\\Kernel', 'http_kernel'],
        ['Illuminate\\Contracts\\Validation\\Rule', 'validation_rule'],
        ['Illuminate\\Contracts\\Validation\\ValidationRule', 'validation_rule'],
        ['Illuminate\\Contracts\\Database\\Eloquent\\CastsAttributes', 'cast'],
        ['Illuminate\\View\\Component', 'view_component'],
        ['Illuminate\\Foundation\\Exceptions\\Handler', 'exception_handler'],
        ['Throwable', 'throwable'],
    ];

    /**
     * @param  ReflectionClass<object>  $reflection
     * @return list<string>
     */
    public function classify(ReflectionClass $reflection): array
    {
        // PHP language constructs get their own kind so consumers can
        // distinguish ``interface Transformer`` from ``abstract class
        // Module`` and from ``enum CustomerStatus``. Audit P0-1, P0-2.
        // Before this split every non-class language construct was
        // bucketed as ``abstract``, which lost meaningful information
        // (interfaces can't be instantiated; enums have cases; traits
        // are mixin behaviour). These return early because no Laravel
        // type-map base would match an interface / enum / trait
        // declaration anyway.
        if ($reflection->isInterface()) {
            return ['interface'];
        }

        if ($reflection->isEnum()) {
            return ['enum'];
        }

        if ($reflection->isTrait()) {
            return ['trait'];
        }

        if ($reflection->isAbstract()) {
            // True abstract classes - bases like ``Synthesq\Relay\Events
            // \SynthesQEvent`` or ``App\Modules\Module``. Profile-defined
            // ``custom_bases`` may upgrade these to more specific kinds
            // in Phase 2 of the Python pipeline.
            return ['abstract'];
        }

        $kinds = [];

        foreach (self::TYPE_MAP as [$base, $label]) {
            if ($this->isSubclassOf($reflection, $base)) {
                $kinds[] = $label;
            }
        }

        // Heuristic kinds based on interfaces only (no class hierarchy):
        if ($this->implementsInterfaceNamed($reflection, 'Illuminate\\Contracts\\Queue\\ShouldQueue')) {
            $kinds[] = 'should_queue';
        }

        // Listeners are loosely typed in Laravel - there's no base class.
        // We rely on Phase A's listener map for definitive listener identity;
        // here we only flag classes that look listener-shaped.
        if ($this->isLikelyListener($reflection)) {
            $kinds[] = 'listener';
        }

        if ($this->isLikelyObserver($reflection)) {
            $kinds[] = 'observer';
        }

        if ($this->isLikelyMiddleware($reflection)) {
            $kinds[] = 'middleware';
        }

        if ($this->isLikelyPolicy($reflection)) {
            $kinds[] = 'policy';
        }

        if ($this->isLikelyController($reflection)) {
            $kinds[] = 'controller';
        }

        if ($this->isLikelyJob($reflection, $kinds)) {
            $kinds[] = 'job';
        }

        if ($this->isLikelyBootstrap($reflection, $kinds)) {
            $kinds[] = 'bootstrap';
        }

        return array_values(array_unique($kinds));
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     * @param  list<string>  $existingKinds
     */
    private function isLikelyBootstrap(ReflectionClass $reflection, array $existingKinds): bool
    {
        // Bootstrap classes are the package's entry point - e.g.
        // ``Synthesq\Relay\Relay``, ``Sentry\Sentry``, ``Cashier::class``.
        // They expose a ``public static boot()`` method declared on
        // themselves that wires up service providers, routes, and
        // extensions. Audit P2-20.
        //
        // Distinguishing from Eloquent models (which also have a static
        // boot()) is done via two negative checks:
        //
        // 1. Already classified as ``model`` → never a bootstrap.
        // 2. Already classified as ``service_provider`` → that's a
        //    provider, not a bootstrap.
        //
        // And one positive check: ``boot()`` must be *declared on*
        // this class, not inherited from a base. Eloquent's
        // ``Model::boot()`` is inherited, so models fail this check
        // even before the negative kind list helps.
        if (in_array('model', $existingKinds, true)
            || in_array('service_provider', $existingKinds, true)) {
            return false;
        }

        if (! $reflection->hasMethod('boot')) {
            return false;
        }

        $boot = $reflection->getMethod('boot');
        if (! $boot->isStatic() || ! $boot->isPublic()) {
            return false;
        }

        // Must be declared HERE, not inherited.
        return $boot->getDeclaringClass()->getName() === $reflection->getName();
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     * @param  list<string>  $existingKinds
     */
    private function isLikelyJob(ReflectionClass $reflection, array $existingKinds): bool
    {
        // A job is a class that uses `Illuminate\Foundation\Bus\Dispatchable`
        // AND has a `handle` method. The Dispatchable trait from the Bus
        // namespace is what `make:job` stubs include and what the docs use
        // as the canonical marker. Mailables and Notifications have their
        // own top-level classes and should NOT be tagged job even if they
        // happen to ship queue-ergonomic traits.
        if (in_array('mailable', $existingKinds, true) || in_array('notification', $existingKinds, true)) {
            return false;
        }

        if (! $reflection->hasMethod('handle')) {
            return false;
        }

        return $this->usesTraitRecursively($reflection, 'Illuminate\\Foundation\\Bus\\Dispatchable');
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     */
    private function isLikelyController(ReflectionClass $reflection): bool
    {
        // Laravel 11+ controllers commonly do NOT extend Illuminate\Routing\Controller;
        // the base controller in `app/Http/Controllers/Controller.php` is an
        // empty abstract class. The reliable heuristic is the namespace
        // convention `\Controllers\`. We also accept single-action controllers
        // that have `__invoke` as their only public method.
        if (! str_contains($reflection->getName(), '\\Controllers\\')) {
            return false;
        }

        // Require at least one public method (controllers are callable handlers).
        foreach ($reflection->getMethods(\ReflectionMethod::IS_PUBLIC) as $method) {
            if ($method->getDeclaringClass()->getName() !== $reflection->getName()) {
                continue;
            }
            if (! $method->isConstructor() && ! $method->isDestructor()) {
                return true;
            }
        }

        return false;
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     */
    private function isSubclassOf(ReflectionClass $reflection, string $target): bool
    {
        if (! class_exists($target) && ! interface_exists($target) && ! trait_exists($target)) {
            return false;
        }

        if ($reflection->getName() === $target) {
            return true;
        }

        if ($reflection->isSubclassOf($target) || $this->implementsInterfaceNamed($reflection, $target)) {
            return true;
        }

        if (trait_exists($target)) {
            return $this->usesTraitRecursively($reflection, $target);
        }

        return false;
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     */
    private function usesTraitRecursively(ReflectionClass $reflection, string $trait): bool
    {
        $cursor = $reflection;
        while ($cursor !== false) {
            foreach ($this->collectTraitsRecursively($cursor->getTraits()) as $name) {
                if ($name === $trait) {
                    return true;
                }
            }
            $cursor = $cursor->getParentClass();
        }

        return false;
    }

    /**
     * @param  array<string, ReflectionClass<object>>  $traits
     * @return list<string>
     */
    private function collectTraitsRecursively(array $traits): array
    {
        $names = [];
        foreach ($traits as $name => $traitReflection) {
            $names[] = (string) $name;
            foreach ($this->collectTraitsRecursively($traitReflection->getTraits()) as $nested) {
                $names[] = $nested;
            }
        }

        return $names;
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     */
    private function implementsInterfaceNamed(ReflectionClass $reflection, string $interface): bool
    {
        foreach ($reflection->getInterfaceNames() as $name) {
            if ($name === $interface) {
                return true;
            }
        }

        return false;
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     */
    private function isLikelyListener(ReflectionClass $reflection): bool
    {
        // Listener convention: a `handle(Event $event)` method on a class
        // located in a namespace ending in `Listeners`. Loose, but useful as
        // a hint; the authoritative listener list lives in Phase A.
        if (! $reflection->hasMethod('handle')) {
            return false;
        }

        return str_contains($reflection->getName(), '\\Listeners\\');
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     */
    private function isLikelyObserver(ReflectionClass $reflection): bool
    {
        if (! str_contains($reflection->getName(), '\\Observers\\')) {
            return false;
        }

        // Observer methods correspond to model events.
        foreach (['creating', 'created', 'updating', 'updated', 'deleting', 'deleted'] as $hook) {
            if ($reflection->hasMethod($hook)) {
                return true;
            }
        }

        return false;
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     */
    private function isLikelyMiddleware(ReflectionClass $reflection): bool
    {
        if (! $reflection->hasMethod('handle')) {
            return false;
        }

        $method = $reflection->getMethod('handle');
        $params = $method->getParameters();

        if (count($params) < 2) {
            return false;
        }

        $second = $params[1];
        $type = $second->getType();
        if ($type === null) {
            return false;
        }

        $typeString = (string) $type;

        return str_contains($typeString, 'Closure');
    }

    /**
     * @param  ReflectionClass<object>  $reflection
     */
    private function isLikelyPolicy(ReflectionClass $reflection): bool
    {
        // Policies are loosely typed; the canonical signal is "lives in a
        // Policies namespace and has a method matching a known ability".
        if (! str_contains($reflection->getName(), '\\Policies\\')) {
            return false;
        }

        foreach (['view', 'create', 'update', 'delete', 'restore', 'forceDelete', 'viewAny'] as $ability) {
            if ($reflection->hasMethod($ability)) {
                return true;
            }
        }

        return false;
    }
}
