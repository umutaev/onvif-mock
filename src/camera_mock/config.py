from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import yaml

from camera_mock.models import AuthConfig, MockConfig, MockDevice, PortConfig, StreamProfile


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MediaMetadata:
    codec: str
    width: int
    height: int
    fps: float
    bitrate: int | None
    duration: float | None


def load_config(path: Path) -> MockConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Unable to read config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    return parse_config(raw, base_dir=Path.cwd())


def parse_config(raw: Any, *, base_dir: Path) -> MockConfig:
    if not isinstance(raw, Mapping):
        raise ConfigError("Config root must be a mapping.")

    ports = _parse_ports(raw.get("ports", {}))
    auth = _parse_auth(raw.get("auth", {}))
    devices = _parse_devices(raw.get("devices"), ports=ports, base_dir=base_dir)
    return MockConfig(auth=auth, ports=ports, devices=tuple(devices))


def _parse_ports(raw: Any) -> PortConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError("ports must be a mapping.")
    rtsp = _int(raw.get("rtsp", 8554), field="ports.rtsp")
    onvif_start = _int(raw.get("onvif_start", 8000), field="ports.onvif_start")
    _validate_port(rtsp, "ports.rtsp")
    _validate_port(onvif_start, "ports.onvif_start")
    return PortConfig(rtsp=rtsp, onvif_start=onvif_start)


def _parse_auth(raw: Any) -> AuthConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError("auth must be a mapping.")
    enabled = bool(raw.get("enabled", False))
    username = str(raw.get("username", "admin"))
    password = str(raw.get("password", "admin"))
    if enabled and (not username or not password):
        raise ConfigError("auth.username and auth.password are required when auth.enabled is true.")
    return AuthConfig(enabled=enabled, username=username, password=password)


def _parse_devices(raw: Any, *, ports: PortConfig, base_dir: Path) -> list[MockDevice]:
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ConfigError("devices must be a non-empty list.")
    if not raw:
        raise ConfigError("At least one device is required.")

    devices: list[MockDevice] = []
    used_ports: set[int] = set()
    used_paths: set[str] = set()
    used_uuids: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ConfigError(f"devices[{index}] must be a mapping.")
        device = _parse_device(item, index=index, ports=ports, base_dir=base_dir)
        if device.http_port in used_ports:
            raise ConfigError(f"Duplicate ONVIF HTTP port {device.http_port}.")
        if device.uuid in used_uuids:
            raise ConfigError(f"Duplicate device uuid {device.uuid}.")
        for profile in device.profiles:
            if profile.path in used_paths:
                raise ConfigError(f"Duplicate RTSP path {profile.path}.")
            used_paths.add(profile.path)
        used_ports.add(device.http_port)
        used_uuids.add(device.uuid)
        devices.append(device)
    return devices


def _parse_device(raw: Mapping[str, Any], *, index: int, ports: PortConfig, base_dir: Path) -> MockDevice:
    device_id = _text(raw.get("id", f"camera-{index + 1}"), field=f"devices[{index}].id")
    http_port = _int(raw.get("http_port", ports.onvif_start + index), field=f"devices[{index}].http_port")
    _validate_port(http_port, f"devices[{index}].http_port")
    uuid = str(raw.get("uuid") or uuid5(NAMESPACE_URL, f"camera-mock:{device_id}"))
    profiles = _parse_profiles(raw.get("profiles"), device_id=device_id, device_index=index, base_dir=base_dir)
    scopes = raw.get("scopes") or [
        "onvif://www.onvif.org/type/video_encoder",
        f"onvif://www.onvif.org/name/{device_id}",
        "onvif://www.onvif.org/Profile/Streaming",
        "onvif://www.onvif.org/Profile/T",
    ]
    if not isinstance(scopes, Sequence) or isinstance(scopes, str):
        raise ConfigError(f"devices[{index}].scopes must be a list.")
    return MockDevice(
        device_id=device_id,
        uuid=f"urn:uuid:{uuid.removeprefix('urn:uuid:')}",
        hostname=_text(raw.get("hostname", device_id), field=f"devices[{index}].hostname"),
        http_port=http_port,
        manufacturer=str(raw.get("manufacturer", "Camera Mock")),
        model=str(raw.get("model", "Profile T Mock")),
        serial=str(raw.get("serial", device_id)),
        firmware=str(raw.get("firmware", "0.1.0")),
        hardware=str(raw.get("hardware", "software")),
        profiles=tuple(profiles),
        scopes=tuple(str(scope) for scope in scopes),
    )


