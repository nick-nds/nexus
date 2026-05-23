<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Feature;

use Illuminate\Foundation\Application;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\Facades\Gate;
use Illuminate\Support\Facades\Route;
use Nexus\Extractor\NexusExtractorServiceProvider;
use Orchestra\Testbench\TestCase;
use SampleApp\Events\PostCreated;
use SampleApp\Http\Controllers\PostController;
use SampleApp\Listeners\SendPostCreatedEmail;
use SampleApp\Models\Post;
use SampleApp\Policies\PostPolicy;

final class ExtractCommandTest extends TestCase
{
    private string $outputPath;

    protected function setUp(): void
    {
        parent::setUp();

        $this->outputPath = sys_get_temp_dir().'/nexus-extract-'.uniqid('', true).'/reflection.json';
    }

    protected function tearDown(): void
    {
        if (is_file($this->outputPath)) {
            @unlink($this->outputPath);
        }
        $dir = dirname($this->outputPath);
        if (is_dir($dir)) {
            @rmdir($dir);
        }

        parent::tearDown();
    }

    /**
     * @param  Application  $app
     * @return list<class-string>
     */
    protected function getPackageProviders($app): array
    {
        return [NexusExtractorServiceProvider::class];
    }

    /**
     * @param  Application  $app
     */
    protected function defineEnvironment($app): void
    {
        $app['config']->set('app.name', 'SampleApp');
    }

    protected function defineRoutes($router): void
    {
        Route::get('/posts', [PostController::class, 'index'])->name('posts.index');
        Route::get('/posts/{post}', [PostController::class, 'show'])->name('posts.show');
        Route::post('/posts', [PostController::class, 'store'])->name('posts.store')->middleware('web');
    }

    public function test_command_runs_and_produces_a_complete_document(): void
    {
        Event::listen(PostCreated::class, SendPostCreatedEmail::class);
        Gate::policy(Post::class, PostPolicy::class);
        Gate::define('ping', static fn (): bool => true);

        $exit = $this->artisan('nexus:extract', [
            '--output' => $this->outputPath,
            '--quiet-progress' => true,
        ])->run();

        $this->assertSame(0, $exit);
        $this->assertFileExists($this->outputPath);

        /** @var string $contents */
        $contents = file_get_contents($this->outputPath);
        /** @var array<string, mixed> $doc */
        $doc = json_decode($contents, true, 512, JSON_THROW_ON_ERROR);

        $this->assertSame('2.4.0', $doc['schema_version']);
        $this->assertSame('SampleApp', $doc['project']['name']);
        $this->assertNotEmpty($doc['project']['laravel_version']);

        $sections = $doc['sections'];
        $this->assertArrayHasKey('routes', $sections);
        $this->assertArrayHasKey('bindings', $sections);
        $this->assertArrayHasKey('events', $sections);
        $this->assertArrayHasKey('gates_policies', $sections);
        $this->assertArrayHasKey('middleware', $sections);
        $this->assertArrayHasKey('config', $sections);
        $this->assertArrayHasKey('schedule', $sections);
        $this->assertArrayHasKey('classes', $sections);
        $this->assertArrayHasKey('static_analysis', $sections);
    }

    public function test_command_extracts_registered_routes(): void
    {
        $this->artisan('nexus:extract', [
            '--output' => $this->outputPath,
            '--quiet-progress' => true,
        ])->run();

        /** @var array<string, mixed> $doc */
        $doc = json_decode((string) file_get_contents($this->outputPath), true);
        /** @var array{count: int, items: list<array<string, mixed>>} $routes */
        $routes = $doc['sections']['routes'];

        $uris = array_map(static fn (array $r): string => (string) $r['uri'], $routes['items']);
        $this->assertContains('/posts', $uris);
        $this->assertContains('/posts/{post}', $uris);
    }

    public function test_command_extracts_event_listener_map(): void
    {
        Event::listen(PostCreated::class, SendPostCreatedEmail::class);

        $this->artisan('nexus:extract', [
            '--output' => $this->outputPath,
            '--quiet-progress' => true,
        ])->run();

        /** @var array<string, mixed> $doc */
        $doc = json_decode((string) file_get_contents($this->outputPath), true);
        /** @var array{listeners: list<array{event: string, listeners: list<array<string, mixed>>}>} $events */
        $events = $doc['sections']['events'];

        $found = null;
        foreach ($events['listeners'] as $entry) {
            if ($entry['event'] === PostCreated::class) {
                $found = $entry;
                break;
            }
        }

        $this->assertNotNull($found);
        $this->assertNotEmpty($found['listeners']);
    }

    public function test_command_extracts_policy_map(): void
    {
        Gate::policy(Post::class, PostPolicy::class);

        $this->artisan('nexus:extract', [
            '--output' => $this->outputPath,
            '--quiet-progress' => true,
        ])->run();

        /** @var array<string, mixed> $doc */
        $doc = json_decode((string) file_get_contents($this->outputPath), true);
        /** @var array{policies: list<array{model: string, policy: string}>} $gp */
        $gp = $doc['sections']['gates_policies'];

        $matched = false;
        foreach ($gp['policies'] as $entry) {
            if ($entry['model'] === Post::class && $entry['policy'] === PostPolicy::class) {
                $matched = true;
                break;
            }
        }

        $this->assertTrue($matched);
    }

    public function test_invalid_output_returns_usage_error(): void
    {
        $exit = $this->artisan('nexus:extract', [
            '--output' => '   ',
        ])->run();

        $this->assertSame(2, $exit);
    }
}
