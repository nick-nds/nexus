<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseA;

use Illuminate\Console\Scheduling\CallbackEvent;
use Illuminate\Console\Scheduling\Event;
use Illuminate\Console\Scheduling\Schedule;
use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use Throwable;

/**
 * Extracts scheduled events from Laravel's Schedule (Console Kernel).
 *
 * Each event records its cron expression, timezone, command/closure
 * description, and the most-relevant runtime constraints (overlapping,
 * onOneServer, withoutOverlapping). We don't try to capture every fluent
 * builder method - only the ones agents typically need to reason about.
 */
final class ScheduleExtractor implements Extractor
{
    public function name(): string
    {
        return 'phase_a.schedule';
    }

    public function extract(ExtractionContext $context): void
    {
        if (! $context->app->bound(Schedule::class)) {
            $context->document->setSection('schedule', ['events' => []]);

            return;
        }

        try {
            $schedule = $context->app->make(Schedule::class);
        } catch (Throwable) {
            $context->document->setSection('schedule', ['events' => []]);

            return;
        }

        $items = [];
        foreach ($schedule->events() as $event) {
            $items[] = $this->describeEvent($event);
        }

        $context->document->setSection('schedule', [
            'count' => count($items),
            'events' => $items,
        ]);
    }

    /**
     * @return array<string, mixed>
     */
    private function describeEvent(Event $event): array
    {
        $base = [
            'expression' => $event->expression,
            'timezone' => $event->timezone instanceof \DateTimeZone
                ? $event->timezone->getName()
                : (is_string($event->timezone) ? $event->timezone : null),
            'description' => $event->description,
            'without_overlapping' => $event->withoutOverlapping,
            'on_one_server' => $event->onOneServer,
        ];

        if ($event instanceof CallbackEvent) {
            return $base + ['kind' => 'callback', 'target' => $event->getSummaryForDisplay()];
        }

        return $base + ['kind' => 'command', 'command' => $event->command];
    }
}
