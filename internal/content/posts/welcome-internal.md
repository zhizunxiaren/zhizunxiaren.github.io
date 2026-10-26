+++
title = "内部知识库说明"
date = 2026-10-26T23:10:00+08:00
draft = false
tags = ["使用说明"]
categories = ["内部"]
+++

这里是**只在你自己电脑上**构建和浏览的内部知识库。它和 `alphachowai.com` 公开站共用同一套主题与目录，但内容目录完全隔离：

| 项目 | 公开站 | 内部站 |
|---|---|---|
| 内容目录 | `content/posts/` | `internal/content/posts/` |
| 构建产物 | `dist/public/` | `dist/internal/` |
| 是否上传服务器 | 是 | **否，永远不传** |
| 访问地址 | https://alphachowai.com | http://localhost:1314 |

## 怎么写

在 `internal/content/posts/` 下新建 `.md` 文件，开头用 TOML front matter：

```toml
+++
title = "标题"
date = 2026-10-26T10:00:00+08:00
draft = false
tags = ["标签"]
categories = ["分类"]
+++
```

正文用 Markdown 即可。

## 怎么看

```bash
发布.bat internal          # 启动本地预览，浏览器打开 http://localhost:1314
```

## 注意

- `draft = true` 的文章默认不渲染，预览时加 `-D` 才显示（内部预览默认带 `-D`）。
- 公开站构建时**不会**扫到 `internal/` 目录，两者互不影响。
