<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseC\AstAnalyzer;
use Nexus\Extractor\Extraction\PhaseC\Visitors\ContainerBindingVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\StaticAnalysisFinding;
use PHPUnit\Framework\TestCase;

/**
 * Pinning audit P1-18: closure-binding resolution.
 *
 * Phase A's runtime ``BindingExtractor`` couldn't surface the
 * concrete class for closure-wrapped bindings. This static-analysis
 * visitor walks ``$this->app->bind/singleton/instance/scoped()`` and
 * ``App::bind(...)`` callsites and detects ``return new X(...)`` /
 * ``X::class`` / return-type-declaration patterns inside the closure
 * body, emitting a ``closure_binding`` finding the Python graph
 * builder can turn into a ``BOUND_TO`` edge.
 */
final class ContainerBindingVisitorTest extends TestCase
{
    private function analyzer(): AstAnalyzer
    {
        return new AstAnalyzer([new ContainerBindingVisitor]);
    }

    public function test_singleton_with_new_in_closure_body_resolves_concrete(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Adapters\SynthesQClient;
        use App\Contracts\Client;

        class AppServiceProvider {
            public function register(): void {
                $this->app->singleton(Client::class, function ($app) {
                    return new SynthesQClient($app);
                });
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $f = $findings[0];
        $this->assertSame('closure_binding', $f->kind);
        $this->assertSame('App\\Adapters\\SynthesQClient', $f->target);
        $this->assertSame('App\\Contracts\\Client', $f->meta['abstract']);
        $this->assertSame('singleton', $f->meta['binding_kind']);
    }

    public function test_bind_with_arrow_function_resolves_concrete(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Mail\SmtpMailer;
        use App\Contracts\Mailer;

        class MailServiceProvider {
            public function register(): void {
                $this->app->bind(Mailer::class, fn ($app) => new SmtpMailer);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('App\\Mail\\SmtpMailer', $findings[0]->target);
        $this->assertSame('App\\Contracts\\Mailer', $findings[0]->meta['abstract']);
        $this->assertSame('bind', $findings[0]->meta['binding_kind']);
    }

    public function test_scoped_binding_is_detected(): void
    {
        // PHP 8.2+ added ``scoped`` to the container - used heavily for
        // per-request resolvers (tenant resolution, request context).
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Tenancy\EnvTenantResolver;
        use App\Contracts\TenantResolver;

        class TenancyServiceProvider {
            public function register(): void {
                $this->app->scoped(TenantResolver::class, fn () => new EnvTenantResolver);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('App\\Tenancy\\EnvTenantResolver', $findings[0]->target);
        $this->assertSame('scoped', $findings[0]->meta['binding_kind']);
    }

    public function test_class_const_concrete_is_resolved_without_a_closure(): void
    {
        // ``$this->app->bind(X::class, Y::class)`` - the non-closure form.
        // We surface it too so all binding-shaped callsites land in one
        // finding stream.
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Contracts\Mailer;
        use App\Mail\SmtpMailer;

        class AppServiceProvider {
            public function register(): void {
                $this->app->bind(Mailer::class, SmtpMailer::class);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('App\\Mail\\SmtpMailer', $findings[0]->target);
    }

    public function test_return_type_declaration_is_used_when_no_new(): void
    {
        // ``function (): X { ... }`` - the closure's return type acts as
        // a static promise of the concrete kind.
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Contracts\Cache;
        use App\Cache\RedisCache;

        class CacheServiceProvider {
            public function register(): void {
                $this->app->singleton(Cache::class, function ($app): RedisCache {
                    return $app->makeCache();
                });
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('App\\Cache\\RedisCache', $findings[0]->target);
    }

    public function test_resolve_or_make_call_inside_closure_surfaces_inner_class(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Contracts\Notifier;
        use App\Notifications\SlackNotifier;

        class AppServiceProvider {
            public function register(): void {
                $this->app->singleton(Notifier::class, function ($app) {
                    return $app->make(SlackNotifier::class);
                });
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('App\\Notifications\\SlackNotifier', $findings[0]->target);
    }

    public function test_unrelated_method_calls_are_ignored(): void
    {
        // ``$collection->bind()`` is not a container call. The receiver
        // check filters these out so the Python side doesn't see noise.
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        class AppServiceProvider {
            public function register(): void {
                $collection->bind(Something::class, fn () => new SomethingConcrete);
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(0, $findings);
    }

    public function test_dynamic_concrete_in_closure_emits_nothing(): void
    {
        // A closure that returns ``$variable`` or ``static`` can't be
        // statically resolved. Skip silently - the agent will see a
        // closure-flavoured binding without a BOUND_TO edge.
        $code = <<<'PHP'
        <?php
        namespace App\Providers;
        use App\Contracts\Client;

        class AppServiceProvider {
            public function register(): void {
                $this->app->singleton(Client::class, function ($app) {
                    return $app->resolveSomehow();
                });
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(0, $findings);
    }

    /**
     * @return list<StaticAnalysisFinding>
     */
    private function analyse(string $code): array
    {
        $result = $this->analyzer()->analyse('/tmp/binding-test.php', $code);
        $this->assertNull($result['error'], $result['error'] ?? '');

        return $result['findings'];
    }
}
