# RepoOps WebUI Source

这里是保留自 nanobot 的 React/TypeScript WebUI。日常使用无需修改前端，运行：

```bash
uv run repoops webui
```

前端开发：

```bash
# 终端一
uv run repoops gateway

# 终端二
cd webui
bun install
bun run dev
```

开发服务器默认打开 `http://127.0.0.1:5173`，并把 API 与 WebSocket 请求代理到
`http://127.0.0.1:8765`。如 Gateway 使用其他端口：

```bash
NANOBOT_API_URL=http://127.0.0.1:9000 bun run dev
```

验证：

```bash
bun run test
bun run build
```

生产构建输出到 `nanobot/web/dist/`，由 Gateway 提供服务。
