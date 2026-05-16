<?php

use Illuminate\Support\Facades\Route;
use NexusFixtures\Sample\Http\Controllers\SampleController;

Route::get('/sample', [SampleController::class, 'show'])->name('sample.show');
