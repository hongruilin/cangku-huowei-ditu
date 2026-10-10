# Mac 上 Caddy 开机自启（launchd）

背景：2026-10-09 仓库地图 IPv6 外网访问失败，根因是 Caddy 没启动（手动 `caddy run`，重启即掉）。

## 安装（在 Mac 上执行一次）

1. 把 `com.user.caddy.plist` 里的两处路径改对：
   - `/opt/homebrew/bin/caddy`：以 `which caddy` 的实际输出为准（Intel Mac 可能是 `/usr/local/bin/caddy`）
   - `/Users/USER_HOME/Caddyfile`：换成真实家目录，如 `/Users/你的用户名/Caddyfile`
2. 复制到 LaunchAgents 并加载：

```bash
cp com.user.caddy.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.caddy.plist
```

3. 验证：

```bash
curl -g -6 "http://[你的IPv6地址]:8000" -I   # 应返回仓库地图登录页响应
launchctl list | grep caddy                    # 有 PID 即在跑
```

## 日常

- 改完 `~/Caddyfile` 后热重载（不用重启服务）：`caddy reload --config ~/Caddyfile`
- 看日志：`tail -f /tmp/caddy.log`
- 停用：`launchctl unload ~/Library/LaunchAgents/com.user.caddy.plist`

## 注意

- `RunAtLoad + KeepAlive`：登录即启动、崩溃自动拉起，Mac 重启后不再需要手动开 Caddy。
- 若 8000/8001 端口被 Docker 旧绑定占用，先在仓库目录 `docker compose up -d`（v2.7.3 起默认只绑 127.0.0.1），再让 Caddy 接管公网口。
