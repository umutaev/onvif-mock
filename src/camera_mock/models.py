from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool = False
    username: str = "admin"
    password: str = "admin"  # noqa: S105


@dataclass(frozen=True)
class PortConfig:
    rtsp: int = 8554
    onvif_start: int = 8000


@dataclass(frozen=True)
class StreamProfile:
    token: str
    name: str
    media_file: Path
    path: str
    width: int
    height: int
    fps: float
    bitrate: int | None = None
    duration: float | None = None
    codec: str = "H265"


@dataclass(frozen=True)
class MockDevice:
    device_id: str
    uuid: str
    hostname: str
    http_port: int
    manufacturer: str
    model: str
    serial: str
    firmware: str
    hardware: str
    profiles: tuple[StreamProfile, ...]
    scopes: tuple[str, ...]

    def device_service_url(self, host: str) -> str:
        return f"http://{host}:{self.http_port}/onvif/device_service"

    def media_service_url(self, host: str) -> str:
        return f"http://{host}:{self.http_port}/onvif/media_service"

    def media2_service_url(self, host: str) -> str:
        return f"http://{host}:{self.http_port}/onvif/media2_service"

    def snapshot_uri(self, host: str, profile: StreamProfile) -> str:
        return f"http://{host}:{self.http_port}/snapshot/{quote(profile.token, safe='')}.jpg"


@dataclass(frozen=True)
class MockConfig:
    auth: AuthConfig
    ports: PortConfig
    devices: tuple[MockDevice, ...]

    def rtsp_uri(self, host: str, profile: StreamProfile) -> str:
        if self.auth.enabled:
            username = quote(self.auth.username, safe="")
            password = quote(self.auth.password, safe="")
            return f"rtsp://{username}:{password}@{host}:{self.ports.rtsp}/{profile.path}"
        return f"rtsp://{host}:{self.ports.rtsp}/{profile.path}"

    @property
    def profiles(self) -> tuple[StreamProfile, ...]:
        return tuple(profile for device in self.devices for profile in device.profiles)
