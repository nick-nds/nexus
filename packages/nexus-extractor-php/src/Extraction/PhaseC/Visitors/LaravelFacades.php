<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC\Visitors;

/**
 * Canonical list of Laravel facade class names used by visitors to
 * disambiguate the "ClassName::dispatch(...)" pattern.
 *
 * Without this list, a call like `Bus::dispatch(new Job)` would be captured
 * by the Dispatchable-trait fallback in EventDispatchVisitor, and
 * `Event::dispatch(Foo::class)` would be captured by the fallback in
 * JobDispatchVisitor - both are wrong. The correct interpretation of
 * facade calls is handled explicitly earlier in each visitor; the fallback
 * branch must skip them.
 *
 * We include both the unqualified facade name (as it appears in source
 * after a `use Illuminate\Support\Facades\Foo;`) and the fully-qualified
 * form (after NameResolver runs) so matches work in both traversal passes.
 */
final class LaravelFacades
{
    /**
     * @var list<string>
     */
    public const DISPATCH_FACADES = [
        // Event
        'Event',
        '\\Event',
        'Illuminate\\Support\\Facades\\Event',
        // Bus (job dispatch)
        'Bus',
        '\\Bus',
        'Illuminate\\Support\\Facades\\Bus',
        // Notification
        'Notification',
        '\\Notification',
        'Illuminate\\Support\\Facades\\Notification',
        // Gate
        'Gate',
        '\\Gate',
        'Illuminate\\Support\\Facades\\Gate',
        // Queue
        'Queue',
        '\\Queue',
        'Illuminate\\Support\\Facades\\Queue',
        // Mail
        'Mail',
        '\\Mail',
        'Illuminate\\Support\\Facades\\Mail',
        // Log
        'Log',
        '\\Log',
        'Illuminate\\Support\\Facades\\Log',
    ];

    public static function isDispatchFacade(string $className): bool
    {
        return in_array($className, self::DISPATCH_FACADES, true);
    }
}
