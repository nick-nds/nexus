<?php

declare(strict_types=1);

namespace NexusFixtures\Sample\Console\Commands;

use Illuminate\Console\Command;

final class SampleCommand extends Command
{
    protected $signature = 'sample:run';

    protected $description = 'Sample fixture command';

    public function handle(): int
    {
        return 0;
    }
}