def _parse_profiles(raw: Any, *, device_id: str, device_index: int, base_dir: Path) -> list[StreamProfile]:
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ConfigError(f"devices[{device_index}].profiles must be a non-empty list.")
    if not raw:
        raise ConfigError(f"devices[{device_index}] must define at least one profile.")

    profiles: list[StreamProfile] = []
    used_tokens: set[str] = set()
    for profile_index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ConfigError(f"devices[{device_index}].profiles[{profile_index}] must be a mapping.")
        token = _text(
            item.get("token", f"profile-{profile_index + 1}"),
            field=f"devices[{device_index}].profiles[{profile_index}].token",
        )
        if token in used_tokens:
            raise ConfigError(f"Duplicate profile token {token} on device {device_id}.")
        used_tokens.add(token)
        media_file = _path(
            item.get("media_file"), field=f"devices[{device_index}].profiles[{profile_index}].media_file"
        )
        if not media_file.is_absolute():
            media_file = (base_dir / media_file).resolve()
        if not media_file.is_file():
            raise ConfigError(f"Media file does not exist: {media_file}")
        metadata = _probe_media(media_file)
        codec = _profile_codec(item, metadata)
        profiles.append(
            StreamProfile(
                token=token,
                name=str(item.get("name", token)),
                media_file=media_file,
                path=_stream_path(str(item.get("path", f"{device_id}/{token}"))),
                width=_int(
                    item.get("width", metadata.width),
                    field=f"devices[{device_index}].profiles[{profile_index}].width",
                ),
                height=_int(
                    item.get("height", metadata.height),
                    field=f"devices[{device_index}].profiles[{profile_index}].height",
                ),
                fps=_float(
                    item.get("fps", metadata.fps), field=f"devices[{device_index}].profiles[{profile_index}].fps"
                ),
                bitrate=_optional_int(
                    item.get("bitrate", metadata.bitrate),
                    field=f"devices[{device_index}].profiles[{profile_index}].bitrate",
                ),
                duration=_optional_float(
                    item.get("duration", metadata.duration),
                    field=f"devices[{device_index}].profiles[{profile_index}].duration",
                ),
                codec=codec,
            ),
        )
    return profiles


def _profile_codec(raw: Mapping[str, Any], metadata: MediaMetadata) -> str:
    if metadata.codec not in {"H264", "H265"}:
        raise ConfigError("Only H.264/AVC and H.265/HEVC streams are supported.")
    codec = _normalize_video_codec(raw.get("codec", metadata.codec))
    if codec not in {"H264", "H265"}:
        raise ConfigError("Only H.264/AVC and H.265/HEVC streams are supported.")
    if codec != metadata.codec:
        raise ConfigError(f"Configured codec {codec} does not match probed media codec {metadata.codec}.")
    return codec


def _stream_path(value: str) -> str:
    cleaned = value.strip("/")
    if not cleaned or any(part in {"", ".", ".."} for part in cleaned.split("/")):
        raise ConfigError(f"Invalid RTSP path: {value}")
    return cleaned


def _text(value: Any, *, field: str) -> str:
    text = str(value)
    if not text:
        raise ConfigError(f"{field} must not be empty.")
    return text


def _path(value: Any, *, field: str) -> Path:
    if value is None:
        raise ConfigError(f"{field} is required.")
    return Path(str(value)).expanduser()


def _int(value: Any, *, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be an integer.") from exc


def _float(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be a number.") from exc
    if result <= 0:
        raise ConfigError(f"{field} must be greater than 0.")
    return result


def _optional_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _int(value, field=field)


def _optional_float(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    return _float(value, field=field)


def _validate_port(value: int, field: str) -> None:
    if not 1 <= value <= 65535:
        raise ConfigError(f"{field} must be between 1 and 65535.")


def _probe_media(media_file: Path) -> MediaMetadata:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate",
        "-show_entries",
        "format=duration,bit_rate",
        "-of",
        "json",
        str(media_file),
    ]
    result = subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise ConfigError(f"ffprobe failed for {media_file}: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"ffprobe returned invalid JSON for {media_file}.") from exc
    return _metadata_from_probe(payload, media_file=media_file)


def _metadata_from_probe(payload: Mapping[str, Any], *, media_file: Path) -> MediaMetadata:
    streams = payload.get("streams")
    if not isinstance(streams, Sequence) or not streams or not isinstance(streams[0], Mapping):
        raise ConfigError(f"No video stream found in {media_file}.")
    stream = streams[0]
    format_data = payload.get("format", {})
    if not isinstance(format_data, Mapping):
        format_data = {}
    codec = _normalize_video_codec(stream.get("codec_name", ""))
    if not codec:
        raise ConfigError(f"Unable to infer video codec for {media_file}.")
    return MediaMetadata(
        codec=codec,
        width=_required_probe_int(stream.get("width"), field="width", media_file=media_file),
        height=_required_probe_int(stream.get("height"), field="height", media_file=media_file),
        fps=_probe_fps(stream, media_file=media_file),
        bitrate=_probe_optional_int(stream.get("bit_rate")) or _probe_optional_int(format_data.get("bit_rate")),
        duration=_probe_optional_float(format_data.get("duration")),
    )


def _probe_fps(stream: Mapping[str, Any], *, media_file: Path) -> float:
    for field in ("avg_frame_rate", "r_frame_rate"):
        value = stream.get(field)
        if isinstance(value, str) and value not in {"", "0/0"}:
            try:
                fps = float(Fraction(value))
            except ValueError, ZeroDivisionError:
                continue
            if fps > 0:
                return fps
    raise ConfigError(f"Unable to infer frame rate for {media_file}.")


def _required_probe_int(value: Any, *, field: str, media_file: Path) -> int:
    result = _probe_optional_int(value)
    if result is None:
        raise ConfigError(f"Unable to infer {field} for {media_file}.")
    return result


def _probe_optional_int(value: Any) -> int | None:
    if value in {None, "N/A", ""}:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _probe_optional_float(value: Any) -> float | None:
    if value in {None, "N/A", ""}:
        return None
    try:
        result = float(value)
    except TypeError, ValueError:
        return None
    return result if result > 0 else None


def _normalize_video_codec(value: Any) -> str:
    codec = str(value).upper().replace(".", "").replace("-", "")
    match codec:
        case "H264" | "AVC" | "AVC1":
            return "H264"
        case "H265" | "HEVC" | "HVC1":
            return "H265"
        case _:
            return codec
