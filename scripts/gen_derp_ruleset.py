#!/usr/bin/env python3
"""
从 Tailscale 控制面拉取 DERP map，生成 Surge 可直接引用的 rule-set 文件。

数据源结构参考 tailscale/tailcfg/derpmap.go：

    {
      "Regions": {
        "1": {
          "RegionID": 1,
          "RegionCode": "nyc",
          "RegionName": "New York City",
          "Nodes": [
            {"Name": "1a", "RegionID": 1, "HostName": "derp1a.tailscale.com",
             "IPv4": "199.38.181.104", "IPv6": "2607:f740:e04a::a"}
          ]
        }
      }
    }

注意 IPv4 / IPv6 / STUNOnly 等字段都带 omitempty，可能整个缺失；
IPv4 字段在 Tailscale 的约定里也允许填非 IP 的占位值（表示"走 DNS 解析"或"禁用"），
所以一律用 ipaddress 模块校验后才写入，解析不出来的直接跳过。

用法：
    python3 scripts/gen_derp_ruleset.py --out-dir rules
    python3 scripts/gen_derp_ruleset.py --out-dir rules --emit-domains --split-regions
    python3 scripts/gen_derp_ruleset.py --from-file tests/fixture.json --out-dir /tmp/x
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DERP_MAP_URL = "https://controlplane.tailscale.com/derpmap/default"
USER_AGENT = "tailscale-derp-ruleset/1.0 (+https://github.com/zeoer/tailscale-derp-ruleset)"
TIMEOUT = 30

# 区域归类：把 RegionCode 前缀映射到大区，仅在 --split-regions 时使用。
# Tailscale 的 RegionCode 是机场码风格（nyc/sfo/fra/sin/tok/hkg/...），
# 这里只做粗粒度归类，认不出来的进 other。
REGION_GROUPS: dict[str, set[str]] = {
    "asia": {
        "tok", "nrt", "hnd", "osa", "kix", "sin", "hkg", "tpe", "sel", "icn",
        "bom", "del", "maa", "blr", "bkk", "kul", "cgk", "jnb", "dxb", "tlv",
        "hkt", "mnl", "han", "sgn",
    },
    "us": {
        "nyc", "sfo", "lax", "sea", "chi", "dfw", "den", "ord", "iad", "atl",
        "mia", "phx", "hnl", "slc", "bos", "las",
    },
    "eu": {
        "fra", "ams", "lhr", "lon", "par", "mad", "waw", "sto", "hel", "dub",
        "mil", "zrh", "vie", "cph", "osl", "prg", "bru", "lis",
    },
    "oceania": {"syd", "mel", "akl", "per", "bne"},
    "sa": {"gru", "sao", "scl", "eze", "bog", "lim", "mex"},
    "ca": {"yyz", "yvr", "yul", "ywg"},
}

HEADER_MARK = "# ---- generated below, do not edit by hand ----"


class DerpNode:
    __slots__ = ("name", "hostname", "region_id", "region_code", "region_name",
                 "ipv4", "ipv6", "stun_only")

    def __init__(self, name, hostname, region_id, region_code, region_name,
                 ipv4, ipv6, stun_only):
        self.name = name
        self.hostname = hostname
        self.region_id = region_id
        self.region_code = region_code
        self.region_name = region_name
        self.ipv4 = ipv4
        self.ipv6 = ipv6
        self.stun_only = stun_only


def fetch_derp_map(url: str = DERP_MAP_URL) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                raise RuntimeError(f"DERP map 返回 HTTP {resp.status}")
            raw = resp.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"拉取 DERP map 失败: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DERP map 不是合法 JSON: {exc}") from exc


def _clean_ip(value, want_version: int) -> str | None:
    """校验并规范化一个 IP 字符串；不合法或版本不符则返回 None。"""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() in {"none", "null", "-"}:
        return None
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if addr.version != want_version:
        return None
    # 排除明显不该出现在公网规则里的地址
    if addr.is_loopback or addr.is_unspecified or addr.is_link_local or addr.is_multicast:
        return None
    return str(addr)


def parse_nodes(derp_map: dict) -> list[DerpNode]:
    regions = derp_map.get("Regions")
    if not isinstance(regions, dict):
        raise RuntimeError("DERP map 缺少 Regions 字段，结构可能已变更")

    nodes: list[DerpNode] = []
    for region_key, region in regions.items():
        if not isinstance(region, dict):
            continue
        region_id = region.get("RegionID")
        if not isinstance(region_id, int):
            try:
                region_id = int(region_key)
            except (TypeError, ValueError):
                region_id = -1
        region_code = str(region.get("RegionCode") or "").strip().lower()
        region_name = str(region.get("RegionName") or "").strip()

        raw_nodes = region.get("Nodes")
        if not isinstance(raw_nodes, list):
            continue

        for raw in raw_nodes:
            if not isinstance(raw, dict):
                continue
            hostname = str(raw.get("HostName") or "").strip()
            nodes.append(DerpNode(
                name=str(raw.get("Name") or "").strip(),
                hostname=hostname,
                region_id=region_id,
                region_code=region_code,
                region_name=region_name,
                ipv4=_clean_ip(raw.get("IPv4"), 4),
                ipv6=_clean_ip(raw.get("IPv6"), 6),
                stun_only=bool(raw.get("STUNOnly", False)),
            ))

    if not nodes:
        raise RuntimeError("DERP map 里没有解析出任何节点，拒绝生成空规则")
    return nodes


def region_group(code: str) -> str:
    for group, codes in REGION_GROUPS.items():
        if code in codes:
            return group
    return "other"


_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def build_rules(nodes: list[DerpNode], emit_domains: bool) -> list[str]:
    """把节点列表转成排序去重后的 Surge 规则行。"""
    v4: set[str] = set()
    v6: set[str] = set()
    domains: set[str] = set()

    for node in nodes:
        if node.ipv4:
            v4.add(node.ipv4)
        if node.ipv6:
            v6.add(node.ipv6)
        if emit_domains and node.hostname:
            host = node.hostname.lower().rstrip(".")
            if _HOSTNAME_RE.match(host):
                domains.add(host)

    lines: list[str] = []
    for ip in sorted(v4, key=lambda s: ipaddress.IPv4Address(s)):
        lines.append(f"IP-CIDR,{ip}/32,no-resolve")
    for ip in sorted(v6, key=lambda s: ipaddress.IPv6Address(s)):
        lines.append(f"IP-CIDR6,{ip}/128,no-resolve")
    for host in sorted(domains):
        lines.append(f"DOMAIN,{host}")
    return lines


def render(lines: list[str], title: str, node_count: int, source: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    head = [
        f"# {title}",
        f"# Source: {source}",
        f"# Nodes: {node_count}  Rules: {len(lines)}",
        f"# Updated: {stamp}",
        "#",
        "# 自动生成，请勿手工编辑。修改逻辑见 scripts/gen_derp_ruleset.py",
        HEADER_MARK,
    ]
    return "\n".join(head + lines) + "\n"


def body_of(text: str) -> str:
    """取出 HEADER_MARK 之后的部分，用于比较内容是否真的变了（忽略时间戳）。"""
    idx = text.find(HEADER_MARK)
    return text[idx + len(HEADER_MARK):].strip() if idx >= 0 else text.strip()


def write_if_changed(path: Path, content: str) -> bool:
    """内容（忽略头部时间戳）有变化才写盘。返回是否写入。"""
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if body_of(old) == body_of(content):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 Tailscale DERP 的 Surge rule-set")
    ap.add_argument("--out-dir", default="rules", help="输出目录（默认 rules）")
    ap.add_argument("--basename", default="tailscale-derp",
                    help="主文件名，不含扩展名（默认 tailscale-derp）")
    ap.add_argument("--url", default=DERP_MAP_URL, help="DERP map 地址")
    ap.add_argument("--from-file", help="改为从本地 JSON 读取，用于测试")
    ap.add_argument("--emit-domains", action="store_true",
                    help="额外输出 DERP 主机名的 DOMAIN 规则")
    ap.add_argument("--split-regions", action="store_true",
                    help="额外按大区生成 <basename>-<group>.list")
    ap.add_argument("--include-stun-only", action="store_true",
                    help="包含标记为 STUNOnly 的节点（默认过滤掉）")
    args = ap.parse_args(argv)

    if args.from_file:
        source = f"file://{args.from_file}"
        derp_map = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    else:
        source = args.url
        derp_map = fetch_derp_map(args.url)

    nodes = parse_nodes(derp_map)
    total_parsed = len(nodes)

    if not args.include_stun_only:
        nodes = [n for n in nodes if not n.stun_only]
    usable = [n for n in nodes if n.ipv4 or n.ipv6]

    if not usable:
        raise RuntimeError("过滤后没有任何带 IP 的节点，拒绝生成空规则")

    out_dir = Path(args.out_dir)
    written: list[str] = []

    lines = build_rules(usable, args.emit_domains)
    main_path = out_dir / f"{args.basename}.list"
    if write_if_changed(main_path, render(
            lines, "Tailscale DERP relay nodes (Surge rule-set)", len(usable), source)):
        written.append(str(main_path))

    if args.split_regions:
        buckets: dict[str, list[DerpNode]] = {}
        for node in usable:
            buckets.setdefault(region_group(node.region_code), []).append(node)
        for group, group_nodes in sorted(buckets.items()):
            group_lines = build_rules(group_nodes, args.emit_domains)
            if not group_lines:
                continue
            path = out_dir / f"{args.basename}-{group}.list"
            if write_if_changed(path, render(
                    group_lines, f"Tailscale DERP relay nodes — {group} (Surge rule-set)",
                    len(group_nodes), source)):
                written.append(str(path))

    print(f"解析节点 {total_parsed} 个，过滤后可用 {len(usable)} 个，主规则 {len(lines)} 条")
    if written:
        print("已更新：" + ", ".join(written))
    else:
        print("内容无变化，未写入任何文件")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)
