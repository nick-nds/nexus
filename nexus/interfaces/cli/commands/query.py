"""Auto-generated ``nexus query <tool>`` subcommand tree.

The Phase 4 :class:`ToolRegistry` is the single source of truth for
every question-answering tool. Rather than hand-write a Click
subcommand per tool - and keep the two in sync forever - the CLI
iterates the registry at import time and generates one subcommand
per tool. Each tool's Pydantic ``input_model`` drives the subcommand's
options: field names become option flags, annotations become Click
types, Pydantic ``Field(description=...)`` becomes the ``help`` text.

Design notes
============

* The registry walk happens **once at module import time**. That's
  the same lifecycle as Click's own decorators, so adding a tool
  means editing one file in ``nexus.core.query.tools`` and rerunning
  the CLI - no rebuild, no plugin dance.
* The subcommand is generated even if the tool input model is empty
  (e.g. ``list_routes``); Click is happy with zero-option commands.
* The generator handles the shapes our tools actually use today
  (``str``, ``str | None``, ``int``, ``bool``). More exotic field
  types (lists, dicts, enums) are rejected loudly so we never
  silently produce a broken command - a new tool needs an opinion
  about how its fancy input surfaces in the CLI.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING, Any

import click
from pydantic_core import PydanticUndefined

from nexus.core.query import ToolInputError, ToolNotFoundError, ToolRegistry
from nexus.core.query.tools import register_builtin_tools
from nexus.interfaces.cli.output import print_error, render

if TYPE_CHECKING:
    from pydantic import BaseModel
    from pydantic.fields import FieldInfo

    from nexus.interfaces.cli.context import CliContext

_OPTIONAL_UNION_ARGS = 2


@click.group(name="query", help="Run a structural or semantic query tool directly.")
def query_group() -> None:
    """Parent group for auto-generated per-tool subcommands."""


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


_REGISTRY = ToolRegistry()
register_builtin_tools(_REGISTRY)


def _iter_options(
    model: type[BaseModel],
) -> list[tuple[str, FieldInfo, Any, bool]]:
    """Return ``(flag_name, field_info, click_type, required)`` per field.

    Resolves ``str | None`` / ``int | None`` unions into the underlying
    primitive so Click users can pass plain values. Raises
    :class:`RuntimeError` if a field uses an unsupported shape - that
    way a broken tool surfaces at import time rather than at runtime.
    """
    out: list[tuple[str, FieldInfo, Any, bool]] = []
    for name, field in model.model_fields.items():
        annotation = field.annotation
        primitive, optional = _unwrap_optional(annotation)
        click_type = _click_type_for(primitive)
        if click_type is None:
            msg = (
                f"Tool input field {model.__name__}.{name!r} has unsupported "
                f"type {annotation!r} for CLI auto-generation. Add a manual "
                f"override or lower the type to str/int/bool."
            )
            raise RuntimeError(msg)
        is_required = field.is_required() and not optional
        flag = f"--{name.replace('_', '-')}"
        out.append((flag, field, click_type, is_required))
    return out


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Peel off ``Optional[X]`` / ``X | None`` into ``(X, True)``."""
    origin = getattr(annotation, "__origin__", None)
    if origin is types.UnionType or origin is type(None).__class__.__mro__[0]:
        pass  # Fall through to args inspection below.
    args = getattr(annotation, "__args__", None)
    if args is None:
        return annotation, False
    non_none = [a for a in args if a is not type(None)]
    if len(non_none) == 1 and len(args) == _OPTIONAL_UNION_ARGS:
        return non_none[0], True
    return annotation, False


def _click_type_for(python_type: Any) -> Any:
    """Map a primitive Python type to a Click param type."""
    if python_type is str:
        return click.STRING
    if python_type is int:
        return click.INT
    if python_type is bool:
        return click.BOOL
    if python_type is float:
        return click.FLOAT
    return None


def _make_callback(tool_name: str) -> Any:
    """Build a Click callback that runs ``tool_name`` via the engine."""

    def _callback(cli_ctx: CliContext, /, **kwargs: Any) -> None:
        # Drop options the user didn't pass so the Pydantic model
        # falls back to its own defaults instead of seeing an
        # explicit None on every unset flag.
        payload = {k: v for k, v in kwargs.items() if v is not None}
        try:
            result = cli_ctx.engine().query(tool_name, payload)
        except ToolInputError as e:
            print_error(cli_ctx, str(e), hint=f"check `nexus query {tool_name} --help`")
            raise click.exceptions.Exit(2) from e
        except ToolNotFoundError as e:
            # Should be impossible at runtime because we generate from
            # the registry - but belt-and-braces.
            print_error(cli_ctx, str(e))
            raise click.exceptions.Exit(2) from e
        # Read project metadata so the renderer can attach the
        # attribution block/footer for package-kind projects (decision #10).
        # read_meta() returns None when no meta.json exists yet; render()
        # handles that gracefully - project-kind output is unchanged.
        meta = cli_ctx.storage().read_meta()
        render(cli_ctx, result, meta=meta)

    return click.pass_obj(_callback)


def _build_subcommand(tool_name: str, tool_class: type[Any]) -> click.Command:
    """Create one ``nexus query <tool>`` Click command from metadata."""
    callback = _make_callback(tool_name)
    options = _iter_options(tool_class.input_model)

    command = click.Command(
        name=tool_name,
        callback=callback,
        help=tool_class.description,
    )
    # Add options in declaration order so ``--help`` is stable.
    for flag, field, click_type, required in options:
        raw_default = field.default
        default = None if raw_default is PydanticUndefined else raw_default
        # Boolean fields become ``--flag / --no-flag`` pairs so users
        # can explicitly disable a default-on knob.
        if click_type is click.BOOL:
            flag_name = flag.lstrip("-")
            command.params.append(
                click.Option(
                    [f"--{flag_name}/--no-{flag_name}"],
                    default=bool(default) if default is not None else False,
                    help=field.description,
                    is_flag=True,
                ),
            )
            continue
        # Click 9 treats ``default=None`` as "value provided", so
        # passing it would defeat ``required=True``. Only pass a
        # default when we genuinely have one to offer.
        option_kwargs: dict[str, Any] = {
            "type": click_type,
            "required": required,
            "help": field.description,
        }
        if default is not None:
            option_kwargs["default"] = default
            option_kwargs["show_default"] = True
        command.params.append(click.Option([flag], **option_kwargs))
    return command


def _generate_subcommands() -> None:
    """Attach one subcommand per registered tool to :data:`query_group`."""
    for entry in _REGISTRY.tools():
        command = _build_subcommand(entry.name, entry.tool_class)
        query_group.add_command(command)


_generate_subcommands()
