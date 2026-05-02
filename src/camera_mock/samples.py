from __future__ import annotations

from pathlib import Path


def sample_config(mode: str) -> str:
    if mode == "single":
        return _single()
    if mode == "multi":
        return _multi()
    raise ValueError(f"Unsupported sample mode: {mode}")


def _video_path(name: str) -> str:
    return str(Path("videos") / name)


def _single() -> str:
    return f"""ports:
  rtsp: 8554
  onvif_start: 8000
auth:
  enabled: false
  username: admin
  password: admin
devices:
  - id: camera-1
    uuid: 11111111-1111-4111-8111-111111111111
    hostname: camera-mock-1
    http_port: 8000
    manufacturer: Camera Mock
    model: Profile T H265 Mock
    serial: CM-0001
    firmware: 0.1.0
    profiles:
      - token: main
        name: Main 4K H265
        media_file: {_video_path("example.ts")}
        path: camera-1/main
      - token: sub
        name: Sub VGA H265
        media_file: {_video_path("example.ts")}
        path: camera-1/sub
"""


def _multi() -> str:
    return f"""ports:
  rtsp: 8554
  onvif_start: 8000
auth:
  enabled: false
devices:
  - id: camera-4k
    uuid: 22222222-2222-4222-8222-222222222222
    hostname: camera-mock-4k
    http_port: 8000
    manufacturer: Camera Mock
    model: Profile T H265 Mock
    serial: CM-4K
    firmware: 0.1.0
    profiles:
      - token: main
        name: Main 4K H265
        media_file: {_video_path("example.ts")}
        path: camera-4k/main
  - id: camera-vga
    uuid: 33333333-3333-4333-8333-333333333333
    hostname: camera-mock-vga
    http_port: 8001
    manufacturer: Camera Mock
    model: Profile T H265 Mock
    serial: CM-VGA
    firmware: 0.1.0
    profiles:
      - token: main
        name: Main VGA H265
        media_file: {_video_path("example.ts")}
        path: camera-vga/main
"""
