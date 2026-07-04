<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseC\AstAnalyzer;
use Nexus\Extractor\Extraction\PhaseC\Visitors\BusDispatchVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\StaticAnalysisFinding;
use PHPUnit\Framework\TestCase;

/**
 * CQRS-bus blindness fix.
 *
 * A command/query bus resolves its handler by naming-convention
 * reflection at runtime, so ``$queryBus->ask(new FooQuery())`` carries
 * no static reference to ``FooQueryHandler::handle`` for an LSP to
 * follow. This visitor records the dispatch site and the message class
 * as a ``bus_dispatch`` finding; the Python graph builder resolves the
 * message to its handler and synthesises the missing ``CALLS`` edge.
 */
final class BusDispatchVisitorTest extends TestCase
{
    private function analyzer(): AstAnalyzer
    {
        return new AstAnalyzer([new BusDispatchVisitor]);
    }

    public function test_query_bus_ask_emits_bus_dispatch_with_message_and_context(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Modules\Routing\Presentation;
        use App\Modules\Routing\Application\Queries\EvaluateRoutingContextQuery;

        class RoutingController {
            public function resolve() {
                return $this->queryBus->ask(new EvaluateRoutingContextQuery('x'));
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $f = $findings[0];
        $this->assertSame('bus_dispatch', $f->kind);
        $this->assertSame(
            'App\\Modules\\Routing\\Application\\Queries\\EvaluateRoutingContextQuery',
            $f->target,
        );
        $this->assertSame('App\\Modules\\Routing\\Presentation\\RoutingController', $f->contextClass);
        $this->assertSame('resolve', $f->contextMethod);
        $this->assertSame('ask', $f->meta['method']);
    }

    public function test_command_bus_dispatch_emits_bus_dispatch(): void
    {
        $code = <<<'PHP'
        <?php
        namespace App\Http\Controllers;
        use App\Modules\Users\Commands\CreateUserCommand;

        class UserController {
            public function store() {
                $this->commandBus->dispatch(new CreateUserCommand('a', 'b'));
            }
        }
        PHP;

        $findings = $this->analyse($code);

        $this->assertCount(1, $findings);
        $this->assertSame('bus_dispatch', $findings[0]->kind);
        $this->assertSame('App\\Modules\\Users\\Commands\\CreateUserCommand', $findings[0]->target);
        $this->assertSame('dispatch', $findings[0]->meta['method']);
    }

    public function test_dispatch_of_a_non_message_variable_is_ignored(): void
    {
        // Only a freshly-constructed message object is a dispatch we can
        // resolve; a variable gives the Python side nothing to key on.
        $code = <<<'PHP'
        <?php
        namespace App\Http\Controllers;

        class UserController {
            public function store() {
                $this->commandBus->dispatch($command);
            }
        }
        PHP;

        $this->assertCount(0, $this->analyse($code));
    }

    public function test_unrelated_method_names_are_ignored(): void
    {
        // ``transform`` is not a bus dispatch method; emitting here would
        // flood the Python side with every ``->method(new X)`` call.
        $code = <<<'PHP'
        <?php
        namespace App\Http\Controllers;
        use App\DTOs\CustomerDto;

        class UserController {
            public function show() {
                return $this->presenter->transform(new CustomerDto);
            }
        }
        PHP;

        $this->assertCount(0, $this->analyse($code));
    }

    /**
     * @return list<StaticAnalysisFinding>
     */
    private function analyse(string $code): array
    {
        $result = $this->analyzer()->analyse('/tmp/bus-dispatch-test.php', $code);
        $this->assertNull($result['error'], $result['error'] ?? '');

        return $result['findings'];
    }
}
