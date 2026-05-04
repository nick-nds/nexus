<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Output\SchemaVersion;
use PHPUnit\Framework\TestCase;

final class SchemaVersionTest extends TestCase
{
    public function test_string_is_semver_format(): void
    {
        $this->assertMatchesRegularExpression('/^\d+\.\d+\.\d+$/', SchemaVersion::string());
    }

    public function test_major_is_two(): void
    {
        $this->assertSame(2, SchemaVersion::MAJOR);
    }
}
