# 单机 VPS Docker 部署手册

本文用于把记账系统（FastAPI 后端 + 后台前端）部署到一台干净的 Linux VPS。生产拓扑固定为：

- Caddy 对公网开放 80/443，自动申请和续期 HTTPS 证书。
- Caddy 同源托管后台前端构建产物（镜像内 `/srv/frontend`），SPA 路由回退到 `index.html`。
- `/api`、`/health`、`/docs*`、`/redoc`、`/openapi.json` 由 Caddy 反向代理到 app。
- app 只在 Compose 内部网络监听 `app:8000`，宿主机不发布 8000。
- SQLite 主库通过 `DATA_HOST_DIR` 绑定挂载，不保存在容器层。
- Caddy 的证书和运行状态分别保存在 `caddy_data`、`caddy_config` 命名卷。
- 前端源码随仓库检出，构建产物由 Dockerfile 的 Node 阶段在镜像构建时生成；服务器不需要安装 Node.js。

架构决策与边界见
[`docs/adr/0001-single-vps-deployment-and-token-auth.md`](adr/0001-single-vps-deployment-and-token-auth.md)。

## 1. 上线前人工检查

1. 在 LLM 厂商控制台设置消费上限和余额告警。
2. 为域名配置正确的 A/AAAA 记录。没有可用 IPv6 时不要保留错误的 AAAA 记录。
3. 先确认 SSH 管理端口可用并优先启用密钥登录，再调整防火墙。
4. 公网只开放业务需要的 80/443；可额外开放 UDP 443 以支持 HTTP/3。
5. 确认 VPS 没有向公网开放 app 的 8000 端口。

下面是 Ubuntu/Debian 系统的示意命令；生产环境建议按 Docker 官方仓库说明安装
Docker Engine 和 Compose 插件：

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 sqlite3 git curl
sudo systemctl enable --now docker
docker version
docker compose version
sqlite3 --version
```

不同发行版的 Compose 包名可能是 `docker-compose-plugin`。无论使用哪个包，都必须保证
`docker compose version` 可执行。

## 2. 准备目录和权限

示例约定：

```bash
export APP_DIR=/opt/bookkeeping
export DATA_HOST_DIR=/var/lib/bookkeeping/data
export BACKUP_HOST_DIR=/var/backups/bookkeeping

sudo install -d -m 0755 "$APP_DIR"
sudo install -d -m 0750 -o 10001 -g 10001 "$DATA_HOST_DIR"
sudo install -d -m 0750 -o "$USER" -g "$USER" "$BACKUP_HOST_DIR"
sudo chown "$USER":"$USER" "$APP_DIR"
```

镜像内 app 用户的 UID/GID 固定为 `10001:10001`，因此数据目录必须允许该用户写入。
备份目录由执行 cron 的宿主机用户写入。

将代码检出到 `$APP_DIR`：

```bash
git clone YOUR_REPOSITORY_URL "$APP_DIR"
cd "$APP_DIR"
cp .env.example .env
chmod 600 .env
```

## 3. 配置环境变量

编辑 `.env`，至少替换所有占位值：

```dotenv
DOMAIN=ledger.example.com
DATA_HOST_DIR=/var/lib/bookkeeping/data
BACKUP_HOST_DIR=/var/backups/bookkeeping

APP_API_TOKEN=手机快捷指令专用的高强度随机值
ADMIN_PASSWORD=12356
DATABASE_URL=sqlite:///./data/bookkeeping.db
DEFAULT_TIMEZONE=Asia/Hong_Kong

