<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseC\AstAnalyzer;
use Nexus\Extractor\Extraction\PhaseC\Visitors\ObserverRegistrationVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\StaticAnalysisFinding;
use PHPUnit\Framework\TestCase;

final class ObserverRegistrationVisitorTest extends TestCase
{
    private function analyzer(): AstAnalyzer
    {
        return new AstAnalyzer([new ObserverRegistrationVisitor]);
    }

    public function test_single_observer_registration_emits_finding(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Models\User;
        use App\Observers\UserObserver;

        class EventServiceProvider {
            public function boot(): void {
                User::observe(UserObserver::class);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $f = $findings[0];
        $this->assertSame('observer_registration', $f->kind);
        // Visitor uses ``ContextTrackingVisitor`` so ``use`` statements
        // resolve to fully-qualified class names - exactly what the
        // Python graph builder needs to point at ``class:<fqn>`` nodes.
        $this->assertSame('App\\Observers\\UserObserver', $f->target);
        $this->assertSame(['model' => 'App\\Models\\User'], $f->meta);
    }

    public function test_array_form_observer_registration_emits_one_finding_per_observer(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Models\Order;
        use App\Observers\OrderObserver;
        use App\Observers\AuditObserver;

        class EventServiceProvider {
            public function boot(): void {
                Order::observe([OrderObserver::class, AuditObserver::class]);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(2, $findings);
        $targets = array_map(static fn (StaticAnalysisFinding $f): string => $f->target ?? '', $findings);
        sort($targets);
        $this->assertSame(
            ['App\\Observers\\AuditObserver', 'App\\Observers\\OrderObserver'],
            $targets,
        );

        // Both findings reference the same model.
        foreach ($findings as $f) {
            $this->assertSame(['model' => 'App\\Models\\Order'], $f->meta);
        }
    }

    public function test_dynamic_observer_argument_is_skipped(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Models\User;

        class EventServiceProvider {
            public function boot(): void {
                $observer = config('app.user_observer');
                User::observe($observer);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(0, $findings, 'dynamic observer args must not produce findings');
    }

    public function test_observe_call_on_other_classes_is_ignored(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App;
        // Some unrelated class with an observe() method.
        class Telescope {
            public function boot(): void {
                Telescope::observe(SomethingElse::class);
            }
        }
        PHP;

        // The visitor doesn't (and can't statically) tell models from
        // non-models, so it'll still emit a finding here. The Python
        // builder filters by whether the target is actually a graph
        // node - the visitor's job is just to surface every static
        // ``X::observe(Y::class)`` call in the codebase.
        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('observer_registration', $findings[0]->kind);
        $this->assertSame('App\\SomethingElse', $findings[0]->target);
    }

    public function test_unrelated_static_call_emits_nothing(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App;
        class Foo {
            public function bar(): void {
                User::find(1);
                User::create(['name' => 'x']);
                Cache::get('key');
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertSame([], $findings);
    }

    /**
     * @return list<StaticAnalysisFinding>
     */
    private function analyse(string $code): array
    {
        $result = $this->analyzer()->analyse('/tmp/observer-test.php', $code);
        $this->assertNull($result['error'], $result['error'] ?? '');

        return $result['findings'];
    }
}
