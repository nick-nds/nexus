<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseB;

use Nexus\Extractor\Extraction\ExtractionContext;
use Nexus\Extractor\Extraction\Extractor;
use Nexus\Extractor\Support\ExtractionWarning;
use ReflectionClass;
use Throwable;

/**
 * Walks the project class map and produces a structured catalogue.
 *
 * For each class we capture:
 *   - source: 'project' or 'vendor' (when allowlisted)
 *   - kinds: Laravel primitives the class fits via instanceof
 *   - reflection: methods, parameters, attributes (via ReflectionInspector)
 *
 * Errors loading individual classes are recorded as warnings; the sweep
 * continues. This is the v1-fix in code form: nothing breaks the run.
 */
final class ProjectClassExtractor implements Extractor
{
    public function __construct(
        private readonly ClassMapWalker $walker,
        private readonly ClassClassifier $classifier = new ClassClassifier,
        private readonly ReflectionInspector $inspector = new ReflectionInspector,
    ) {}

    public function name(): string
    {
        return 'phase_b.classes';
    }

    public function extract(ExtractionContext $context): void
    {
        $entries = $this->walker->walk(
            basePath: $context->app->basePath(),
            includeVendor: $context->includeVendor,
            vendorAllowlist: $context->vendorAllowlist,
            includeTests: $context->includeTests,
            scope: $context->package,
        );

        $items = [];
        $tracker = $context->classTracker;

        foreach ($entries as $entry) {
            $class = $entry['class'];

            // Record the class we are about to touch so that, if PHP raises
            // an uncatchable fatal error during autoload/declaration, the
            // shutdown handler can name the culprit in the partial output.
            $tracker?->set($class);

            try {
                if (! class_exists($class) && ! interface_exists($class) && ! trait_exists($class)) {
                    $context->errors->warn(new ExtractionWarning(
                        code: 'class_not_loadable',
                        message: sprintf('Class %s could not be loaded.', $class),
                        file: $entry['file'],
                        context: ['class' => $class],
                    ));

                    continue;
                }

                $reflection = new ReflectionClass($class);
            } catch (Throwable $e) {
                $context->errors->warn(new ExtractionWarning(
                    code: 'class_reflection_failed',
                    message: $e->getMessage(),
                    file: $entry['file'],
                    context: ['class' => $class],
                ));

                continue;
            }

            try {
                $items[] = [
                    'source' => $entry['source'],
                    'kinds' => $this->classifier->classify($reflection),
                    'reflection' => $this->inspector->inspect($reflection),
                ];
            } catch (Throwable $e) {
                $context->errors->warn(new ExtractionWarning(
                    code: 'class_inspect_failed',
                    message: $e->getMessage(),
                    file: $entry['file'],
                    context: ['class' => $class],
                ));
            }

            // Clear the tracker after each successful iteration so the
            // shutdown handler only sees a non-null value if we actually
            // died mid-load on a specific class.
            $tracker?->clear();
        }

        usort($items, static function (array $a, array $b): int {
            /** @var array{name: string} $ar */
            $ar = $a['reflection'];
            /** @var array{name: string} $br */
            $br = $b['reflection'];

            return strcmp($ar['name'], $br['name']);
        });

        $context->document->setSection('classes', [
            'count' => count($items),
            'items' => $items,
        ]);
    }
}
