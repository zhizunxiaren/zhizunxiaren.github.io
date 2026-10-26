#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alphachowai 博客命令行工具。

用法:
  python tools/cli.py publish              构建并发布公开站到 alphachowai.com
  python tools/cli.py internal             启动内部知识库本地预览 (http://localhost:1314)
  python tools/cli.py build                只构建，不上传（public + internal）
  python tools/cli.py new "文章标题"        新建一篇公开文章
  python tools/cli.py new "笔记标题" -i     新建一篇内部笔记
  python tools/cli.py status               查看线站在线状态

依赖: hugo(extended) + node(postcss) + 系统自带 ssh，均为本机已装。
配置: deploy.local.json（见 deploy.local.example.json）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_PATH = os.path.join(ROOT, "deploy.local.json")

DEFAULTS = {
    "host": "",
    "user": "root",
    "port": 22,
    "ssh_key": "",
    "domain": "https://alphachowai.com",
    "hugo": "hugo",
    "node_dir": "",
    "internal_port": 1314,
}


def load_conf():
    conf = dict(DEFAULTS)
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            conf.update(json.load(f))
    return conf


def build_env(conf):
    env = os.environ.copy()
    paths = []
    if conf.get("node_dir"):
        paths.append(conf["node_dir"])
    paths.append(os.path.join(ROOT, "node_modules", ".bin"))
    hugo = conf.get("hugo", "hugo")
    if os.path.sep in hugo or "/" in hugo:
        paths.append(os.path.dirname(hugo))
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    return env


def run(cmd, env, cwd=ROOT, check=True, stdin=None):
    printable = " ".join(cmd) if isinstance(cmd, list) else cmd
    print("  > %s" % printable)
    proc = subprocess.run(cmd, cwd=cwd, env=env, stdin=stdin,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    if out.strip():
        for line in out.rstrip().splitlines()[-12:]:
            print("    " + line)
    if check and proc.returncode != 0:
        raise SystemExit("命令失败 (exit %d): %s" % (proc.returncode, printable))
    return proc.returncode


def hugo_cmd(conf):
    hugo = conf.get("hugo") or "hugo"
    if os.path.exists(hugo):
        return [hugo]
    found = shutil.which(hugo)
    if found:
        return [found]
    raise SystemExit(
        "找不到 hugo。请在 deploy.local.json 的 \"hugo\" 填入完整路径，"
        "例如 C:/Users/YourName/.workbuddy/binaries/hugo/hugo.exe"
    )


def build_public(conf, env):
    print("[1/4] 构建公开站")
    run(hugo_cmd(conf) + ["--minify", "--cleanDestinationDir", "-d", "dist/public"], env)
    out = os.path.join(ROOT, "dist", "public", "index.html")
    if not os.path.exists(out):
        raise SystemExit("构建产物缺失: " + out)


def build_internal(conf, env):
    print("构建内部站")
    run(hugo_cmd(conf) + ["--config", "hugo.toml,config.internal.toml",
                          "--minify", "--cleanDestinationDir",
                          "-d", "dist/internal", "-D"], env)


def make_tarball():
    src = os.path.join(ROOT, "dist", "public")
    tar_path = os.path.join(ROOT, "dist", "publish-%s.tgz" % time.strftime("%Y%m%d-%H%M%S"))

    def normalize(ti):
        # Windows 上 os.stat 给出的权限不可用，统一成服务器上的常规权限
        ti.uid = 0
        ti.gid = 0
        ti.uname = "root"
        ti.gname = "root"
        ti.mode = 0o755 if ti.isdir() else 0o644
        return ti

    with tarfile.open(tar_path, "w:gz") as tar:
        for name in os.listdir(src):
            tar.add(os.path.join(src, name), arcname=name, filter=normalize)
    size_mb = os.path.getsize(tar_path) / 1048576.0
    print("  > 打包 %s (%.1f MB)" % (os.path.basename(tar_path), size_mb))
    return tar_path


def ssh_base(conf):
    if not conf.get("host"):
        raise SystemExit("deploy.local.json 缺少 host")
    cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new",
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
           "-p", str(conf.get("port", 22))]
    key = os.path.expanduser(conf.get("ssh_key") or "")
    if key:
        if not os.path.exists(key):
            raise SystemExit("找不到 SSH 私钥: " + key)
        cmd += ["-i", key]
    cmd.append("%s@%s" % (conf.get("user", "root"), conf["host"]))
    return cmd


def upload_and_publish(conf, env, tar_path):
    base = ssh_base(conf)
    remote_tar = "/tmp/blog-deploy-%d.tgz" % int(time.time())

    print("[2/4] 上传构建产物")
    with open(tar_path, "rb") as f:
        run(base + ["cat > %s" % remote_tar], env, stdin=f)

    print("[3/4] 服务器端发布")
    run(base + ["bash /usr/local/bin/publish-blog.sh %s" % remote_tar], env)

    print("[4/4] 清理远端临时文件")
    run(base + ["rm -f %s" % remote_tar], env, check=False)


def verify(conf):
    print("[验证] 检查线上站点")
    url = conf.get("domain", "https://alphachowai.com").rstrip("/") + "/"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "alphachow-publish/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        print("  x 站点返回 HTTP %s" % e.code)
        return False
    except Exception as e:  # noqa: BLE001
        print("  x 无法访问 %s: %r" % (url, e))
        return False

    ok = status == 200
    print("  %s HTTP %d %s" % ("v" if ok else "x", status, url))
    has_beian = bool(re.search(r"蜀ICP备\d+号", body))
    print("  %s 备案号注入" % ("v" if has_beian else "x"))
    if not has_beian:
        print("    ! 页面里没找到备案号，检查 /etc/alphachowai/beian.html 与页脚占位符")
    return ok and has_beian


def cmd_publish(_args):
    conf = load_conf()
    env = build_env(conf)
    build_public(conf, env)
    tar_path = make_tarball()
    upload_and_publish(conf, env, tar_path)
    ok = verify(conf)
    print()
    print("发布完成。" if ok else "发布后校验未通过，请检查上面的提示。")
    return 0 if ok else 1


def cmd_internal(_args):
    conf = load_conf()
    env = build_env(conf)
    port = conf.get("internal_port", 1314)
    url = "http://localhost:%d/" % port
    print("内部知识库预览: %s   (Ctrl+C 退出)" % url)
    print("注意: 只在本机运行，不会上传到任何服务器。")
    try:
        subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    except Exception:  # noqa: BLE001
        pass
    return run(hugo_cmd(conf) + ["server", "--config", "hugo.toml,config.internal.toml",
                                 "-D", "--port", str(port), "--bind", "127.0.0.1"],
               env, check=False)


def cmd_build(_args):
    conf = load_conf()
    env = build_env(conf)
    build_public(conf, env)
    build_internal(conf, env)
    print("构建完成: dist/public (公开) / dist/internal (内部)")
    return 0


def cmd_new(args):
    if not args:
        raise SystemExit('用法: python tools/cli.py new "文章标题" [-i]')
    internal = "-i" in args or "--internal" in args
    words = [a for a in args if not a.startswith("-")]
    title = " ".join(words).strip()
    if not title:
        raise SystemExit('请提供标题')

    slug = re.sub(r"\s+", "-", title.lower())
    slug = re.sub(r"[^\w\-\u4e00-\u9fa5]", "", slug)
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if internal:
        base = os.path.join(ROOT, "internal", "content", "posts")
    else:
        base = os.path.join(ROOT, "content", "posts")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, slug + ".md")
    if os.path.exists(path):
        raise SystemExit("已存在: " + path)
    body = (
        "+++\n"
        'title = "%s"\n'
        "date = %s\n"
        "draft = true\n"
        'slug = "%s"\n'
        'tags = []\n'
        'categories = []\n'
        "+++\n\n"
        "在这里开始写正文。\n\n"
        "<!--more-->\n"
    ) % (title, now, slug)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    print("已创建: %s" % os.path.relpath(path, ROOT))
    print("记得把 draft 改成 false 再发布。")
    return 0


def cmd_status(_args):
    conf = load_conf()
    verify(conf)
    return 0


COMMANDS = {
    "publish": cmd_publish,
    "internal": cmd_internal,
    "build": cmd_build,
    "new": cmd_new,
    "status": cmd_status,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    action = sys.argv[1]
    if action not in COMMANDS:
        print("未知命令: %s\n" % action)
        print(__doc__)
        return 2
    return COMMANDS[action](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
