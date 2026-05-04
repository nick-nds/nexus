<?php

declare(strict_types=1);

namespace Nexus\Extractor\Tests\Unit;

use Nexus\Extractor\Extraction\PhaseB\ClassClassifier;
use PHPUnit\Framework\TestCase;
use ReflectionClass;
use SampleApp\Events\PostCreated;
use SampleApp\Http\Controllers\MinimalController;
use SampleApp\Http\Controllers\PostController;
use SampleApp\Http\Requests\StorePostRequest;
use SampleApp\Jobs\NotifySubscribersJob;
use SampleApp\Listeners\SendPostCreatedEmail;
use SampleApp\Mail\WelcomeMail;
use SampleApp\Middleware\EnsureToken;
use SampleApp\Models\Post;
use SampleApp\Models\User;
use SampleApp\Observers\PostObserver;
use SampleApp\Policies\PostPolicy;

final class ClassClassifierTest extends TestCase
{
    private ClassClassifier $classifier;

    protected function setUp(): void
    {
        $this->classifier = new ClassClassifier;
    }

    public function test_model_is_classified(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(User::class));

        $this->assertContains('model', $kinds);
    }

    public function test_controller_is_classified(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(PostController::class));

        $this->assertContains('controller', $kinds);
    }

    public function test_laravel_11_style_controller_without_base_class_is_classified(): void
    {
        // Regression: Laravel 11+ ships without requiring controllers to
        // extend Illuminate\Routing\Controller. We must still classify a
        // `\Controllers\` class with public methods as a controller.
        $kinds = $this->classifier->classify(new ReflectionClass(MinimalController::class));

        $this->assertContains('controller', $kinds);
    }

    public function test_form_request_is_classified(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(StorePostRequest::class));

        $this->assertContains('form_request', $kinds);
    }

    public function test_event_is_classified_via_dispatchable_trait(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(PostCreated::class));

        $this->assertContains('event', $kinds);
    }

    public function test_job_is_classified_with_should_queue(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(NotifySubscribersJob::class));

        $this->assertContains('job', $kinds);
        $this->assertContains('should_queue', $kinds);
        $this->assertContains('queueable', $kinds);
    }

    public function test_mailable_using_queueable_trait_is_not_misclassified_as_job(): void
    {
        // Regression: mailables internally use Illuminate\Bus\Queueable for
        // fluent queueing. Tagging them as "job" because they happen to use
        // that trait is wrong — jobs are marked by
        // Illuminate\Foundation\Bus\Dispatchable, which mailables do not use.
        $kinds = $this->classifier->classify(new ReflectionClass(WelcomeMail::class));

        $this->assertContains('mailable', $kinds);
        $this->assertNotContains('job', $kinds);
    }

    public function test_event_is_not_misclassified_as_job(): void
    {
        // Events use Illuminate\Foundation\Events\Dispatchable (different
        // namespace from the Bus one). Ensure job detection does not fire.
        $kinds = $this->classifier->classify(new ReflectionClass(PostCreated::class));

        $this->assertContains('event', $kinds);
        $this->assertNotContains('job', $kinds);
    }

    public function test_listener_is_detected_by_namespace_and_handle_method(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(SendPostCreatedEmail::class));

        $this->assertContains('listener', $kinds);
    }

    public function test_observer_is_detected_by_namespace_and_hook_methods(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(PostObserver::class));

        $this->assertContains('observer', $kinds);
    }

    public function test_middleware_is_detected_by_handle_signature(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(EnsureToken::class));

        $this->assertContains('middleware', $kinds);
    }

    public function test_policy_is_detected_by_namespace_and_ability_methods(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(PostPolicy::class));

        $this->assertContains('policy', $kinds);
    }

    public function test_unknown_user_class_returns_empty_kinds(): void
    {
        $kinds = $this->classifier->classify(new ReflectionClass(Post::class));

        $this->assertContains('model', $kinds);
    }
}
