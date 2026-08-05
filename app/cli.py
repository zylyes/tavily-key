#!/usr/bin/env python3
"""
Tavily API Key Pool CLI — batch add/list/remove/manage API keys.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from key_pool import KeyPool

pool = KeyPool()


def cmd_add(args):
    keys = args.keys
    if args.from_file:
        fpath = Path(args.from_file)
        if not fpath.exists():
            print(f"File not found: {args.from_file}")
            sys.exit(1)
        keys = [line.strip() for line in fpath.read_text().splitlines() if line.strip()]
    if not keys:
        print("No keys provided.")
        sys.exit(1)
    n = pool.add_keys_batch(keys)
    print(f"Added {n} key(s).")


def cmd_remove(args):
    pool.remove_key(args.key)
    print(f"Removed key: {args.key}")


def cmd_list(args):
    keys = pool.list_keys()
    if not keys:
        print("No keys in pool.")
        return
    status = "ACTIVE" if args.active else "ALL"
    print(f"=== API Key Pool ({status}) ===")
    for k in keys:
        if args.active and not k.is_active:
            continue
        state = "+" if k.is_active else "-"
        used = f"{k.request_count} reqs, {k.credits_used} credits"
        last = ""
        if k.last_used_at > 0:
            import datetime
            dt = datetime.datetime.fromtimestamp(k.last_used_at)
            last = f", last: {dt.strftime('%Y-%m-%d %H:%M')}"
        print(f"  {state} {k.masked} | {used}{last}")
        if k.last_error:
            print(f"      last error: {k.last_error[:100]}")
    active = sum(1 for k in keys if k.is_active)
    print(f"Total: {len(keys)} keys, {active} active")


def cmd_deactivate(args):
    pool.deactivate_key(args.masked, args.reason or "manual")
    print(f"Deactivated: {args.masked}")


def cmd_activate(args):
    pool.activate_key(args.masked)
    print(f"Activated: {args.masked}")


def cmd_stats(args):
    stats = pool.get_stats()
    import json
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def cmd_recent(args):
    logs = pool.get_recent_logs(args.limit)
    if not logs:
        print("No recent requests.")
        return
    print(f"=== Recent Requests (last {len(logs)}) ===")
    for log in logs:
        status = "OK" if log["success"] else "FAIL"
        import datetime
        dt = datetime.datetime.fromtimestamp(log["created_at"])
        err = f" | {log['error_msg'][:60]}" if log["error_msg"] else ""
        print(f"  [{dt.strftime('%m-%d %H:%M:%S')}] {status} {log['endpoint']} via {log['key_masked']} ({log['latency_ms']:.0f}ms){err}")


def cmd_health(args):
    print("Running health checks on all active keys...")
    results = pool.check_health_all()
    if not results:
        print("No active keys to check.")
        return
    for r in results:
        status = "ALIVE" if r["alive"] else "DEAD"
        extra = f" ({r['latency_ms']}ms)" if r.get("latency_ms") else f" [{r.get('error', '')}]"
        print(f"  {status} {r['masked']}{extra}")
    alive = sum(1 for r in results if r["alive"])
    dead = len(results) - alive
    print(f"Result: {alive} alive, {dead} dead")


def main():
    parser = argparse.ArgumentParser(prog="tavily-pool", description="Tavily API Key Pool Manager")
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Add API keys")
    p_add.add_argument("keys", nargs="*", help="API keys to add")
    p_add.add_argument("--from-file", "-f", help="File with one key per line")

    p_remove = sub.add_parser("remove", help="Remove a key")
    p_remove.add_argument("key", help="Masked key or full key to remove")

    p_list = sub.add_parser("list", help="List all keys")
    p_list.add_argument("--active", "-a", action="store_true", help="Show only active keys")

    p_deact = sub.add_parser("deactivate", help="Deactivate a key")
    p_deact.add_argument("masked", help="Masked key")
    p_deact.add_argument("--reason", "-r", help="Reason for deactivation")

    p_act = sub.add_parser("activate", help="Activate a key")
    p_act.add_argument("masked", help="Masked key")

    sub.add_parser("stats", help="Show pool stats")

    p_recent = sub.add_parser("recent", help="Show recent request logs")
    p_recent.add_argument("--limit", "-n", type=int, default=20, help="Number of recent logs")

    sub.add_parser("health", help="Probe all active keys, auto-deactivate dead ones")

    args = parser.parse_args()
    if args.cmd is None:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "add": cmd_add,
        "remove": cmd_remove,
        "list": cmd_list,
        "deactivate": cmd_deactivate,
        "activate": cmd_activate,
        "stats": cmd_stats,
        "recent": cmd_recent,
        "health": cmd_health,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
