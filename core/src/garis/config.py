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
        if dataclasses.is_dataclass(annotation) and isinstance(value, dict):
            kwargs[f.name] = _build(annotation, value)
        elif origin is dict:
            key_t, val_t = typing.get_args(annotation)
            del key_t
            if dataclasses.is_dataclass(val_t) and isinstance(value, dict):
                kwargs[f.name] = {k: _build(val_t, v) for k, v in value.items()}
            else:
                kwargs[f.name] = value
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


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
