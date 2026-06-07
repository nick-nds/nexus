<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit\Extraction\PhaseA;

use Illuminate\Contracts\Events\Dispatcher;
use Illuminate\Foundation\Application;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\PhaseA\EventListenerExtractor;
use Nexus\Extractor\Output\ReflectionDocument;
use Nexus\Extractor\Support\ErrorCollector;
use Nexus\Extractor\Support\ProgressReporter;
use Nexus\Extractor\Tests\TestCase;
use SampleApp\Events\BrandCreated;
use SampleApp\Listeners\QueuedBrandListener;
use SampleApp\Listeners\SyncBrandListener;
use SampleApp\Providers\BrandEventServiceProvider;
use Symfony\Component\Console\Output\BufferedOutput;

/**
 * Accuracy of the live-dispatcher event→listener extraction.
 *
 * SyncBrandListener is wired through BrandEventServiceProvider::$listen;
 * QueuedBrandListener is wired at runtime via Event::listen. This lets us
 * assert the queued flag, the source file, and the registration source.
 */
final class EventListenerExtractorTest extends TestCase
{
    /**
     * @param  Application  $app
     * @return list<class-string>
     */
    protected function getPackageProviders($app): array
    {
        return [...parent::getPackageProviders($app), BrandEventServiceProvider::class];
    }

    /**
     * Run the extractor and return the describe-arrays for BrandCreated's
     * listeners, in dispatcher order.
     *
     * @return list<array<string, mixed>>
     */
    private function brandListeners(): array
    {
        /** @var Dispatcher $events */
        $events = $this->app->make('events');
        // Wired outside the $listen map - should be tagged "discovered".
        $events->listen(BrandCreated::class, QueuedBrandListener::class);

        $errors = new ErrorCollector;
        $document = new ReflectionDocument($errors);
        $context = new ExtractionContext(
            app: $this->app,
            document: $document,
            errors: $errors,
            progress: new ProgressReporter(new BufferedOutput, quiet: true),
            includeVendor: false,
            vendorAllowlist: [],
            profileHint: null,
        );

        (new EventListenerExtractor)->extract($context);

        $section = $document->section('events');
        $this->assertNotNull($section);

        foreach ($section['listeners'] as $entry) {
            if ($entry['event'] === BrandCreated::class) {
                return $entry['listeners'];
            }
        }

        $this->fail('BrandCreated not found in extracted listeners.');
    }

    /**
     * @return array<string, array<string, mixed>>
     */
    private function byClass(): array
    {
        $map = [];
        foreach ($this->brandListeners() as $listener) {
            $map[$listener['class']] = $listener;
        }

        return $map;
    }

    public function test_marks_listener_queued_when_class_implements_should_queue(): void
    {
        $byClass = $this->byClass();

        $this->assertTrue($byClass[QueuedBrandListener::class]['queued']);
        $this->assertFalse($byClass[SyncBrandListener::class]['queued']);
    }

    public function test_records_source_file_for_class_listener(): void
    {
        $byClass = $this->byClass();

        $this->assertSame(
            (new \ReflectionClass(SyncBrandListener::class))->getFileName(),
            $byClass[SyncBrandListener::class]['file'],
        );
    }

    public function test_tags_listener_in_listen_map_as_source_listen(): void
    {
        $byClass = $this->byClass();

        $this->assertSame('listen', $byClass[SyncBrandListener::class]['source']);
    }

    public function test_tags_listener_outside_listen_map_as_source_discovered(): void
    {
        $byClass = $this->byClass();

        $this->assertSame('discovered', $byClass[QueuedBrandListener::class]['source']);
    }
}
