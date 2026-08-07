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


def cmd_usage(args):
    """从 Tavily 官方 /usage 同步并展示每个 key 的 billing cycle 用量。"""
    import json as _json

    if args.sync:
        print("Syncing official usage from Tavily...")
        results = pool.sync_usage()
        ok = sum(1 for r in results if r.get("ok"))
        print(f"Synced {ok}/{len(results)} key(s).")
        if args.json:
            print(_json.dumps(results, ensure_ascii=False, indent=2))
        for r in results:
            if r.get("ok"):
                plan = r.get("plan") or ""
                print(f"  {r['masked']}: {r['usage']}/{r['limit']} credits"
                      f" (search={r.get('search_usage',0)} extract={r.get('extract_usage',0)}"
                      f" crawl={r.get('crawl_usage',0)} map={r.get('map_usage',0)}"
                      f" research={r.get('research_usage',0)}) plan={plan}"
                      + (" [recovered]" if r.get("recovered") else ""))
            else:
                print(f"  {r['masked']}: FAILED - {r.get('error', '')}")
        return
    # 只展示本地已同步数据
    agg = pool.get_aggregate()
    print(f"Aggregate: {agg['active_keys']} active / {agg['total_keys']} total,"
          f" used {agg['total_used']} / {agg['total_limit']} credits, remaining {agg['remaining']}")
    for k in pool.list_keys():
        if k.credits_limit > 0:
            print(f"  {k.masked}: {k.credits_used}/{k.credits_limit}"
                  f" ({k.usage_pct:.1f}%) plan={k.plan or '-'}"
                  + (" [exhausted]" if k.is_exhausted else ""))
        else:
            print(f"  {k.masked}: no official data (run with --sync)")


def cmd_audit(args):
    """列出结合本地记录与官方用量的异常 key，附请求来源与 Research 任务概览。"""
    anomalies = pool.detect_anomalies()
    if not anomalies:
        print("No anomalies detected. All keys look healthy.")
    else:
        print(f"Found {len(anomalies)} anomalous key(s):")
        for a in anomalies:
            flags = ",".join(a["flags"])
            print(f"  {a['masked']} [{flags}]")
            for reason in a["reasons"]:
                print(f"      - {reason}")
    # 近 24h 请求概览（按来源拆分）
    try:
        trend = pool.get_usage_trend(1)
        points = trend.get("points") or []
        day = points[-1] if points else {}
        print(f"\n近24h请求: {day.get('requests', 0)}"
              f"（{day.get('success', 0)} 成功 / {day.get('failed', 0)} 失败）")
        endpoints = day.get("endpoints") or {}
        if endpoints:
            print("  按接口: " + ", ".join(f"{k}={v}" for k, v in sorted(endpoints.items())))
        src = {}
        for s in ("mcp", "proxy", "cli"):
            t = pool.get_usage_trend(1, source=s)
            p = (t.get("points") or [])
            src[s] = (p[-1].get("requests", 0) if p else 0)
        print("  按来源: " + ", ".join(f"{k}={v}" for k, v in src.items()))
        # 按项目拆分（request_log.project_id：MCP 请求的 mcp_project_id 归属）
        proj: dict[str, int] = {}
        rows, _ = pool.query_logs(limit=1000)
        for r in rows:
            p = (r.get("project_id") or "").strip()
            if p:
                proj[p] = proj.get(p, 0) + 1
        if proj:
            print("  按项目: " + ", ".join(f"{k}={v}" for k, v in sorted(proj.items())))
    except Exception:  # noqa: BLE001
        pass
    # Research 任务看板概览（仅统计，不逐个调官方接口）
    try:
        from mcp_server import list_research_tasks
        tasks = list_research_tasks(limit=50)
        running = sum(1 for t in tasks if (t.get("status") or "").lower() in ("pending", "processing", "running", "queued"))
        done = sum(1 for t in tasks if (t.get("status") or "").lower() in ("completed",))
        failed = sum(1 for t in tasks if (t.get("status") or "").lower() in ("failed", "error", "cancelled"))
        print(f"Research 任务: 共 {len(tasks)}（进行中 {running} / 完成 {done} / 失败 {failed}）")
    except Exception:  # noqa: BLE001
        pass


