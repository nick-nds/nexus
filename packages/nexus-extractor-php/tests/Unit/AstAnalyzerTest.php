<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseC\AstAnalyzer;
use Nexus\Extractor\Extraction\PhaseC\Visitors\EventDispatchVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\JobDispatchVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\PolicyUseVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\StaticAnalysisFinding;
use Nexus\Extractor\Extraction\PhaseC\Visitors\ValidationRuleVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\ViewReturnVisitor;
use PHPUnit\Framework\TestCase;

final class AstAnalyzerTest extends TestCase
{
    private function analyzer(): AstAnalyzer
    {
        return new AstAnalyzer([
            new EventDispatchVisitor,
            new JobDispatchVisitor,
            new ViewReturnVisitor,
            new ValidationRuleVisitor,
            new PolicyUseVisitor,
        ]);
    }

    public function test_event_helper_dispatch_with_class_const(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App;
        class Foo {
            public function bar(): void {
                event(\App\Events\OrderPlaced::class);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'event_dispatch', 'App\\Events\\OrderPlaced');
    }

    public function test_event_helper_dispatch_with_new(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App;
        class Foo {
            public function bar(): void {
                event(new \App\Events\OrderPlaced(1));
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'event_dispatch', 'App\\Events\\OrderPlaced');
    }

    public function test_event_helper_dispatch_with_leading_backslash(): void
    {
        // The canonical idiom inside namespaced DDD code that hasn't
        // aliased the helper: `\event(new Event(...))`. The NameResolver
        // pass normalises the leading separator before our visitor runs,
        // so the global helper is recognised the same as the bare form.
        $code = <<<'PHP'
        <?php
        namespace App\Modules\Sales\Application\Commands;
        use App\Modules\Sales\Domain\Events\OrderShipped;
        class ShipOrderCommandHandler {
            public function handle(): void {
                \event(new OrderShipped(1));
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding(
            $findings,
            'event_dispatch',
            'App\\Modules\\Sales\\Domain\\Events\\OrderShipped',
        );
    }

    public function test_event_facade_dispatch(): void
    {
        $code = <<<'PHP'
        <?php
        use Illuminate\Support\Facades\Event;
        Event::dispatch(\App\Events\OrderPlaced::class);
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'event_dispatch', 'App\\Events\\OrderPlaced');
    }

    public function test_event_trait_dispatch(): void
    {
        $code = <<<'PHP'
        <?php
        \App\Events\OrderPlaced::dispatch(1);
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'event_dispatch', 'App\\Events\\OrderPlaced');
    }

    public function test_job_helper_dispatch(): void
    {
        $code = <<<'PHP'
        <?php
        dispatch(new \App\Jobs\SendEmail(1));
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'job_dispatch', 'App\\Jobs\\SendEmail');
    }

    public function test_job_trait_dispatch(): void
    {
        $code = <<<'PHP'
        <?php
        \App\Jobs\SendEmail::dispatch(1);
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'job_dispatch', 'App\\Jobs\\SendEmail');
    }

