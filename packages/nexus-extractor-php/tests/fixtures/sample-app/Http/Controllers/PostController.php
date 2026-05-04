<?php

declare(strict_types=1);

namespace SampleApp\Http\Controllers;

use Illuminate\Contracts\View\View;
use Illuminate\Http\RedirectResponse;
use Illuminate\Routing\Controller;
use SampleApp\Events\PostCreated;
use SampleApp\Http\Requests\StorePostRequest;
use SampleApp\Jobs\NotifySubscribersJob;
use SampleApp\Models\Post;

final class PostController extends Controller
{
    public function index(): View
    {
        return view('posts.index', ['posts' => Post::query()->get()]);
    }

    public function show(Post $post): View
    {
        $this->authorize('view', $post);

        return view('posts.show', ['post' => $post]);
    }

    public function store(StorePostRequest $request): RedirectResponse
    {
        $post = Post::query()->create($request->validated());

        event(new PostCreated($post));
        NotifySubscribersJob::dispatch($post);

        return redirect()->route('posts.show', $post);
    }
}
