<?php

declare(strict_types=1);

namespace SampleApp\DTOs;

/**
 * Fixture for audit P0-5: ``final readonly class`` is heavily used for
 * DTOs in PHP 8.2+ codebases (e.g. synthesq-relay's Customer / Lead /
 * Invoice DTOs). The reflection inspector must surface the ``readonly``
 * modifier so consumers can tell DTOs apart from mutable models.
 */
final readonly class CustomerDto
{
    public function __construct(
        public string $name,
        public string $email,
    ) {}
}
