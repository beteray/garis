"""Tool registry — the complete, declared surface of what GARIS can do.

Two properties matter:

1. **It is the only menu.** The planner is shown this catalogue and nothing else,
   so a model cannot invent ``run_arbitrary_thing``. Unknown tool names fail
   before any code runs.
2. **Effects are declared here, not by the caller.** ``send_email`` says it sends
   a message; no phrasing of the request changes that, which is what lets the
   policy layer gate real consequences instead of guessing from prose.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..errors import ToolNotFound, ToolValidationError, Unsupported
from .action import Effect

if TYPE_CHECKING:  # pragma: no cover
    from .context import ToolContext

Handler = Callable[..., Awaitable[Any]]
ResourceFn = Callable[[dict[str, Any]], Sequence[str]]
EstimateFn = Callable[[dict[str, Any]], tuple[int, float]]

_MISSING = object()


@dataclass(frozen=True, slots=True)
class ParamSpec:
    type: str = "string"       # string | int | float | bool | list | dict | path | secret
    description: str = ""
    required: bool = False
    default: Any = None
    choices: tuple[Any, ...] = ()
    max_length: int | None = None

    def coerce(self, name: str, value: Any) -> Any:
        try:
            match self.type:
                case "string" | "path" | "secret":
                    out: Any = value if isinstance(value, str) else str(value)
                case "int":
                    out = int(value)
                case "float":
                    out = float(value)
                case "bool":
                    if isinstance(value, str):
                        low = value.strip().lower()
                        if low in {"true", "1", "yes", "tak"}:
                            out = True
                        elif low in {"false", "0", "no", "nie"}:
                            out = False
                        else:
                            raise ValueError(value)
                    else:
                        out = bool(value)
                case "list":
                    out = list(value) if isinstance(value, (list, tuple)) else [value]
                case "dict":
                    if not isinstance(value, dict):
                        raise ValueError("oczekiwano obiektu")
                    out = dict(value)
                case _:
                    out = value
        except (TypeError, ValueError) as exc:
            raise ToolValidationError(
                f"Parametr {name!r} powinien być typu {self.type}, otrzymano {value!r}"
            ) from exc

        if self.choices and out not in self.choices:
            allowed = ", ".join(str(c) for c in self.choices)
            raise ToolValidationError(
                f"Parametr {name!r} musi być jednym z: {allowed} (otrzymano {out!r})"
            )
        if self.max_length is not None and isinstance(out, (str, list)) and len(out) > self.max_length:
            raise ToolValidationError(
                f"Parametr {name!r} jest za długi (maks. {self.max_length})"
            )
        return out

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type}
        if self.description:
            schema["description"] = self.description
        if self.required:
            schema["required"] = True
        elif self.default is not None:
            schema["default"] = self.default
        if self.choices:
            schema["choices"] = list(self.choices)
        return schema


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    summary: str
    handler: Handler
    params: dict[str, ParamSpec] = field(default_factory=dict)
    effects: frozenset[Effect] = frozenset()
    platforms: frozenset[str] = frozenset({"*"})
    category: str = "general"
    timeout: float = 120.0
    reversible: bool = True
    trusted_source: bool = True        # for INSTALL: comes from a vetted repo
    resources: ResourceFn | None = None    # lease keys this action needs
    estimate: EstimateFn | None = None     # (bytes, money) for notice gates
    danger_note: str = ""              # shown verbatim in the approval prompt
    examples: tuple[str, ...] = ()

    def supported_here(self) -> bool:
        return "*" in self.platforms or sys.platform in self.platforms

    def require_supported(self) -> None:
        if not self.supported_here():
            raise Unsupported(
                f"Narzędzie {self.name!r} nie działa na {sys.platform}", tool=self.name
            )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        """Coerce and check parameters. Errors are phrased so a model can retry."""
        unknown = set(params) - set(self.params)
        if unknown:
            known = ", ".join(sorted(self.params)) or "(brak)"
            raise ToolValidationError(
                f"{self.name}: nieznane parametry {sorted(unknown)}. Dostępne: {known}"
            )
        out: dict[str, Any] = {}
        for name, spec in self.params.items():
            value = params.get(name, _MISSING)
            if value is _MISSING:
                if spec.required:
                    raise ToolValidationError(f"{self.name}: brakuje parametru {name!r}")
                if spec.default is not None:
                    out[name] = spec.default
                continue
            if value is None and not spec.required:
                continue
            out[name] = spec.coerce(name, value)
        return out

    def resource_keys(self, params: dict[str, Any]) -> tuple[str, ...]:
        if self.resources is None:
            return ()
        return tuple(self.resources(params))

    def estimates(self, params: dict[str, Any]) -> tuple[int, float]:
        if self.estimate is None:
            return (0, 0.0)
        return self.estimate(params)

    def describe_for_model(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "summary": self.summary,
            "params": {k: v.to_schema() for k, v in self.params.items()},
        }
        if self.effects:
            out["effects"] = sorted(e.value for e in self.effects)
        if self.examples:
            out["examples"] = list(self.examples)
        return out


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec, *, replace: bool = False) -> ToolSpec:
        if spec.name in self._tools and not replace:
            raise ValueError(f"Narzędzie {spec.name!r} jest już zarejestrowane")
        self._tools[spec.name] = spec
        return spec

    def tool(
        self,
        name: str,
        summary: str,
        *,
        params: dict[str, ParamSpec] | None = None,
        effects: Iterable[Effect] = (),
        platforms: Iterable[str] = ("*",),
        **kwargs: Any,
    ) -> Callable[[Handler], Handler]:
        """Decorator form used by every module in ``garis.tools``."""

        def decorate(handler: Handler) -> Handler:
            self.register(
                ToolSpec(
                    name=name,
                    summary=summary,
                    handler=handler,
                    params=params or {},
                    effects=frozenset(effects),
                    platforms=frozenset(platforms),
                    **kwargs,
                )
            )
            return handler

        return decorate

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(
                f"Nie ma narzędzia {name!r}. Dostępne: {', '.join(sorted(self._tools)) or '(brak)'}"
            ) from None

    def has(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda s: s.name)

    def available(self) -> list[ToolSpec]:
        """Tools usable on this host — what the planner is allowed to see."""
        return [s for s in self.all() if s.supported_here()]

    def by_category(self) -> dict[str, list[ToolSpec]]:
        grouped: dict[str, list[ToolSpec]] = {}
        for spec in self.available():
            grouped.setdefault(spec.category, []).append(spec)
        return grouped

    def catalog_for_model(self, categories: Iterable[str] | None = None) -> list[dict[str, Any]]:
        wanted = set(categories) if categories else None
        return [
            s.describe_for_model()
            for s in self.available()
            if wanted is None or s.category in wanted
        ]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


__all__ = ["Handler", "ParamSpec", "ToolRegistry", "ToolSpec"]
