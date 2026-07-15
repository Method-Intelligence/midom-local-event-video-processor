from __future__ import annotations

import argparse
import json
import socket
import sys
from collections import deque

from .capabilities import build_capabilities
from .config import config_path, delete_config, load_config, save_config
from .ffmpeg_probe import probe_ffmpeg
from .log import log, log_path, redact_token
from .midom_api import MidomApi, pair_worker
from .security import normalize_api_base_url
from .worker import LocalProcessorWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="midom-local-processor", description="Standalone Midom Event Video Processing worker.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pair = subparsers.add_parser("pair", help="Pair this local processor with a Midom project.")
    pair.add_argument("--url", required=True, help="Midom API base URL.")
    pair.add_argument("--code", required=True, help="Midom pairing code.")
    pair.add_argument("--name", default=socket.gethostname(), help="Machine/display name.")
    pair.add_argument("--allow-local-http", action="store_true", help="Allow HTTP for localhost development.")
    pair.add_argument("--allow-lan-http", action="store_true", help="Allow HTTP for private LAN/tailnet development.")

    start = subparsers.add_parser("start", help="Pair when needed, then run the worker loop.")
    start.add_argument("--url", help="Midom API base URL. Required when no local pairing exists.")
    start.add_argument("--code", help="Midom pairing code. Required when no local pairing exists.")
    start.add_argument("--name", default=socket.gethostname(), help="Machine/display name.")
    start.add_argument("--allow-local-http", action="store_true", help="Allow HTTP for localhost development.")
    start.add_argument("--allow-lan-http", action="store_true", help="Allow HTTP for private LAN/tailnet development.")

    subparsers.add_parser("run", help="Run the worker loop.")
    subparsers.add_parser("update-capabilities", help="Probe FFmpeg and update Midom worker capabilities.")
    subparsers.add_parser("status", help="Show local pairing and FFmpeg status.")
    subparsers.add_parser("show-config", help="Print redacted local config.")
    subparsers.add_parser("log-path", help="Print the persistent processor log path.")
    tail_log = subparsers.add_parser("tail-log", help="Print recent processor log lines.")
    tail_log.add_argument("--lines", type=int, default=80, help="Number of recent lines to print.")

    disconnect = subparsers.add_parser("disconnect", help="Disconnect this worker in Midom and remove local credentials.")
    disconnect.add_argument("--force-local", action="store_true", help="Remove local credentials even if remote disconnect fails.")

    return parser


def cmd_pair(args: argparse.Namespace) -> int:
    api_base_url = normalize_api_base_url(args.url, allow_local_http=args.allow_local_http, allow_lan_http=args.allow_lan_http)
    capabilities = build_capabilities()
    if not capabilities.get("media_processing"):
        log(f"Cannot pair because Event Video Processing capability is unavailable. FFmpeg probe: {probe_ffmpeg()}")
        return 2
    config = pair_worker(
        api_base_url,
        args.code,
        args.name,
        capabilities,
        allow_local_http=args.allow_local_http,
        allow_lan_http=args.allow_lan_http,
    )
    save_config(config)
    log(f"Paired local processor; worker_id={config.worker_id} project_id={config.project_id} config={config_path()}.")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    try:
        load_config()
    except FileNotFoundError:
        if (not args.url or not args.code) and sys.stdin.isatty():
            _prompt_for_first_run(args)
        if not args.url or not args.code:
            log("No local pairing exists. Start again with a Midom address and pairing code.")
            return 2
        pair_result = cmd_pair(args)
        if pair_result != 0:
            return pair_result
    return cmd_run(args)


def _prompt_for_first_run(args: argparse.Namespace) -> None:
    print("First-time Midom Local Event Video Processor setup.")
    print("Use the pairing code provided by your Midom project administrator.")
    default_url = args.url or "https://midombot.com"
    entered_url = input(f"Midom address [{default_url}]: ").strip()
    args.url = entered_url or default_url
    entered_code = input("Pairing code: ").strip()
    if entered_code:
        args.code = entered_code
    default_name = args.name or socket.gethostname()
    entered_name = input(f"Processor name [{default_name}]: ").strip()
    args.name = entered_name or default_name


def cmd_run(_args: argparse.Namespace) -> int:
    config = load_config()
    worker = LocalProcessorWorker(config)
    worker.run_forever()
    return 0


def cmd_update_capabilities(_args: argparse.Namespace) -> int:
    config = load_config()
    worker = LocalProcessorWorker(config)
    worker.update_capabilities()
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    try:
        config = load_config()
    except FileNotFoundError as exc:
        log(str(exc))
        return 1
    probe = probe_ffmpeg()
    log(
        "Local processor status: "
        f"worker_id={config.worker_id} project_id={config.project_id} org_id={config.org_id} "
        f"machine_name={config.machine_name!r}."
    )
    log(f"Stored worker token expires at: {config.token_expires_at or 'unknown/not reported by Midom'}.")
    log(f"Standalone processor config path: {config_path()}.")
    log(f"FFmpeg probe: {json.dumps(probe, sort_keys=True)}")
    return 0


def cmd_show_config(_args: argparse.Namespace) -> int:
    config = load_config()
    payload = {
        **config.__dict__,
        "worker_token": redact_token(config.worker_token),
        "config_path": str(config_path()),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_log_path(_args: argparse.Namespace) -> int:
    print(log_path())
    return 0


def cmd_tail_log(args: argparse.Namespace) -> int:
    path = log_path()
    if not path.exists():
        log(f"No processor log exists yet at {path}.")
        return 1
    line_count = max(1, min(1000, int(args.lines or 80)))
    lines: deque[str] = deque(maxlen=line_count)
    with path.open("r", encoding="utf-8", errors="replace") as reader:
        for line in reader:
            lines.append(line.rstrip("\n"))
    for line in lines:
        print(line)
    return 0


def cmd_disconnect(args: argparse.Namespace) -> int:
    config = load_config()
    api = MidomApi(config)
    try:
        api.disconnect()
        log(f"Remote disconnect accepted; worker_id={config.worker_id}.")
    except Exception as exc:
        log(f"Remote disconnect failed; worker may remain visible in Midom: {exc}")
        if not args.force_local:
            log("Local config was not removed. Use --force-local only if you intentionally want to discard local credentials.")
            return 2
    delete_config()
    log("Local processor credentials removed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "pair":
            return cmd_pair(args)
        if args.command == "start":
            return cmd_start(args)
        if args.command == "run":
            return cmd_run(args)
        if args.command == "update-capabilities":
            return cmd_update_capabilities(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "show-config":
            return cmd_show_config(args)
        if args.command == "log-path":
            return cmd_log_path(args)
        if args.command == "tail-log":
            return cmd_tail_log(args)
        if args.command == "disconnect":
            return cmd_disconnect(args)
    except KeyboardInterrupt:
        log("Interrupted.")
        return 130
    except Exception as exc:
        log(f"Error: {exc}", stream=sys.stderr)
        return 1
    parser.error(f"Unhandled command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
