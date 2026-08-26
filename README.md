# tailscale-derp-ruleset

定时抓取 Tailscale DERP 中继节点的 IP，生成 [Surge](https://nssurge.com/) 可以直接通过
URL 引用的 rule-set 文件。配好之后 Surge 每次更新规则都会拿到最新的节点列表，不需要你
手动维护。

- **数据源**：`https://controlplane.tailscale.com/derpmap/default`（Tailscale 官方控制面）
- **更新频率**：每天一次（UTC 04:17 / 台北时间 12:17），也可以在 Actions 页面手动触发
- **无变化不提交**：内容一样时不会产生空提交，仓库历史保持干净

## 在 Surge 里怎么用

在 Surge 配置的 `[Rule]` 段加一行，把 `DIRECT` 换成你想要的策略：

```ini
[Rule]
RULE-SET,https://raw.githubusercontent.com/zeoer/tailscale-derp-ruleset/main/rules/tailscale-derp.list,DIRECT
```

几点说明：

- **策略写在这一行，不在规则文件里。** Surge 的外部 rule-set 文件只包含规则本体，
  策略由引用它的这一行决定。所以同一个文件你可以在不同配置里指向不同策略。
- 规则里带了 `no-resolve`，Surge 不会为了匹配这条规则去做 DNS 解析，避免额外的解析
  开销和 DNS 泄漏。
- 这行的位置很重要：Surge 的规则是**从上往下第一个命中生效**，所以要放在会抢先命中的
  宽泛规则（比如 `GEOIP,CN` 或兜底的 `FINAL`）**之前**。
- Surge 对外部 rule-set 有本地缓存，改了远端文件后可以在 Surge 里手动「更新配置」或等
  它自己刷新。

## 首次启用步骤

1. 在 GitHub 上建一个**公开**仓库，名字 `tailscale-derp-ruleset`
2. 把本项目的文件全部传上去（保持目录结构）
3. 进仓库的 **Actions** 标签页，如果提示需要启用工作流就点启用
4. 左侧选 **Update DERP ruleset** → 右侧 **Run workflow** 手动跑一次
5. 跑完后 `rules/tailscale-derp.list` 就生成了，把上面那个 raw 链接填进 Surge

> 第 4 步不能省。仓库刚建好时 `rules/` 目录是空的，raw 链接会 404，必须先跑一次
> 让它生成文件。之后就全自动了。

## 目录结构

```
.
├── .github/
│   ├── workflows/update-ruleset.yml   定时任务
│   └── dependabot.yml                 只盯 Actions 的版本更新
├── scripts/gen_derp_ruleset.py        全部逻辑，纯标准库无依赖
├── tests/fixture.json                 边界样例，用于离线测试
├── rules/                             生成产物，勿手工编辑
└── CLAUDE.md                          给 AI 助手看的项目约定
```

## 可选开关

默认只输出 IP 规则。想要更多内容，编辑
`.github/workflows/update-ruleset.yml` 里的生成命令，加上对应参数：

| 参数 | 作用 | 什么时候用 |
|---|---|---|
| `--emit-domains` | 额外输出 `DOMAIN,derp1a.tailscale.com` 这类域名规则 | 想让 Surge 在 DNS 阶段就命中，比等连接建立更早。代价是文件长度翻倍 |
| `--split-regions` | 额外生成 `tailscale-derp-asia.list`、`-us.list` 等分区文件 | 想做细分策略，比如亚洲节点直连、其他走代理 |
| `--include-stun-only` | 包含标记 `STUNOnly` 的节点 | 那些节点只做 NAT 穿透探测不做中继。纳入后规则更全，但也更长 |

例如三个全开：

```yaml
      - name: Generate ruleset
        run: |
          python3 scripts/gen_derp_ruleset.py \
            --out-dir rules \
            --basename tailscale-derp \
            --emit-domains \
            --split-regions \
            --include-stun-only
```

分区文件生成后，就能在 Surge 里分别引用：

```ini
RULE-SET,https://raw.githubusercontent.com/zeoer/tailscale-derp-ruleset/main/rules/tailscale-derp-asia.list,DIRECT
RULE-SET,https://raw.githubusercontent.com/zeoer/tailscale-derp-ruleset/main/rules/tailscale-derp.list,PROXY
```

## 本地测试

脚本不需要任何第三方依赖，Python 3.9+ 即可：

```bash
# 用离线样例跑，不碰网络
python3 scripts/gen_derp_ruleset.py --from-file tests/fixture.json --out-dir /tmp/out
cat /tmp/out/tailscale-derp.list

# 真实拉取
python3 scripts/gen_derp_ruleset.py --out-dir /tmp/out
```

`tests/fixture.json` 里刻意放了各种畸形输入：字段缺失、`IPv4` 填 `"none"`、
IPv4 和 IPv6 写反、回环地址、跨区域重复 IP、空区域、非对象节点。改逻辑后拿它跑一遍，
能挡掉大部分低级错误。

## 设计上的两个取舍

**为什么异常时宁可失败也不写空文件。** 如果上游返回异常或结构变了，脚本会非零退出并
保留上一版规则文件。理由是：一个空的 rule-set 会让 Surge 的这条分流规则完全失效，
流量默默走错策略，而且很难察觉；相比之下"规则停留在几天前的版本"影响小得多。所以
`parse_nodes()` 和 `main()` 里所有异常路径都是 `RuntimeError` + 退出码 1。

**为什么用 `github-actions[bot]` 提交而不是你的身份。** 一年三百多次机器提交挂在你名下，
会把贡献图刷成一片绿，反而看不出你真实的活动。Actions bot 的提交不计入贡献图，更干净。
如果你想改，把 workflow 里那两行 `git config` 换成你的 noreply 身份即可。

## 数据源参考

DERP map 的 JSON 结构对应 Tailscale 源码里的 `DERPMap` / `DERPRegion` / `DERPNode`：
[tailscale/tailcfg/derpmap.go](https://github.com/tailscale/tailscale/blob/main/tailcfg/derpmap.go)。
其中 `IPv4`、`IPv6`、`STUNOnly` 等字段都带 `omitempty`，可能整个不出现，脚本按这个前提
做了防御。