LLM_API_KEY=厂商密钥
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=实际使用的模型
LLM_TIMEOUT_SECONDS=30
```

`APP_API_TOKEN` 只给 iPhone 快捷指令；`ADMIN_PASSWORD` 是后台登录密码，默认 `12356`。旧的 `ADMIN_API_TOKEN` 不再使用。
`DATABASE_URL` 应保持指向容器内 `/app/data` 对应的相对路径；宿主机真实路径只放在
`DATA_HOST_DIR`。不要提交 `.env`，也不要把 Token 或 LLM Key 粘贴进日志和工单。

可用密码管理器生成 Token，或在安全终端执行：

```bash
openssl rand -hex 32
```

## 4. 首次启动与健康检查

先做不启动容器的配置校验，再构建和启动：

```bash
cd /opt/bookkeeping
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 app caddy
```

`docker compose up -d --build` 会构建两个镜像：`app`（FastAPI）和 `caddy`（Web 入口）。构建 `caddy` 时会先经过 Dockerfile 的 Node 阶段，在构建器内执行 `npm ci` 与 `npm run build` 生成 `frontend/dist`，再连同 `Caddyfile` 一起打进 `caddy:2-alpine` 基础镜像；前端构建失败会让整个构建失败，不会上线半成品。

验证 HTTP 自动跳转、HTTPS 和数据库探活：

```bash
curl -I "http://${DOMAIN}/health"
curl -fsS "https://${DOMAIN}/health"
```

验证前端托管与 SPA 回退（应返回 `index.html` 的内容）：

```bash
curl -fsS "https://${DOMAIN}/" | head -n 5
curl -fsS "https://${DOMAIN}/transactions" | head -n 5
```

打开 `https://${DOMAIN}/login`，输入 `.env` 中的 `ADMIN_PASSWORD` 应能进入总览页。

成功的健康响应为 `{"status":"ok"}`。下面的命令不应显示宿主机端口映射：

```bash
docker compose port app 8000
```

查看实时日志：

```bash
docker compose logs -f --tail=100 app
docker compose logs -f --tail=100 caddy
```

两个服务均使用 `json-file` 日志轮转，单文件上限 10 MiB，最多保留 3 个文件。

## 5. 更新与回滚

前端源码与后端同仓库，服务器 `git pull` 会一并拉取；前端产物由 Docker 多阶段构建生成，不需要手动上传构建产物。更新前先确认备份可用，然后拉取受信任版本并重建：

```bash
cd /opt/bookkeeping
git pull --ff-only
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl -fsS "https://${DOMAIN}/health"
```

回滚时检出已知可用的 tag 或 commit，再重建：

```bash
git checkout YOUR_KNOWN_GOOD_TAG_OR_COMMIT
docker compose up -d --build
curl -fsS "https://${DOMAIN}/health"
```

普通 `docker compose down` 不删除绑定挂载的 SQLite 数据和命名卷。不要执行
`docker compose down -v`：`-v` 会删除 Caddy 的证书与运行状态卷。也不要删除
`DATA_HOST_DIR`。

## 6. 修改手机 Token 或后台密码

手机 Token 和后台密码可以分别修改：

1. 准备新的手机 Token 或后台密码。
2. 修改服务器 `.env` 中对应的 `APP_API_TOKEN` 或 `ADMIN_PASSWORD`。
3. 强制重新创建 app：

   ```bash
   docker compose up -d --force-recreate app
   ```

4. 用旧 Token 请求其原权限接口，应返回 401；用新 Token 请求应成功。
5. 轮换 `APP_API_TOKEN` 时，更新 iPhone 快捷指令并执行一次真实记账请求。
6. 轮换 `ADMIN_PASSWORD` 时，更新后台的安全配置并验证列表、手工新增和统计；不得把新值写进前端源码或 `localStorage`。
7. 从密码管理器和安全变量中撤销旧 Token。

只运行 `docker compose restart app` 不会重新读取 `.env`；环境变量变更必须使用
`docker compose up -d --force-recreate app`。升级后后台使用 `ADMIN_PASSWORD`，未配置时默认 `12356`。
原来的 `ADMIN_API_TOKEN` 不再生效；无需修改数据库或手机 Token。

## 7. 每日 SQLite 备份与保留策略

先手动执行一次一致性备份：

```bash
sqlite3 "$DATA_HOST_DIR/bookkeeping.db" \
  ".backup '$BACKUP_HOST_DIR/bookkeeping-$(date +%F-%H%M%S).db'"
```

用 `crontab -e` 添加每日 02:30 任务。cron 会把未转义的 `%` 当作换行，因此日期格式中的
`%` 必须写成 `\%`：

```cron
30 2 * * * /usr/bin/sqlite3 /var/lib/bookkeeping/data/bookkeeping.db ".backup '/var/backups/bookkeeping/bookkeeping-$(/usr/bin/date +\%F).db'" && /usr/bin/find /var/backups/bookkeeping -type f -name 'bookkeeping-*.db' -mtime +30 -delete
```

确认任务和最新备份：

