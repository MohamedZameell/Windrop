from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any
import winreg

from windrop.utils.logger import get_logger


APP_DIR = Path.home() / "AppData" / "Roaming" / "WinDrop"
CONFIG_PATH = APP_DIR / "config.json"
CONFIG_TMP_PATH = APP_DIR / "config.tmp"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "WinDrop"


@dataclass(slots=True)
class AppConfig:
    device_name: str
    receive_folder: Path
    start_on_startup: bool
    minimize_to_tray_on_close: bool

    @property
    def minimise_to_tray(self) -> bool:
        return self.minimize_to_tray_on_close


def get_app_dir() -> Path:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    return APP_DIR


def default_config() -> AppConfig:
    return AppConfig(
        device_name=socket.gethostname(),
        receive_folder=Path.home() / "Downloads" / "WinDrop",
        start_on_startup=False,
        minimize_to_tray_on_close=True,
    )


def config_to_payload(config: AppConfig) -> dict[str, Any]:
    return {
        "device_name": config.device_name,
        "receive_folder": str(config.receive_folder),
        "start_on_startup": config.start_on_startup,
        "minimize_to_tray_on_close": config.minimize_to_tray_on_close,
    }


def payload_to_config(payload: dict[str, Any] | None) -> AppConfig:
    defaults = default_config()
    payload = payload or {}
    return AppConfig(
        device_name=str(payload.get("device_name", defaults.device_name)),
        receive_folder=Path(str(payload.get("receive_folder", defaults.receive_folder))),
        start_on_startup=bool(payload.get("start_on_startup", defaults.start_on_startup)),
        minimize_to_tray_on_close=bool(
            payload.get("minimize_to_tray_on_close", defaults.minimize_to_tray_on_close)
        ),
    )


class SettingsManager:
    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self._logger = get_logger()
        self._config_path = config_path
        self._tmp_path = config_path.with_suffix(".tmp")
        self._data = config_to_payload(default_config())
        self.load()

    def load(self) -> dict[str, Any]:
        get_app_dir()
        try:
            with self._config_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                raise JSONDecodeError("Config root must be an object", "", 0)
        except (FileNotFoundError, JSONDecodeError, OSError) as exc:
            self._logger.warning("Falling back to default config: %s", exc)
            self._data = config_to_payload(default_config())
            return dict(self._data)

        config = payload_to_config(payload)
        self._data = config_to_payload(config)
        return dict(self._data)

    def save(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data, indent=2)

        try:
            with self._tmp_path.open("w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(self._tmp_path, self._config_path)
        except PermissionError as exc:
            self._logger.warning("Atomic config replace failed; falling back to direct write: %s", exc)
            try:
                with self._config_path.open("w", encoding="utf-8") as handle:
                    handle.write(payload)
            except OSError as write_exc:
                self._logger.warning("Direct config write failed: %s", write_exc)
            finally:
                if self._tmp_path.exists():
                    try:
                        self._tmp_path.unlink()
                    except OSError:
                        pass
        except OSError as exc:
            self._logger.warning("Config save failed: %s", exc)
            if self._tmp_path.exists():
                try:
                    self._tmp_path.unlink()
                except OSError:
                    pass

    def get(self, key: str) -> Any:
        defaults = config_to_payload(default_config())
        return self._data.get(key, defaults.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def as_config(self) -> AppConfig:
        return payload_to_config(self._data)


def get_settings_manager() -> SettingsManager:
    return SettingsManager()


def load_config() -> AppConfig:
    return get_settings_manager().as_config()


def save_config(config: AppConfig) -> None:
    manager = get_settings_manager()
    manager._data = config_to_payload(config)
    manager.save()


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        target = Path(sys.argv[0]).resolve()
    else:
        target = Path(sys.executable).resolve()
    return f'"{target}"'


def enable_startup() -> None:
    logger = get_logger()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, _startup_command())
    except OSError as exc:
        logger.warning("Unable to enable startup registration: %s", exc)


def disable_startup() -> None:
    logger = get_logger()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE_NAME)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Unable to disable startup registration: %s", exc)


def is_startup_enabled() -> bool:
    logger = get_logger()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, RUN_VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Unable to query startup registration: %s", exc)
        return False
