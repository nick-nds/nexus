<?php

declare(strict_types=1);

namespace SampleApp\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

final class StorePostRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    /**
     * @return array<string, string|list<string>>
     */
    public function rules(): array
    {
        return [
            'title' => 'required|string|max:255',
            'body' => ['required', 'string', 'min:10'],
        ];
    }
}
