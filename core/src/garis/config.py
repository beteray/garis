"""User-visible settings.

Design rule: nothing here may require the user to understand GARIS internals.
There is no "which model for which task" table and no per-tool permission grid —
those are the runtime's job. What lives here is what a person actually has an
opinion about: their name, how GARIS talks to them, quiet hours, which mic,
which voice, what it may spend.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import types
import typing
from dataclasses import dataclass, field
from datetime import time as clock
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .paths import Paths


@dataclass
class Identity:
    """Who the user is, in GARIS's words."""

    name: str = ""
    address_as: str = ""          # "Michał", "szefie", "sir" — how GARIS opens
    language: str = "pl"
    timezone: str = ""            # empty = follow the OS
    onboarded: bool = False


@dataclass
class PersonaConfig:
    preset: str = "assistant"     # see persona.presets
    custom_prompt: str = ""       # only style; never rules (enforced in persona/)
    humor: float = 0.3            # 0..1
    brevity: float = 0.7          # 0..1, higher = shorter answers
    adapt_to_context: bool = True  # looser in chat, formal at work, terse in games


@dataclass
class VoiceConfig:
    enabled: bool = True
    wake_word: str = "garis"
    wake_word_enabled: bool = True
    push_to_talk: str = "ctrl+alt+space"
    open_window_hotkey: str = "ctrl+alt+g"
    input_device: str = ""        # empty = system default
    output_device: str = ""
    voice_id: str = "pl_female_calm"
    speech_rate: float = 1.0
    noise_floor_dbfs: float = -45.0   # set by calibration
    barge_in: bool = True
    engine: str = "auto"         # auto | provider_realtime | local


@dataclass
class ProviderConfig:
    """A model provider. The key itself lives in the vault, never here."""

    enabled: bool = True
    key_ref: str = ""            # e.g. "vault://openai_api_key"
    base_url: str = ""
    monthly_budget: float = 0.0  # 0 = no explicit cap


@dataclass
class ModelsConfig:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    privacy: str = "balanced"    # local_only | prefer_local | balanced | quality_first
    allow_cloud: bool = True
    monthly_budget: float = 0.0


@dataclass
class QuietHours:
    enabled: bool = True
    start: str = "23:00"
    end: str = "08:00"

    def _parse(self, value: str) -> clock:
        try:
            hh, mm = value.split(":")
            return clock(int(hh), int(mm))
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"Nieprawidłowa godzina: {value!r}") from exc

    def contains(self, moment: clock) -> bool:
        if not self.enabled:
            return False
        start, end = self._parse(self.start), self._parse(self.end)
        if start == end:
            return False
        if start < end:
            return start <= moment < end
        return moment >= start or moment < end  # window crosses midnight


@dataclass
class NotificationsConfig:
    quiet_hours: QuietHours = field(default_factory=QuietHours)
    suppress_while_gaming: bool = True
    min_importance: int = 3          # 1..5; below this GARIS stays silent
    briefing_on_startup: bool = True
    max_per_hour: int = 6


@dataclass
class AutonomyConfig:
    """How much GARIS does without checking in.

    Defaults follow the product rule: safety works underneath and does not get
    in the way; only final, hard-to-undo effects need a yes.
    """

    auto_install_free_software: bool = True
    download_notice_bytes: int = 2 * 1024**3      # >2 GB → mention it first
    spend_notice_amount: float = 0.0              # any money → always ask
    allow_admin_elevation: bool = True
    confirm_effects: list[str] = field(
        default_factory=lambda: [
            "payment",
            "publish",
            "send_message",
            "credentials",
            "delete_permanent",
        ]
    )
    max_repair_attempts: int = 3                  # self-healing budget per step


@dataclass
class TasksConfig:
    max_parallel: int = 4
    step_timeout_seconds: int = 900
    keep_finished_days: int = 30


@dataclass
class ApiConfig:
    host: str = "127.0.0.1"       # loopback only; mobile reaches it via a relay
    port: int = 8756
    token_ref: str = "vault://api_token"


@dataclass
class DevConfig:
    verbose: bool = False          # detailed reports in the UI
    developer_mode: bool = False   # raw plans, prompts, tool payloads
    log_level: str = "INFO"


