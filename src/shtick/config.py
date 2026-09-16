"""Settings, profiles and their on-disk locations."""

from __future__ import annotations

import ast
import dataclasses
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

EDITING_MODES = ("emacs", "vi")


@dataclass
class Settings:
    theme: str = "void"
    editing_mode: str = "emacs"
    shell: str = "bash"
    lint: bool = True  # live shellcheck while typing, when shellcheck is installed
    lint_exclude: list[str] = field(default_factory=lambda: ["SC2034", "SC2154"])  # cells see state from other cells
    tty: bool = False
    save_shebang: str = ""  # %save: "" picks `#!/usr/bin/env <shell>`
    startup: list[str] = field(default_factory=list)  # shell lines run in every new session

    def validate(self, name: str, value: Any) -> Any:
        """Coerce and check a value for setting `name`; raises ValueError/KeyError."""
        spec = {f.name: f for f in fields(self)}[name]
        default = spec.default if spec.default is not dataclasses.MISSING else spec.default_factory()  # type: ignore[misc]
        if isinstance(value, str) and not isinstance(default, str):
            try:
                value = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                lowered = value.strip().lower()
                if isinstance(default, bool) and lowered in ("on", "off", "yes", "no", "true", "false"):
                    value = lowered in ("on", "yes", "true")
                else:
                    raise ValueError(f"{name} expects a {type(default).__name__}") from None
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, list):
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{name} expects a list")
            value = [str(v) for v in value]
        elif isinstance(default, str):
            value = str(value).strip().strip("'\"")
        if name == "editing_mode":
            value = value.lower()
            if value not in EDITING_MODES:
                raise ValueError(f"{name} must be one of: {', '.join(EDITING_MODES)}")
        return value


def setting_names() -> list[str]:
    return [f.name for f in fields(Settings)]


@dataclass
class Profile:
    name: str
    config_dir: Path
    data_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def history_db(self) -> Path:
        return self.data_dir / "history.sqlite"

    def ensure(self) -> None:
        for d in (self.config_dir, self.data_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass


def _base(env: str, fallback: str) -> Path:
    return Path(os.environ.get(env) or os.path.expanduser(fallback)) / "shtick"


def load_profile(name: str | None = None) -> Profile:
    name = name or os.environ.get("SHTICK_PROFILE") or "default"
    config, data = _base("XDG_CONFIG_HOME", "~/.config"), _base("XDG_DATA_HOME", "~/.local/share")
    if name != "default":
        config, data = config / "profiles" / name, data / "profiles" / name
    profile = Profile(name, config, data)
    profile.ensure()
    return profile


def load_settings(profile: Profile | None) -> tuple[Settings, list[str]]:
    """Read the profile's config.toml. Returns the settings and any warnings."""
    settings = Settings()
    warnings: list[str] = []
    data: dict[str, Any] = {}
    if profile is not None and profile.config_file.exists():
        try:
            import tomllib
        except ImportError:  # Python 3.10
            import tomli as tomllib  # type: ignore[no-redef]
        try:
            data = tomllib.loads(profile.config_file.read_text())
        except (OSError, ValueError) as e:
            warnings.append(f"could not read {profile.config_file}: {e}")
    for env, key in (("SHTICK_THEME", "theme"), ("SHTICK_SHELL", "shell")):
        if value := os.environ.get(env):
            data[key] = value
    for key, value in data.items():
        try:
            setattr(settings, key, settings.validate(key, value))
        except KeyError:
            warnings.append(f"config: unknown setting {key!r}")
        except ValueError as e:
            warnings.append(f"config: {key}: {e}")
    return settings, warnings
