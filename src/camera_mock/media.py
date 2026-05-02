from __future__ import annotations

from pathlib import Path

import yaml

from camera_mock.models import MockConfig, StreamProfile


def rtsp_publish_uri(config: MockConfig, profile: StreamProfile) -> str:
    return f"rtsp://127.0.0.1:{config.ports.rtsp}/{profile.path}"


def mediamtx_config(config: MockConfig) -> str:
    paths: dict[str, dict[str, object]] = {}
    for profile in config.profiles:
        paths[profile.path] = {}

    raw: dict[str, object] = {
        "rtspAddress": f":{config.ports.rtsp}",
        "rtspTransports": ["tcp"],
        "paths": paths,
    }
    raw["authInternalUsers"] = _auth_users(config)
    return yaml.safe_dump(raw, sort_keys=False)


def write_mediamtx_config(config: MockConfig, path: Path) -> None:
    path.write_text(mediamtx_config(config), encoding="utf-8")


def ffmpeg_publish_command(ffmpeg_bin: str, config: MockConfig, profile: StreamProfile) -> list[str]:
    return [
        ffmpeg_bin,
        "-nostats",
        "-loglevel",
        "warning",
        "-re",
        "-stream_loop",
        "-1",
        "-i",
        str(profile.media_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_publish_uri(config, profile),
    ]


def _auth_users(config: MockConfig) -> list[dict[str, object]]:
    if not config.auth.enabled:
        return [
            {
                "user": "any",
                "pass": "",
                "ips": [],
                "permissions": [
                    {"action": "publish", "path": ""},
                    {"action": "read", "path": ""},
                    {"action": "playback", "path": ""},
                    {"action": "api", "path": ""},
                    {"action": "metrics", "path": ""},
                    {"action": "pprof", "path": ""},
                ],
            },
        ]
    return [
        {
            "user": "any",
            "pass": "",
            "ips": ["127.0.0.1", "::1"],
            "permissions": [
                {"action": "publish", "path": ""},
                {"action": "api", "path": ""},
                {"action": "metrics", "path": ""},
                {"action": "pprof", "path": ""},
            ],
        },
        {
            "user": config.auth.username,
            "pass": config.auth.password,
            "ips": [],
            "permissions": [
                {"action": "read", "path": ""},
                {"action": "playback", "path": ""},
            ],
        },
    ]
