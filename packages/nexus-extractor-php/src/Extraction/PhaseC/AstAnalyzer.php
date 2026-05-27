<?php

declare(strict_types=1);

namespace Nexus\Extractor\Extraction\PhaseC;

use Nexus\Extractor\Extraction\PhaseC\Visitors\ContextTrackingVisitor;
use Nexus\Extractor\Extraction\PhaseC\Visitors\StaticAnalysisFinding;
use PhpParser\Error as ParserError;
use PhpParser\NodeTraverser;
use PhpParser\NodeVisitor\NameResolver;
use PhpParser\Parser;
use PhpParser\ParserFactory;
use Throwable;

/**
 * Runs a configured set of {@see ContextTrackingVisitor}s over PHP source.
 *
 * The analyser is constructed once per pipeline run with the visitors it
 * needs, then `analyse($file, $source)` is called for every project file.
 * Visitors accumulate findings; the analyser returns them as one flat list
 * after each file (so the caller can group by file or aggregate by kind).
 *
 * The PhpParser instance is cached on the analyser; nikic's parser is
 * thread-unsafe but we are single-threaded.
 */
final class AstAnalyzer
{
    private readonly Parser $parser;

    /**
     * Pass 1: resolves names in-place. Must run on its own traverser before
     * the visitor pass, because nikic's traversal is depth-first and our
     * visitors process parent expressions (FuncCall, New_, StaticCall) in
     * enterNode - *before* the inner Name child is visited and replaced by
     * NameResolver. Running the resolver in a separate prior pass guarantees
     * every Name is fully qualified when our visitors see it.
     */
    private readonly NodeTraverser $resolverPass;

    /** Pass 2: our finding-collection visitors. */
    private readonly NodeTraverser $visitorPass;

    /** @var list<ContextTrackingVisitor> */
    private readonly array $visitors;

    /**
     * @param  list<ContextTrackingVisitor>  $visitors
     */
    public function __construct(array $visitors)
    {
        $this->parser = (new ParserFactory)->createForNewestSupportedVersion();
        $this->visitors = $visitors;

        $this->resolverPass = new NodeTraverser;
        $this->resolverPass->addVisitor(new NameResolver);

        $this->visitorPass = new NodeTraverser;
        foreach ($visitors as $visitor) {
            $this->visitorPass->addVisitor($visitor);
        }
    }

    /**
     * @return array{findings: list<StaticAnalysisFinding>, error: ?string}
     */
    public function analyse(string $file, string $source): array
    {
        foreach ($this->visitors as $visitor) {
            $visitor->reset();
            $visitor->setCurrentFile($file);
        }

        try {
            $ast = $this->parser->parse($source);
        } catch (ParserError $e) {
            return ['findings' => [], 'error' => 'parse_error: '.$e->getMessage()];
        } catch (Throwable $e) {
            return ['findings' => [], 'error' => 'parse_failed: '.$e->getMessage()];
        }

        if ($ast === null) {
            return ['findings' => [], 'error' => null];
        }

        try {
            $ast = $this->resolverPass->traverse($ast);
            $this->visitorPass->traverse($ast);
        } catch (Throwable $e) {
            return ['findings' => [], 'error' => 'traverse_failed: '.$e->getMessage()];
        }

        $all = [];
        foreach ($this->visitors as $visitor) {
            foreach ($visitor->findings() as $finding) {
                $all[] = $finding;
            }
        }

        return ['findings' => $all, 'error' => null];
    }
}