    public function test_view_helper_with_literal(): void
    {
        $code = <<<'PHP'
        <?php
        return view('posts.index', ['k' => 1]);
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'view_return', 'posts.index');
    }

    public function test_view_helper_with_dynamic_argument_marks_dynamic(): void
    {
        $code = <<<'PHP'
        <?php
        $name = 'x';
        return view($name);
        PHP;

        $findings = $this->analyse($code);

        $found = $this->find($findings, 'view_return');
        $this->assertNotNull($found);
        $this->assertNull($found->target);
        $this->assertTrue($found->meta['dynamic'] ?? false);
    }

    public function test_form_request_rules_method_extracted(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Http\Requests;
        class StoreThing {
            public function rules(): array {
                return [
                    'title' => 'required|string|max:255',
                    'tags' => ['array', 'min:1'],
                ];
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $found = $this->find($findings, 'form_request_rules');
        $this->assertNotNull($found);
        /** @var array<string, string|list<string>> $rules */
        $rules = $found->meta['rules'];
        $this->assertSame('required|string|max:255', $rules['title']);
        $this->assertSame(['array', 'min:1'], $rules['tags']);
    }

    public function test_inline_validate_rules_extracted(): void
    {
        $code = <<<'PHP'
        <?php
        $request->validate([
            'name' => 'required|string',
        ]);
        PHP;

        $findings = $this->analyse($code);

        $found = $this->find($findings, 'inline_validation');
        $this->assertNotNull($found);
        /** @var array<string, string|list<string>> $rules */
        $rules = $found->meta['rules'];
        $this->assertSame('required|string', $rules['name']);
    }

    public function test_authorize_method_call_with_literal_ability(): void
    {
        $code = <<<'PHP'
        <?php
        class C {
            public function show($post): void {
                $this->authorize('view', $post);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'authorize', 'view');
    }

    public function test_gate_facade_check(): void
    {
        $code = <<<'PHP'
        <?php
        use Illuminate\Support\Facades\Gate;
        Gate::allows('update', $post);
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'gate_check', 'update');
    }

    public function test_event_target_is_fully_qualified_via_use_statement(): void
    {
        // Regression: NameResolver must run as a separate pass before the
        // visitors so that an unqualified `new PostCreated($post)` reachable
        // through a `use` import is captured as the FQN, not the basename.
        $code = <<<'PHP'
        <?php
        namespace App\Http\Controllers;
        use App\Events\PostCreated;
        class C {
            public function store($post): void {
                event(new PostCreated($post));
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'event_dispatch', 'App\\Events\\PostCreated');
    }

    public function test_job_trait_target_is_fully_qualified_via_use_statement(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Http\Controllers;
        use App\Jobs\SendEmail;
        class C {
            public function store(): void {
                SendEmail::dispatch(1);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'job_dispatch', 'App\\Jobs\\SendEmail');
    }

    public function test_bus_facade_dispatch_is_not_captured_as_event_dispatch(): void
    {
        // Regression: `Bus::dispatch(new Job)` must be a job_dispatch only;
        // the event visitor must not treat the facade class as an event.
        $code = <<<'PHP'
        <?php
        use Illuminate\Support\Facades\Bus;
        Bus::dispatch(new \App\Jobs\SendEmail());
        PHP;

        $findings = $this->analyse($code);

        $eventKinds = array_filter($findings, static fn ($f) => $f->kind === 'event_dispatch');
        $this->assertCount(0, $eventKinds, 'Bus::dispatch must not be captured as an event dispatch');

        $this->assertHasFinding($findings, 'job_dispatch', 'App\\Jobs\\SendEmail');
    }

    public function test_event_facade_dispatch_is_not_captured_as_job_dispatch(): void
    {
        // Regression: `Event::dispatch(Foo::class)` must be an event_dispatch
        // only; the job visitor's trait fallback must not claim it.
        $code = <<<'PHP'
        <?php
        use Illuminate\Support\Facades\Event;
        Event::dispatch(\App\Events\Thing::class);
        PHP;

        $findings = $this->analyse($code);

        $jobKinds = array_filter($findings, static fn ($f) => $f->kind === 'job_dispatch');
        $this->assertCount(0, $jobKinds, 'Event::dispatch must not be captured as a job dispatch');

        $this->assertHasFinding($findings, 'event_dispatch', 'App\\Events\\Thing');
    }

    public function test_self_dispatch_resolves_to_enclosing_class(): void
    {
        // Regression: a job recursing itself via `self::dispatch(...)` used
        // to emit the literal string "self" as the target, which is useless
        // to the Python pipeline. The context-tracking base should resolve
        // `self` to the enclosing class name.
        $code = <<<'PHP'
        <?php
        namespace App\Jobs;
        class ReprocessJob {
            public function handle(): void {
                self::dispatch();
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertHasFinding($findings, 'job_dispatch', 'App\\Jobs\\ReprocessJob');
        foreach ($findings as $f) {
            $this->assertNotSame('self', $f->target, 'self must be resolved, not emitted as a literal');
        }
    }

    public function test_parse_error_returns_error_field(): void
    {
        $result = $this->analyzer()->analyse('/tmp/x.php', '<?php class { }');

        $this->assertNotNull($result['error']);
        $this->assertSame([], $result['findings']);
    }

    /**
     * @return list<StaticAnalysisFinding>
     */
    private function analyse(string $code): array
    {
        $result = $this->analyzer()->analyse('/tmp/test.php', $code);
        $this->assertNull($result['error'], $result['error'] ?? '');

        return $result['findings'];
    }

    /**
     * @param  list<StaticAnalysisFinding>  $findings
     */
    private function assertHasFinding(array $findings, string $kind, string $target): void
    {
        foreach ($findings as $f) {
            if ($f->kind === $kind && $f->target === $target) {
                $this->addToAssertionCount(1);

                return;
            }
        }

        $shapes = array_map(static fn (StaticAnalysisFinding $f): string => $f->kind.':'.($f->target ?? 'null'), $findings);
        $this->fail(sprintf('Expected finding %s -> %s. Got: %s', $kind, $target, implode(', ', $shapes)));
    }

    /**
     * @param  list<StaticAnalysisFinding>  $findings
     */
    private function find(array $findings, string $kind): ?StaticAnalysisFinding
    {
        foreach ($findings as $f) {
            if ($f->kind === $kind) {
                return $f;
            }
        }

        return null;
    }
}
