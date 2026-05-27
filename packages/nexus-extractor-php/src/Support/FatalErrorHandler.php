<?php

declare(strict_types=1);

namespace Nexus\Extractor\Support;

use Nexus\Extractor\Output\JsonWriter;
use Nexus\Extractor\Output\ReflectionDocument;
use Throwable;

/**
 * Catches PHP fatal errors via a shutdown handler and turns them into a
 * structured entry in the reflection document, then writes the partial
 * document to disk before the process exits.
 *
 * Why this exists: Phase B calls `class_exists()` on every class in the
 * Composer classmap, which triggers the autoloader. PHP performs
 * class-declaration checks (abstract method implementation, readonly
 * compatibility, typed property constraints) during the `require`, and if
 * any of them fail the result is an uncatchable fatal error. Without this
 * handler the user would see only a generic Whoops trace and the partial
 * extraction work would be lost.
 *
 * With this handler the user gets:
 *   - A written `reflection.json` containing every section that had
 *     completed before the crash
 *   - An `errors` entry naming the class that was being loaded, the
 *     original PHP error message, and the file/line where the class is
 *     declared
 *   - A non-zero exit code so scripted callers can detect the failure
 *
 * The handler is split into two methods so the decision logic can be
 * unit-tested without triggering real fatals.
 */
final class FatalErrorHandler
{
    /**
     * Error types that warrant partial-document persistence. These are the
     * uncatchable fatal categories; lower-severity errors are left alone
     * and handled by the normal pipeline.
     *
     * @var list<int>
     */
    private const FATAL_TYPES = [
        E_ERROR,
        E_CORE_ERROR,
        E_COMPILE_ERROR,
        E_PARSE,
        E_USER_ERROR,
    ];

    private bool $registered = false;

    public function __construct(
        private readonly ReflectionDocument $document,
        private readonly CurrentClassTracker $tracker,
        private readonly JsonWriter $writer,
        private readonly string $outputPath,
    ) {}

    /**
     * Register the shutdown function with PHP. Safe to call multiple times;
     * only the first call wires up the handler.
     */
    public function register(): void
    {
        if ($this->registered) {
            return;
        }

        register_shutdown_function(function (): void {
            /** @var array{type: int, message: string, file: string, line: int}|null $error */
            $error = error_get_last();

            if ($error !== null) {
                $this->handleError($error);
            }
        });

        $this->registered = true;
    }

    /**
     * Decide whether a given error array corresponds to a fatal we should
     * capture, and if so, write the partial document.
     *
     * Extracted as a public entry point so unit tests can exercise the
     * branching without triggering real fatal errors.
     *
     * @param  array{type: int, message: string, file: string, line: int}  $error
     * @return bool true if the error was captured and a partial document written
     */
    public function handleError(array $error): bool
    {
        if (! in_array($error['type'], self::FATAL_TYPES, true)) {
            return false;
        }

        $class = $this->tracker->current();

        $this->document->errors()->fail(new ExtractionError(
            code: 'fatal_during_class_load',
            message: $this->buildMessage($class, $error['message']),
            file: $error['file'],
            line: $error['line'],
            context: [
                'class' => $class,
                'php_error_type' => $error['type'],
                'php_error_message' => $error['message'],
            ],
        ));

        try {
            $this->writer->write($this->document, $this->outputPath);
        } catch (Throwable) {
            // Last resort. If we can't even persist the partial document,
            // PHP is about to exit anyway - there's nothing more we can do.
        }

        return true;
    }

    private function buildMessage(?string $class, string $phpError): string
    {
        if ($class !== null) {
            return sprintf(
                'PHP fatal error while loading class %s: %s. '
                .'The class is autoloadable via Composer but cannot be declared. '
                .'Common causes: missing abstract-method implementation, '
                .'incompatible parent class, broken readonly modifier, '
                .'or a typed-property mismatch. Fix the class or exclude it '
                .'from extraction and retry.',
                $class,
                $phpError,
            );
        }

        return sprintf('PHP fatal error during extraction: %s.', $phpError);
    }
}