```bash
crontab -l
ls -lh "$BACKUP_HOST_DIR"
sqlite3 "$(find "$BACKUP_HOST_DIR" -type f -name 'bookkeeping-*.db' | sort | tail -n 1)" \
  "PRAGMA integrity_check;"
```

完整性检查必须输出 `ok`。这些备份仍位于同一台 VPS，不能覆盖整机、磁盘损坏或账号失陷；
应另行把加密备份复制到独立存储。

## 8. 恢复演练

上线前必须在临时目录完成一次恢复演练；生产恢复建议安排维护窗口。

```bash
cd /opt/bookkeeping
export DATA_HOST_DIR=/var/lib/bookkeeping/data
export BACKUP_FILE=/var/backups/bookkeeping/bookkeeping-YYYY-MM-DD.db

sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;"
docker compose stop app
sqlite3 "$DATA_HOST_DIR/bookkeeping.db" "PRAGMA wal_checkpoint(TRUNCATE);"
cp -a "$DATA_HOST_DIR/bookkeeping.db" \
  "$DATA_HOST_DIR/bookkeeping.db.pre-restore-$(date +%F-%H%M%S)"
sqlite3 "$DATA_HOST_DIR/bookkeeping.db" ".restore '$BACKUP_FILE'"
sudo chown 10001:10001 "$DATA_HOST_DIR/bookkeeping.db"
sqlite3 "$DATA_HOST_DIR/bookkeeping.db" "PRAGMA integrity_check;"
docker compose start app
curl -fsS "https://${DOMAIN}/health"
```

恢复前和恢复后的 `PRAGMA integrity_check` 都必须输出 `ok`。确认关键交易记录与汇总无误后，
再删除恢复前副本。

## 9. 常见故障

- `/health` 返回 503：查看 app 日志，并检查 `DATA_HOST_DIR` 权限、磁盘空间和 SQLite 文件。
- Caddy 无法签发证书：检查 A/AAAA、80/443 入站规则、域名是否已传播及 Caddy 日志。
- 修改 `.env` 后配置没有生效：使用 `docker compose up -d --force-recreate app`。
- 宿主机能访问 8000：Compose 配置被错误覆盖；移除 app 的 `ports` 映射并检查防火墙。
- 容器反复退出：运行 `docker compose ps` 和 `docker compose logs --tail=200 app caddy`。

## 10. 上线验收

- `docker compose config --quiet` 和镜像构建均成功。
- app 容器进程 UID 为 10001，健康状态为 `healthy`。
- 宿主机没有 app 的 8000 映射；HTTP 跳转 HTTPS，正式证书有效。
- 前端 `index.html` 与静态资源正常返回，刷新 `/transactions` 等前端路由不 404。
- `/api/transactions` 未带 Token 返回 401；浏览器中使用 `ADMIN_PASSWORD` 能完成登录、新增、编辑、删除与统计查看。
- 空数据目录首次启动可创建数据库，重新创建容器后数据仍存在。
- 两枚 Token 不能互换；各自的旧值返回 401，新值能访问对应权限接口。
- 日志包含错误上下文但不包含密钥、完整 Authorization 或完整模型响应。
- 每日 `.backup` 成功、30 天保留生效，并完成恢复与完整性检查演练。

## 11. Alembic 数据库迁移

镜像包含 `alembic.ini` 与 `migrations/`。app 容器的启动顺序固定为：

```text
python -m alembic upgrade head
uvicorn app.main:app ...
```

迁移失败时 Uvicorn 不会启动，Compose 健康检查也不会通过。首次接入 Alembic 时，初始迁移会接管字段兼容的旧 `transactions` 表并写入 `alembic_version`；结构不兼容时会停止并报错，不会删除或重建旧表。

更新前先完成 SQLite 一致性备份，再构建新镜像。可在启动前单独验证迁移：

```bash
docker compose build app
docker compose run --rm app python -m alembic current
docker compose run --rm app python -m alembic upgrade head
docker compose up -d
```

查看当前版本与可用迁移：

```bash
docker compose run --rm app python -m alembic current
docker compose run --rm app python -m alembic history
```

代码回滚不会自动回滚数据库结构。数据库降级可能丢失字段或整张表，执行任何 `downgrade` 前必须确认迁移脚本、停止应用并创建可恢复备份；初始迁移的 downgrade 会删除 `transactions` 表，生产环境不得执行。
