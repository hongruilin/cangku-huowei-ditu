# 仓库货位地图（开源 WMS）

![version](https://img.shields.io/badge/version-v2.4.0-blue) ![license](https://img.shields.io/badge/license-MIT-green)

免费开源的仓库管理系统，适合小仓库、电商仓、工厂库房：自己画仓库地图，每个货位按层管理货物，一眼看到每层的名称、数量、状态和照片；手机和电脑都能用，Docker 一条命令部署。

- **仓库地图**：按楼层画格子地图，可划分区域，放大缩小、平移查看
- **货位分层**：每个货位多层独立管理（名称、数量、状态、照片、备注），货架视图一眼看全
- **批量操作**：多选货位批量移动/复制，相对位置不变
- **备份恢复**：一键导出 ZIP 备份包（数据 + 图片），导入即完整还原；图片支持服务器本地存储与外链
- **MCP 接口**：AI 客户端（如 Minis、Claude 等）可直接查货、改库存、批量移货、看照片找货

---

## 最新更新

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

浏览器打开：`http://服务器IP:8000`

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
