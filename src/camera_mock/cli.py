from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from camera_mock.config import ConfigError, load_config
from camera_mock.runtime import CameraMockRuntime, RuntimeErrorMessage, endpoints
from camera_mock.samples import sample_config


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _configure_logging(verbose=args.verbose)
    try:
        return args.func(args)
    except (ConfigError, RuntimeErrorMessage) as exc:
        logging.getLogger("camera_mock").warning("%s", exc)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="camera-mock", description="ONVIF Profile T H.265 camera mock.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logs.")
    subparsers = parser.add_subparsers(required=True)

    run = subparsers.add_parser("run", help="Run ONVIF, discovery, and RTSP mock services.")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--interface", required=True, dest="interface_host", help="Advertised interface/IP/host.")
    run.add_argument("--bind", default="0.0.0.0", help="HTTP bind address.")  # noqa: S104
    run.add_argument("--mediamtx-bin", default=None, help="Path to mediamtx binary.")
    run.add_argument("--ffmpeg-bin", default="ffmpeg", help="Path to ffmpeg binary.")
    run.set_defaults(func=_run)

    validate = subparsers.add_parser("validate", help="Validate config.")
    validate.add_argument("--config", required=True, type=Path)
    validate.set_defaults(func=_validate)

    ep = subparsers.add_parser("endpoints", help="Print advertised ONVIF and RTSP endpoints.")
    ep.add_argument("--config", required=True, type=Path)
    ep.add_argument("--interface", required=True, dest="interface_host", help="Advertised interface/IP/host.")
    ep.set_defaults(func=_endpoints)

    sample = subparsers.add_parser("sample-config", help="Print an example config.")
    sample.add_argument("--mode", choices=["single", "multi"], default="single")
    sample.set_defaults(func=_sample_config)
    return parser


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    runtime = CameraMockRuntime(
        config,
        interface_host=args.interface_host,
        mediamtx_bin=args.mediamtx_bin,
        ffmpeg_bin=args.ffmpeg_bin,
        bind_host=args.bind,
    )
    runtime.run_forever()
    return 0


def _validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    sys.stdout.write(f"valid: {len(config.devices)} device(s), {len(config.profiles)} profile(s)\n")
    return 0


def _endpoints(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    for line in endpoints(config, interface_host=args.interface_host):
        sys.stdout.write(f"{line}\n")
    return 0


def _sample_config(args: argparse.Namespace) -> int:
    sys.stdout.write(sample_config(args.mode))
    return 0


def _configure_logging(*, verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
    )
