# 更新日志

## [2.0.2] - 2026-10-09
### 说明
- 基于用户实际在用的版本（楼层/区域同步修复版）重建，废弃 2.0.1 及之前两版的临时方案
- 版本号机制：VERSION 文件 + CHANGELOG.md + 后端 `/api/version` + 前端标题栏显示

## [2.0.1] - 2026-10-09（已废弃）
- 图片显示改用本地 blob 缓存 hack，用户要求废弃

## [2.0.0] - 2026-10-08
### 重构
- 后端从 Node.js + Express 重写为 Python + FastAPI（旧版废弃）
- 登录从 JWT 改为 Starlette SessionMiddleware（签名 cookie，解决刷新掉登录）
- 密码哈希从 passlib 换为 bcrypt 原生（解决 passlib/bcrypt 4.x 兼容性崩溃）
- MCP 从 Node 版重写为 Python 版，新增 HTTP/SSE 模式（`mcp/http_server.py`，:8001）
- Docker：`docker-compose.yml` 新增 `mcp` 服务
### 修复
- 楼层/区域增删改实时同步到服务器
- 后端新增 `PUT /api/zones/:id`（区域改名）
- pydantic 锁 2.9.2（解决 mcp 1.9.0 的 `eval_type_backport` 导入失败）

## [2.0.3] - 2026-10-09
### 修复
- MCP HTTP/SSE 模式无法连接：`http_server.py` 的 `from mcp.server import server` 误导入 pip 的 mcp 包而非本地 `mcp/server.py`；改用 importlib 按文件路径加载

## [2.0.4] - 2026-10-09
### 新增
- MCP 新增 Streamable HTTP 协议端点 `/mcp`（GET/POST/DELETE），兼容只支持新版协议的客户端（如 Minis）；旧版 SSE `/mcp/sse` 保留

## [2.0.5] - 2026-10-09
### 修复
- MCP `/mcp` 端点 500 错误：改用 `StreamableHTTPSessionManager`（带 lifespan）替代裸 `StreamableHTTPServerTransport`

## [2.0.6] - 2026-10-09
### 修复
- JSON 导入不持久化：导入只改本地、服务器未更新导致刷新回退。新增 `POST /api/import` 全量导入端点，前端在线时导入后从服务器重载
### 决策
- 图片方案定为 A：本地文件存储（`/uploads/`），不做对象存储抽象层

## [2.0.7] - 2026-10-09
### 修复
- 导入被服务器拒绝：状态同步把字符串 key（如 "empty"）误写入整数主键 id 列导致 500；改走 key 列
- 前端导入错误提示拆分：文件格式错 / 服务器拒绝分开显示

## [2.0.8] - 2026-10-09
### 修复
- `/api/import` 照模型重写并通过 TestClient 端到端验证：① 以前 `wh.floors` 关系不存在直接 500；② Zone 坐标按 x1/y1 写错列（应为 r0/c0）；③ Slot 用 cx/cy（应为 r/c）；④ Level 用 idx/status_id（应为 level_index/status_key）；⑤ Floor 多传 pz 字段；⑥ slot key 按 "c,r" 解析但前端实际是 "r,c"，行列会互换；⑦ 删旧数据改显式逐层删除，不依赖数据库级联

## [2.0.9] - 2026-10-09
### 修复
- 导入区域不恢复：前端区域坐标是 r0/c0/r1/c1，导入端点却读 x1/y1 全部落 0，区域缩成原点。改为直接读 r0/c0/r1/c1；已用前端真实格式端到端验证（区域坐标/名称、槽位、图片路径、重复导入替换均正确）

## [2.1.0] - 2026-10-09
### 新增
- 每层可选备注：编辑窗口加备注框（每层独立），货架视图显示备注，层标签带 📝 标记；后端 Level.note（老库启动自动补列）；MCP 的 get_slot / get_floor_map / search_goods 返回备注，update_level 可改备注
- 库位移动/复制：编辑窗口加「移动库位」「复制库位」，点选目标格完成（目标已占用会先确认）；MCP 新增 wms_move_slot / wms_copy_slot（默认不覆盖，overwrite=true 才覆盖）
- 编辑库位窗口可拖动：拖把手或标题栏上下调高，下滑到底自动关闭
### 修复
- 删除页面残留的旧「仓库地图原型 v0.14」标题栏（与新标题重复、stats id 重复）
- save_map 删旧库位改显式删层级，不再单靠数据库级联
- MCP update_level 对新层级会丢名称/数量的问题（UPSERT 改为带值插入）

## [2.2.0] - 2026-10-09
### 新增
- 批量移动/复制（工具栏）：点「批量移动/批量复制」→ 点选多个库位（高亮）→ 点锚点整体平移，相对位置保持；冲突先确认，越界拦截
- 编辑窗口可拖动停靠：拖把手或标题栏任意移动，松手自动贴最近的边（上/下/左/右），位置会记住；左右停靠时背景不挡地图
- 备份带图：导出改为备份包（本地图片 base64 嵌入，外链图片保留 URL），恢复时图片文件一并写回并提示张数；图片路径带安全校验（防目录穿越、格式/大小）
- 图片智能区分本地路径与 URL：/uploads/... 存服务器文件，http(s) 外链原样保存、直接显示（map/导入/MCP 全链路）
### 变更
- 移动/复制从编辑窗口内移到工具栏（支持批量）；编辑窗口内的单格移动/复制及拖拽调高移除，改为拖动停靠
### MCP
- 新增 wms_move_slots / wms_copy_slots 批量工具（成对映射、单事务、连环移动/复制安全、覆盖保护）
- wms_update_level 可设 img（本地路径/外链/清空）；get_slot、get_floor_map、search_goods 返回 img

## [2.3.0] - 2026-10-09
### 新增
- ZIP 备份包（替代 base64 JSON 当主力）：导出为 `wms-backup-v2.3.0-时间.zip`，内含 backup.json（数据）+ images/（图片原文件）+ meta.json；恢复时把 ZIP 发服务器解压还原。图片不再 base64 膨胀（约省 1/3），图片再多文件也只是正常图片大小，浏览器只管上传/下载、不读进内存
### 说明
- 旧的 JSON 备份（含 base64 图片）仍可导入，export-package 端点保留兼容；图片较少时两种都行，多图请用 ZIP
- 恢复安全：ZIP 内只接受 images/ 下的合法图片路径，单张 ≤15MB、总量 ≤500MB、下载包 ≤600MB，目录穿越成员会被拒绝

## [2.3.1] - 2026-10-09
### 修复
- 删除操作提示里过时的文案（原写"数据存在 localStorage、导出 JSON 接后端"，与现在的服务器同步/ZIP 备份不符）
