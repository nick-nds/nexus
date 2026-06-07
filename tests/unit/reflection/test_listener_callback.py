"""``ListenerCallback`` carries the queued flag and registration source.

These fields back ``find_listeners``' ability to report whether a
listener implements ``ShouldQueue`` and whether it was wired via an
``EventServiceProvider::$listen`` map or discovered some other way
(auto-discovery, ``Event::listen``, a subscriber).
"""

from __future__ import annotations

from nexus.core.reflection.document import ListenerCallback


def test_parses_queued_flag_from_reflection_json() -> None:
    callback = ListenerCallback.model_validate(
        {
            "kind": "class",
            "class": "App\\Listeners\\Brand\\CreateCpanelSubdomain",
            "method": "handle",
            "queued": True,
        },
    )

    assert callback.queued is True


def test_parses_registration_source_from_reflection_json() -> None:
    callback = ListenerCallback.model_validate(
        {
            "kind": "class",
            "class": "App\\Listeners\\Brand\\CreateStoreInChannels",
            "method": "handle",
            "source": "discovered",
        },
    )

    assert callback.source == "discovered"


def test_queued_defaults_false_and_source_none_for_old_documents() -> None:
    callback = ListenerCallback.model_validate(
        {
            "kind": "class",
            "class": "App\\Listeners\\Legacy",
            "method": "handle",
        },
    )

    assert callback.queued is False
    assert callback.source is None
