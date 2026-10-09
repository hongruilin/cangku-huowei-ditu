# 仓库货位地图（开源 WMS）

![version](https://img.shields.io/badge/version-v2.7.3-blue) ![license](https://img.shields.io/badge/license-MIT-green)

免费开源的仓库管理系统，适合小仓库、电商仓、工厂库房：自己画仓库地图，每个货位按层管理货物，一眼看到每层的名称、数量、状态和照片；手机和电脑都能用，Docker 一条命令部署。

- **仓库地图**：按楼层画格子地图，可划分区域，放大缩小、平移查看
- **货位分层**：每个货位多层独立管理（名称、数量、状态、照片、备注），货架视图一眼看全
- **批量操作**：多选货位批量移动/复制，相对位置不变
- **备份恢复**：一键导出 ZIP 备份包（数据 + 图片），导入即完整还原；图片支持服务器本地存储与外链
- **MCP 接口**：AI 客户端（如 Minis、Claude 等）可直接查货、改库存、批量移货、看照片找货

---

## 最新更新

**v2.7.3（2026-10-09）— 端口默认只绑本机**
- Docker 端口默认绑 127.0.0.1，对外走 Caddy 等反向代理（Mac + IPv6 场景更顺）；直接暴露设 `WMS_BIND=0.0.0.0`

**v2.7.2（2026-10-09）— 部署支持反向代理前置**
- compose 新增 `WMS_BIND`：可把端口只绑在 127.0.0.1，再用 Caddy 双栈对外（解决 Mac Docker Desktop 下 IPv6 进不来的问题）

**v2.7.1（2026-10-09）— 计数方向纠正**
- 组内库位从下往上数（第一排是最下面一排），与「第1层是最底层」统一

**v2.7.0（2026-10-09）— AI 报位置带组内序号**
- 位置精确到「第几个库位」：如「1F · 1号货架第2个库位第3层（A-03）」，从左往右、从下往上数（第一排是最下面一排）

**v2.6.0（2026-10-09）— 问 AI 东西在哪，它说人话位置**
- 搜索/详情结果带现成位置描述（如「1F · 1号货架第3层（A-03）」），AI 直接念，不再只报冷冰冰的库位编码
- MCP 服务器新增回答规范 instructions，所有客户端一连接就自动带着

**v2.5.0（2026-10-09）— 库位编组：多个库位组成一个置物架**
- 工具栏「库位编组」多选库位并命名：地图上有组名标签，货架视图卡片带编组徽章，移动/删除库位时编组自动维护
- MCP 新增编组管理工具（wms_list/create/update/delete_group，共 18 个工具），AI 可按"XX 架"定位与重组

**v2.4.0（2026-10-09）— MCP 看图：AI 能按图找货**
- 新增 `wms_view_images`：按楼层批量返回货物照片（带货位索引），AI 扫图比对即可定位实物在哪个货位
- 新增 `wms_view_image`：细看某库位某层的照片；照片自动压缩省 AI 上下文，本地图与外链图都支持

**v2.3.0（2026-10-09）** — 备份升级为 ZIP 备份包（数据 + 图片原文件），图片再多也不膨胀
**v2.2.0（2026-10-09）** — 批量移动/复制货位、编辑窗可拖动停靠、备份带图、图片分本地/外链

完整变更记录见 [CHANGELOG.md](CHANGELOG.md)。

> Python + FastAPI。部署跟着下面走，复制粘贴命令就行。

---

## 第一步：安装 Docker

**Mac / Windows**：下载 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，安装打开，等鲸鱼图标不转了。

**Linux**（Ubuntu/Debian）：

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# 重新登录一次
```

验证：

```bash
docker --version && docker compose version
```

---

## 第二步：启动

```bash
cd wms-server
docker compose up -d --build
```

等 30 秒，看日志：

```bash
docker compose logs app | tail -20
```

看到 `[wms] ready` 就是成功了。

---

## 第三步：使用

在跑服务的这台机器上打开：`http://127.0.0.1:8000`

> 出于安全，默认端口只绑本机。要从局域网/外网直接访问，二选一：在 `.env` 里写 `WMS_BIND=0.0.0.0` 重启（直接暴露），或保持默认，用 Caddy/Nginx 反向代理到 `127.0.0.1:8000` 对外（推荐）。

- 默认账号：`admin` / `admin123`
- 登录后右上角显示用户名，刷新不会掉登录（session cookie，7天有效）

> ⚠️ 正式使用前请改掉默认口令：管理员密码 `ADMIN_PASS`、数据库密码 `PGPASSWORD`、会话密钥 `SESSION_SECRET` 都可用环境变量覆盖（见 docker-compose.yml）。MCP 服务（8001）没有鉴权，只建议在内网使用，不要直接暴露到公网。

> 自定义密码启动：
> ```bash
> ADMIN_PASS=你的密码 docker compose up -d --build
> ```

---

## 常用命令

| 操作 | 命令 |
|------|------|
| 看状态 | `docker compose ps` |
| 看日志 | `docker compose logs -f app` |
| 重启 | `docker compose restart` |
| 停止（数据保留） | `docker compose down` |
| 换端口 | `PORT=9000 docker compose up -d` |
| 直接对外（不走反向代理） | `WMS_BIND=0.0.0.0 docker compose up -d`（默认只绑本机 127.0.0.1，推荐前置 Caddy/Nginx 再对外，Mac 下也能解决 IPv6 到不了 Docker 的问题） |

---

## API 文档

FastAPI 自带交互式文档，登录后访问：`http://服务器IP:8000/docs`

---

## MCP（给 AI 用）

```bash
# Docker 里跑 MCP（连 db 容器）：
docker compose exec app python mcp/server.py
```

MCP 客户端配置：

```json
{
  "mcpServers": {
    "wms": {
      "command": "docker",
      "args": ["compose", "exec", "-T", "app", "python", "mcp/server.py"],
      "cwd": "/你的路径/wms-server"
    }
  }
}
```

或本地直连数据库：

```json
{
  "mcpServers": {
    "wms": {
      "command": "python",
      "args": ["/你的路径/wms-server/mcp/server.py"],
      "env": {"DATABASE_URL": "postgresql://wms:wms@localhost:5432/wms"}
    }
  }
}
```

工具：查仓库/楼层/地图、货名搜索、库位详情、改层级货物、库存汇总、区域列表。

---

## 备份

页面里点**导出备份**下载 ZIP 包（含数据 + 图片原文件），换设备/重装后用**导入**选该 ZIP 即可完整恢复；图片少时用旧 JSON 备份也可以。命令行方式：

```bash
docker compose exec db pg_dump -U wms wms > backup.sql
# 图片在 ./uploads，直接复制
```

---

## 出问题？

```bash
docker compose ps              # 看容器状态
docker compose logs app | tail -30   # 看应用日志
docker compose logs db | tail -30    # 看数据库日志
```
