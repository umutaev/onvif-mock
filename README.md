# Camera Mock

## Description

This is a simple mock implementation that supports selecting arbitrary number of streams and acts as an IP camera with ONVIF support.
It's designed to be used for testing and development purposes during NVR development.

I vibecoded this tool in one evening because I needed something to test my DIY NVR with. It has nothing to do with ONVIF conformance and should be used at your own risk.

## Requirements

- Python 3.14, managed by `uv`.
- `ffmpeg` on `PATH`.
- `mediamtx` on `PATH`, or pass `--mediamtx-bin`.

## Usage

Validate one of the bundled examples:

```bash
uv run camera-mock validate --config videos/example.yaml
```

Print the advertised ONVIF and RTSP endpoints:

```bash
uv run camera-mock endpoints --config videos/example.yaml --interface 127.0.0.1
```

Run the mock:

```bash
uv run camera-mock run --config videos/example.yaml --interface 127.0.0.1
```

The `--interface` value is used in all advertised ONVIF and RTSP URLs. Use a LAN IP address when another machine or NVR needs to discover and connect to the mock.

## Config

YAML configs can define one or more devices. Each device exposes one ONVIF HTTP endpoint and one or more Media2 profiles backed by H.264 or H.265 video files. Relative `media_file` paths are resolved from the current working directory, not from the config file directory. The v1 mock is open by default; enable `auth.enabled` to require WS-Security UsernameToken for ONVIF and username/password credentials in RTSP URLs.

Generate sample configs from the CLI:

```bash
uv run camera-mock sample-config --mode single
uv run camera-mock sample-config --mode multi
```

## ONVIF Scope

This is a pragmatic Profile T-oriented mock for development, not an ONVIF conformance-certified camera. It implements WS-Discovery, selected Device service operations, Media profile listing, H.264/H.265 stream URI retrieval, and no-op setter responses with logs. Unsupported SOAP actions return standards-shaped SOAP faults.
