# AlphaChow 博客

一套源码，两个站：

| | 公开站 | 内部知识库 |
|---|---|---|
| 内容目录 | `content/posts/` | `internal/content/posts/` |
| 构建命令 | `blog.cmd publish` | `blog.cmd internal` |
| 产物 | `dist/public/` | `dist/internal/` |
| 访问 | https://alphachowai.com | http://localhost:1314 |
| 是否上传服务器 | 是 | **否，永不上传** |

## 日常用法

```bat
blog.cmd new "文章标题"       :: 新建公开文章
blog.cmd new "笔记标题" -i    :: 新建内部笔记
blog.cmd internal             :: 本地预览内部知识库（自动开浏览器）
blog.cmd publish              :: 构建公开站并发布上线
blog.cmd status               :: 检查线上是否正常
```

`blog.cmd publish` 会依次做：本地构建 → 打包 → SSH 上传 → 服务器注入备案号 → 切换线上目录 → 备份旧版本 → 重载 Caddy → 回验 HTTP 与备案号。

## 目录结构

```
hugo.toml                 公开站配置（baseURL=https://alphachowai.com/）
config.internal.toml      内部站覆盖配置（叠加在 hugo.toml 之上）
content/posts/            公开文章
internal/content/posts/   内部笔记
themes/demius/            主题，随仓库内置，不依赖外网
dist/                     构建产物，不进仓库
tools/cli.py              命令行工具
tools/server/publish-blog.sh   服务器端发布脚本（部署在 /usr/local/bin/）
deploy.local.json         本机配置（服务器地址/密钥路径），不进仓库
```

## 首次在新机器上使用

1. 装 Hugo **extended**（版本须与线上一致，当前 v0.150.1）与 Node.js；
2. 在仓库根目录 `npm install`（主题的 CSS 走 PostCSS，必须有 `node_modules/.bin/postcss`）；
3. 复制 `deploy.local.example.json` 为 `deploy.local.json`，填入自己的路径；
4. 确保本机有服务器私钥，且公钥已在服务器 `/root/.ssh/authorized_keys` 里。

## 服务器端

```
线上目录    /mnt/data/hugo/live      （Caddy 的 root）
备份目录    /mnt/data/hugo/releases  （保留最近 5 个版本）
发布脚本    /usr/local/bin/publish-blog.sh
备案模板    /etc/alphachowai/beian.html
Web 服务    Caddy 2.6.2，配置 /etc/caddy/Caddyfile
```

回滚：

```bash
ssh root@119.91.129.185 "publish-blog.sh --rollback"
```

## 写文章注意

- front matter 用 TOML（`+++` 包裹）。
- 一定要写 `slug`，否则中文标题会直接变成中文文件名 URL。
- 草稿 `draft = true` 只有内部预览（`-D`）可见，公开站不会构建。
