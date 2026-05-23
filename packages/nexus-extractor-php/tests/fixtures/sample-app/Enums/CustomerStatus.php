<?php

declare(strict_types=1);

namespace SampleApp\Enums;

/**
 * Fixture for audit P0-2: a backed ``enum`` declaration. The
 * classifier should tag this as ``enum`` (not ``class``), and the
 * inspector should emit the cases (``active``, ``inactive``,
 * ``churned``) without injecting PHP-synthesized methods like
 * ``cases``/``from``/``tryFrom``.
 */
enum CustomerStatus: string
{
    case Active = 'active';
    case Inactive = 'inactive';
    case Churned = 'churned';
}
