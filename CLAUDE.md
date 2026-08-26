# 项目说明（给 Claude 读的）

## 这个仓库是干什么的

定时从 Tailscale 控制面拉取 DERP 中继节点列表，生成 Surge 可以直接通过 URL 引用的
rule-set 文件。核心逻辑只有 `scripts/gen_derp_ruleset.py` 一个文件。

## Git 提交身份

本仓库的提交使用以下身份，会话开始时先执行：

```bash
git config user.name  "zeoer"
git config user.email "98957121+zeoer@users.noreply.github.com"
```

必须用这个 noreply 地址：GitHub 账号开启了 "Keep my email addresses private" 和
"Block command line pushes that expose my email"，用真实邮箱提交会被服务端拒绝推送。

## 改动时的注意事项

- **`rules/` 目录下的 `.list` 文件是自动生成的，不要手工编辑。** 要改输出内容，改脚本。
- 脚本**只用 Python 标准库**。请不要引入第三方依赖 —— 这样 workflow 不需要
  `setup-python` 和 `pip install`，跑得快也少一层供应链风险。
- 上游 DERP map 的字段（`Regions` / `RegionID` / `RegionCode` / `Nodes` / `HostName` /
  `IPv4` / `IPv6` / `STUNOnly`）都可能缺失或变形，`IPv4` 字段甚至允许填非 IP 的占位值。
  所有取值都要走 `_clean_ip()` 这类校验，**不要假设字段存在或格式正确**。
- **任何异常情况都必须让脚本非零退出，而不是写出一个空规则文件。** 空文件会让 Surge
  的分流规则整体失效，比"规则过期几天"严重得多。已有的失败路径见 `parse_nodes()`
  和 `main()` 里的 `RuntimeError`。
- 改完脚本用 fixture 验证，不要依赖真实网络：
  ```bash
  python3 scripts/gen_derp_ruleset.py --from-file tests/fixture.json --out-dir /tmp/out
  ```
  `tests/fixture.json` 里刻意放了缺字段、非法 IP、IPv4/IPv6 写反、回环地址、
  重复 IP、空区域、非对象节点等边界样例，改逻辑后应保证这些仍被正确处理。

## 输出格式

Surge 的外部 rule-set 文件里**不写策略**，策略在主配置的 `RULE-SET` 行指定。
所以这里只输出 `IP-CIDR,<ip>/32,no-resolve` 这样的规则体。
