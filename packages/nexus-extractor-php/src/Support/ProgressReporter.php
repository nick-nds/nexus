<?php

declare(strict_types=1);

namespace Nexus\Extractor\Support;

use Symfony\Component\Console\Output\OutputInterface;

/**
 * Lightweight progress reporter wrapping a Symfony Console output.
 *
 * The reporter has two modes: verbose (writes per-step messages) and quiet
 * (suppresses everything). Extractors call {@see step()} between phases and
 * {@see info()} for noteworthy events.
 *
 * The reporter does NOT use Symfony's ProgressBar because phase counts are
 * known up front and a structured per-phase log is more useful in CI tails.
 */
final class ProgressReporter
{
    public function __construct(
        private readonly OutputInterface $output,
        private readonly bool $quiet = false,
    ) {}

    public function step(string $name, string $message): void
    {
        if ($this->quiet) {
            return;
        }

        $this->output->writeln(sprintf('<info>[%s]</info> %s', $name, $message));
    }

    public function info(string $message): void
    {
        if ($this->quiet) {
            return;
        }

        $this->output->writeln('  '.$message);
    }

    public function warn(string $message): void
    {
        if ($this->quiet) {
            return;
        }

        $this->output->writeln('  <comment>! '.$message.'</comment>');
    }

    public function error(string $message): void
    {
        $this->output->writeln('<error>'.$message.'</error>');
    }
}