def cmd_proxy(args):
    """展示搜索代理状态 / 地址 / 密钥（启停走面板，此处仅展示）。"""
    from proxy_manager import status as proxy_status
    from settings import get_settings, proxy_urls

    cfg = get_settings()
    st = proxy_status()
    if st["running"]:
        print(f"运行状态 : 运行中 (PID {st['pid']})")
    else:
        print("运行状态 : 已停止")
    print(f"监听     : {st['host']}:{st['port']}")
    print(f"API 地址 : {proxy_urls(cfg).get('local')}")
    token = (cfg.get("proxy_token") or "").strip()
    print(f"代理密钥 : {token or '（未设置，对外开放）'}")
    print(f"随软件启动: {'是' if cfg.get('proxy_auto_start') else '否'}")
    print()
    print("对接方式：把 API 地址填入客户端「API 地址」、代理密钥填入「API 密钥」")
    print("（如 Cherry Studio 网络搜索 → Tavily 提供商，填后点「检测」验证）。")


def cmd_backup(args):
    """备份 data/ 关键文件为 zip。"""
    from backup import backup_to

    dest = backup_to(args.path)
    print(f"备份完成: {dest}")
    print("备份内容：config.json、tavily_keys.db(+wal)、research_keys.json、")
    print("research_tasks_cache.json，及存在时的 .tavily-secret.key（Fernet 加密密钥）。")


def cmd_restore(args):
    """从备份 zip 恢复 data/。建议先停止 MCP/搜索代理/面板，避免文件占用。"""
    from backup import restore_from

    try:
        n = restore_from(args.zip)
    except Exception as e:  # noqa: BLE001
        print(f"恢复失败: {e}")
        return
    print(f"已恢复 {n} 个文件。请重启服务使配置与 Key 生效。")


def cmd_update(args):
    """检查 GitHub 最新 release，与本地版本对比（--force 强制刷新网络）。"""
    import datetime

    from updater import check_update

    result = check_update(force=args.force)
    print(f"当前版本 : {result['current_version']}")
    if result.get("disabled"):
        print("更新检查 : 已禁用（settings.update_repo 未配置）")
        return
    if not result.get("ok"):
        print(f"更新检查 : 失败 - {result.get('error', '')}")
        return
    latest = result.get("latest_version") or ""
    print(f"最新版本 : {latest}")
    published = result.get("published_at") or ""
    if published:
        try:
            dt = datetime.datetime.fromisoformat(published.replace("Z", "+00:00"))
            print(f"发布时间 : {dt.astimezone().strftime('%Y-%m-%d %H:%M')}")
        except Exception:  # noqa: BLE001
            pass
    if result.get("update_available"):
        print("状态     : ⬆ 发现新版本，可前往 GitHub 查看更新")
    else:
        print("状态     : ✅ 已是最新版本")
    if result.get("release_url"):
        print(f"地址     : {result['release_url']}")
    body = result.get("body") or ""
    if body and args.notes:
        print("\n更新说明：")
        print(body[:2000])


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

    p_usage = sub.add_parser("usage", help="Show/sync official Tavily usage")
    p_usage.add_argument("--sync", action="store_true", help="Sync from official /usage endpoint")
    p_usage.add_argument("--json", action="store_true", help="Print raw JSON results")

    sub.add_parser("audit", help="List anomalous keys (local records + official usage)")

    sub.add_parser("proxy", help="Show search proxy status/URL/key (Tavily-compatible REST)")

    p_backup = sub.add_parser("backup", help="Backup data directory to a zip file")
    p_backup.add_argument("path", nargs="?",
                          help="Target directory or .zip path (default: system temp dir)")

    p_restore = sub.add_parser("restore", help="Restore data directory from a backup zip")
    p_restore.add_argument("zip", help="Backup zip file path")

    p_update = sub.add_parser("update-check", help="Check for updates on GitHub")
    p_update.add_argument("--force", "-f", action="store_true",
                          help="Force re-check (bypass cache)")
    p_update.add_argument("--notes", action="store_true",
                          help="Print release notes of the latest version")

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
        "usage": cmd_usage,
        "audit": cmd_audit,
        "proxy": cmd_proxy,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "update-check": cmd_update,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
