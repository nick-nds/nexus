<?php

declare(strict_types=1);

namespace SampleApp\Mail;

use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Mail\Mailable;

/**
 * A mailable that ALSO uses the Queueable trait - exactly the shape that
 * previously caused the classifier to mis-tag mailables as jobs.
 */
final class WelcomeMail extends Mailable implements ShouldQueue
{
    use Queueable;

    public function build(): self
    {
        return $this->subject('Welcome');
    }
}