@dataclass
class AppearanceConfig:
    """How the window looks. Settings, not preferences buried in a stylesheet.

    Every field here has exactly one reader — ``lib/appearance.ts`` turns it into
    attributes and custom properties on the document root, and the stylesheet
    reacts to those. Nothing else is allowed to decide how anything looks, which
    is what makes "zmień akcent" one change instead of a hunt.
    """

    theme: str = "system"            # system | dark | light
    accent: str = "cyan"             # a preset name, or "#rrggbb"
    glass: float = 1.0               # 0 = plain surfaces, 1 = full Liquid Glass
    density: str = "comfortable"     # comfortable | compact
    text_scale: float = 1.0          # 0.9 .. 1.4
    animation: str = "system"        # system (follow the OS) | full | off
    orb: str = "full"                # full | simple | off
    navigation: str = "auto"         # auto (follow the window) | labels | rail
    sounds: bool = False
    high_contrast: bool = False


#: Accent presets, as HSL triples so they compose with the alpha the glass needs.
ACCENTS: dict[str, str] = {
    "cyan": "196 95% 60%",
    "blue": "212 92% 64%",
    "violet": "268 85% 68%",
    "teal": "168 78% 52%",
    "green": "148 70% 52%",
    "amber": "38 92% 62%",
    "rose": "348 88% 66%",
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

#: Enumerated appearance fields and what they accept. One table, so the API, the
#: CLI and the settings screen cannot disagree about what is valid.
_APPEARANCE_CHOICES: dict[str, tuple[str, ...]] = {
    "theme": ("system", "dark", "light"),
    "density": ("comfortable", "compact"),
    "animation": ("system", "full", "off"),
    "orb": ("full", "simple", "off"),
    "navigation": ("auto", "labels", "rail"),
}


@dataclass
class Config:
    identity: Identity = field(default_factory=Identity)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    tasks: TasksConfig = field(default_factory=TasksConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    dev: DevConfig = field(default_factory=DevConfig)
    appearance: AppearanceConfig = field(default_factory=AppearanceConfig)
    autostart: bool = True

    # --- persistence ---

    @classmethod
    def load(cls, paths: Paths) -> Config:
        if not paths.config_file.exists():
            return cls()
        try:
            raw = json.loads(paths.config_file.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Uszkodzony plik konfiguracji: {paths.config_file}") from exc
        return cls.from_dict(raw)

    def validate(self) -> None:
        """Reject values no screen should be able to produce.

        ``set`` only coerces types, so without this "theme": "purpurowy" would be
        stored happily and then silently ignored by the stylesheet — a setting
        that says it was saved and does nothing.
        """
        look = self.appearance
        for field_name, allowed in _APPEARANCE_CHOICES.items():
            value = getattr(look, field_name)
            if value not in allowed:
                raise ConfigError(
                    f"appearance.{field_name}: \u201e{value}\u201d nie jest jedn\u0105 z "
                    f"{', '.join(allowed)}"
                )
        if look.accent not in ACCENTS and not _HEX.match(look.accent):
            raise ConfigError(
                f"appearance.accent: \u201e{look.accent}\u201d to ani jeden z "
                f"{', '.join(ACCENTS)}, ani kolor #rrggbb"
            )
        if not 0.0 <= look.glass <= 1.0:
            raise ConfigError("appearance.glass mieści się w 0..1")
        if not 0.9 <= look.text_scale <= 1.4:
            raise ConfigError("appearance.text_scale mieści się w 0.9..1.4")

    def save(self, paths: Paths) -> None:
        paths.ensure()
        tmp = paths.config_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, paths.config_file)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        return _build(cls, data)

    # --- reloading ---

    def adopt(self, other: Config) -> tuple[str, ...]:
        """Take on another config's values *without becoming another object*.

        Reloading by replacing ``garis.config`` would be simpler and wrong: the
        policy engine holds ``config.autonomy``, the router holds ``config.models``,
        the notification gate holds ``config.notifications``. Rebinding the top
        object leaves every one of them reading a config nobody edits any more.
        So values move and identities stay.

        Returns the dotted paths that actually changed — an empty tuple means the
        file was rewritten with the same content and nobody needs to react.
        """
        return tuple(_adopt(self, other, ""))

    # --- dotted access, used by the settings UI and `garis config` ---

    def get(self, path: str) -> Any:
        node: Any = self
        for part in path.split("."):
            if dataclasses.is_dataclass(node) and not isinstance(node, type):
                node = getattr(node, part)
            elif isinstance(node, dict):
                node = node[part]
            else:
                raise ConfigError(f"Nieznane ustawienie: {path}")
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node: Any = self
        for part in parts[:-1]:
            if dataclasses.is_dataclass(node) and not isinstance(node, type):
                node = getattr(node, part)
            elif isinstance(node, dict):
                node = node[part]
            else:
                raise ConfigError(f"Nieznane ustawienie: {path}")
        leaf = parts[-1]
        if isinstance(node, dict):
            node[leaf] = value
            return
        if not hasattr(node, leaf):
            raise ConfigError(f"Nieznane ustawienie: {path}")
        current = getattr(node, leaf)
        setattr(node, leaf, _coerce_like(current, value))


def _unwrap_optional(annotation: Any) -> Any:
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _build(cls: type, data: Any) -> Any:
    """Rebuild a dataclass tree from JSON, ignoring unknown keys.

    Tolerance matters here: a config written by a newer GARIS must still open in
    an older one rather than refusing to start.
    """
    if not isinstance(data, dict):
        raise ConfigError(f"Oczekiwano obiektu dla {cls.__name__}")
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        annotation = _unwrap_optional(hints.get(f.name, Any))
        origin = typing.get_origin(annotation)
        if isinstance(annotation, type) and dataclasses.is_dataclass(annotation) \
                and isinstance(value, dict):
            kwargs[f.name] = _build(annotation, value)
        elif origin is dict:
            key_t, val_t = typing.get_args(annotation)
            del key_t
            if isinstance(val_t, type) and dataclasses.is_dataclass(val_t) \
                    and isinstance(value, dict):
                kwargs[f.name] = {k: _build(val_t, v) for k, v in value.items()}
            else:
                kwargs[f.name] = value
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


def _adopt(target: Any, source: Any, prefix: str) -> list[str]:
    changed: list[str] = []
    for f in dataclasses.fields(target):
        path = f"{prefix}{f.name}"
        current, incoming = getattr(target, f.name), getattr(source, f.name)
        if dataclasses.is_dataclass(current) and type(current) is type(incoming):
            changed += _adopt(current, incoming, f"{path}.")
        elif isinstance(current, dict) and isinstance(incoming, dict):
            changed += _adopt_dict(current, incoming, path)
        elif current != incoming:
            setattr(target, f.name, incoming)
            changed.append(path)
    return changed


def _adopt_dict(target: dict[Any, Any], source: dict[Any, Any], prefix: str) -> list[str]:
    """Same rules one level down, for maps like ``models.providers``.

    A removed key is a change too: deleting a provider has to reach the router.
    """
    changed: list[str] = []
    for key in list(target):
        if key not in source:
            del target[key]
            changed.append(f"{prefix}.{key}")
    for key, incoming in source.items():
        path = f"{prefix}.{key}"
        current = target.get(key)
        if dataclasses.is_dataclass(current) and type(current) is type(incoming):
            changed += _adopt(current, incoming, f"{path}.")
        elif key not in target or current != incoming:
            target[key] = incoming
            changed.append(path)
    return changed


def _coerce_like(current: Any, value: Any) -> Any:
    """Let `garis config set voice.enabled false` do the obvious thing."""
    if isinstance(current, bool):
        if isinstance(value, str):
            low = value.strip().lower()
            if low in {"1", "true", "tak", "yes", "on"}:
                return True
            if low in {"0", "false", "nie", "no", "off"}:
                return False
            raise ConfigError(f"Oczekiwano tak/nie, otrzymano {value!r}")
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, list) and isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def load_config(home: str | Path | None = None) -> tuple[Config, Paths]:
    paths = Paths.resolve(home).ensure()
    return Config.load(paths), paths


__all__ = [
    "ApiConfig",
    "AutonomyConfig",
    "Config",
    "DevConfig",
    "Identity",
    "ModelsConfig",
    "NotificationsConfig",
    "PersonaConfig",
    "ProviderConfig",
    "QuietHours",
    "TasksConfig",
    "VoiceConfig",
    "load_config",
]
