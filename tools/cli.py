#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alphachowai 博客命令行工具。

用法:
  python tools/cli.py publish              构建 → 发主站 → 推镜像站（双链合一）
  python tools/cli.py publish --no-github  只发主站 alphachowai.com
  python tools/cli.py publish --github-only  只推 GitHub 镜像站（本地已构建过时用）
  python tools/cli.py publish --no-wait    推送后不等待 Actions 构建结果
  python tools/cli.py internal             启动内部知识库本地预览 (http://localhost:1314)
  python tools/cli.py build                只构建，不上传（public + internal）
  python tools/cli.py new "文章标题"        新建一篇公开文章
  python tools/cli.py new "笔记标题" -i     新建一篇内部笔记
  python tools/cli.py status               查看线上站点状态（主站 + 镜像站）

publish 的两条链路:
  主站 alphachowai.com  — 本机构建 → tar → ssh 上传 → 服务器 publish-blog.sh
  镜像站 zhizunxiaren.github.io — git commit + push → GitHub Actions 自动构建

依赖: hugo(extended) + node(postcss) + 系统自带 ssh/git，均为本机已装。
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
    "mirror_domain": "https://zhizunxiaren.github.io",
    "mirror_branch": "main",
    "mirror_wait_seconds": 150,
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
    print("[构建] 公开站")
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

    print("[上传] 构建产物")
    with open(tar_path, "rb") as f:
        run(base + ["cat > %s" % remote_tar], env, stdin=f)

    print("[发布] 服务器端")
    run(base + ["bash /usr/local/bin/publish-blog.sh %s" % remote_tar], env)

    print("[清理] 远端临时文件")
    run(base + ["rm -f %s" % remote_tar], env, check=False)


def http_check(url):
    """访问 url，返回 (状态码或 None, 页面正文, 错误信息)。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "alphachow-publish/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return e.code, "", None
    except Exception as e:  # noqa: BLE001
        return None, "", repr(e)


def verify(conf, label="主站", require_beian=True):
    print("[验证] %s" % label)
    url = conf.get("domain", "https://alphachowai.com").rstrip("/") + "/"
    status, body, err = http_check(url)
    if status is None:
        print("  x 无法访问 %s: %s" % (url, err))
        return False
    ok = status == 200
    print("  %s HTTP %d %s" % ("v" if ok else "x", status, url))
    has_beian = bool(re.search(r"蜀ICP备\d+号", body))
    if require_beian:
        print("  %s 备案号渲染" % ("v" if has_beian else "x"))
        if not has_beian:
            print("    ! 页面里没找到备案号，检查 hugo.toml 的 [params.footer] beian")
    return ok and (has_beian or not require_beian)


# ---------------------------------------------------------------- GitHub 镜像站

def git(args, env, check=False):
    """跑 git 子命令，返回 (returncode, 合并后的输出)。"""
    proc = subprocess.run(["git"] + args, cwd=ROOT, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise SystemExit("git %s 失败: %s" % (" ".join(args), out.strip()[:300]))
    return proc.returncode, out


def git_repo_info(env):
    """从 origin 地址解析出 (owner, repo)；解析不到返回 (None, None)。"""
    code, out = git(["remote", "get-url", "origin"], env)
    if code != 0:
        return None, None
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", out.strip())
    if not m:
        return None, None
    return m.group(1), m.group(2)


def push_mirror(conf, env):
    """提交本地改动并推送到 GitHub，触发镜像站 Actions 构建。

    返回 (状态, 说明)，状态取值：True 成功 / False 失败 / None 跳过。
    失败不致命——主站已经发布完成，可以稍后手动补推。
    """
    code, _ = git(["rev-parse", "--git-dir"], env)
    if code != 0:
        return None, "当前目录不是 git 仓库，跳过镜像站推送"

    branch = conf.get("mirror_branch", "main")
    code, out = git(["status", "--porcelain"], env)
    dirty = bool(out.strip())
    if dirty:
        changed = len([ln for ln in out.splitlines() if ln.strip()])
        print("  发现 %d 处改动，先提交" % changed)
        code, out = git(["add", "-A"], env)
        if code != 0:
            return False, "git add 失败: " + out.strip()[:200]
        msg = "publish: " + time.strftime("%Y-%m-%d %H:%M")
        code, out = git(["commit", "-m", msg], env)
        if code != 0:
            return False, "git commit 失败: " + out.strip()[:200]
        print("  已提交: %s" % msg)
    else:
        print("  工作区干净，无需提交")

    # 无改动且没有待推送的提交 → 直接跳过，避免空跑 Actions
    if not dirty:
        code, out = git(["rev-list", "--count", "origin/%s..HEAD" % branch], env)
        if code == 0 and out.strip() == "0":
            return None, "没有新提交，跳过推送"

    # 代理环境下 github.com 会间歇性 502，重试三次
    last = ""
    for attempt in range(1, 4):
        code, out = git(["push", "origin", "%s:%s" % (branch, branch)], env)
        if code == 0:
            return True, "已推送到 origin/%s" % branch
        lines = [ln for ln in out.strip().splitlines() if ln.strip()]
        last = lines[-1] if lines else "未知错误"
        print("  推送失败（第 %d/3 次）: %s" % (attempt, last))
        if attempt < 3:
            time.sleep(4)
    return False, "推送连续三次失败（多半是代理抖动），稍后手动执行 git push origin %s" % branch


def wait_actions(conf, env, sha):
    """轮询 Actions，等本次 commit 的构建跑完。返回 (结论, 运行链接)。"""
    owner, repo = git_repo_info(env)
    if not owner or not sha:
        return None, None
    api = ("https://api.github.com/repos/%s/%s/actions/runs?per_page=10"
           % (owner, repo))
    deadline = time.time() + int(conf.get("mirror_wait_seconds", 150))
    run = None
    last_status = ""
    while True:
        try:
            req = urllib.request.Request(api, headers={
                "User-Agent": "alphachow-publish/1.0",
                "Accept": "application/vnd.github+json",
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            print("  ! 查询 Actions 状态失败: %r" % e)
            return None, None

        for item in data.get("workflow_runs", []):
            if item.get("head_sha") == sha:
                run = item
                break

        if run and run.get("status") == "completed":
            return run.get("conclusion"), run.get("html_url")
        if run:
            st = run.get("status")
            if st != last_status:
                print("  Actions 构建中（%s）..." % st)
                last_status = st
        elif time.time() > deadline:
            print("  ! 没找到本次 commit 的构建任务（可能还没排队）")
            return None, None
        if time.time() > deadline:
            print("  ! 等待超时，构建可能仍在进行")
            return None, run.get("html_url") if run else None
        time.sleep(6)


def verify_mirror(conf, env, sha, wait=True):
    """验证镜像站：先等 Actions 跑完，再检查首页可访问。"""
    domain = (conf.get("mirror_domain") or "").rstrip("/")
    if not domain:
        return True

    if wait:
        print("[验证] 镜像站（等待 GitHub Actions）")
        conclusion, url = wait_actions(conf, env, sha)
        if conclusion:
            print("  %s 构建结论: %s" % ("v" if conclusion == "success" else "x", conclusion))
        if url:
            print("  %s" % url)
        if conclusion and conclusion != "success":
            print("    ! 构建失败，去上面的链接看日志；旧站可用 git push -f origin legacy-pages:main 恢复")
            return False
    else:
        print("[验证] 镜像站（跳过等待 Actions）")

    status, body, err = http_check(domain + "/")
    if status is None:
        print("  x 无法访问 %s: %s" % (domain, err))
        return False
    ok = status == 200
    print("  %s HTTP %d %s" % ("v" if ok else "x", status, domain + "/"))
    return ok


def cmd_publish(args):
    do_server = "--github-only" not in args
    do_github = "--no-github" not in args
    wait = "--no-wait" not in args
    if not do_server and not do_github:
        raise SystemExit("--github-only 与 --no-github 不能同时用")

    conf = load_conf()
    env = build_env(conf)
    total = int(do_server) + int(do_github)
    step = 0
    results = {}

    if do_server:
        step += 1
        print("== %d/%d  主站 %s ==" % (step, total, conf.get("domain", "").rstrip("/")))
        build_public(conf, env)
        tar_path = make_tarball()
        upload_and_publish(conf, env, tar_path)
        results["主站"] = verify(conf, "主站")

    if do_github:
        step += 1
        print()
        print("== %d/%d  镜像站 %s ==" % (step, total,
                                       (conf.get("mirror_domain") or "").rstrip("/")))
        pushed, msg = push_mirror(conf, env)
        print("  %s %s" % ({True: "v", False: "x"}.get(pushed, "-"), msg))
        if pushed:
            code, out = git(["rev-parse", "HEAD"], env)
            head = out.strip() if code == 0 else ""
            results["镜像站"] = verify_mirror(conf, env, head, wait)
        elif pushed is None:
            results["镜像站"] = verify_mirror(conf, env, "", wait=False)
        else:
            results["镜像站"] = False

    print()
    print("== 结果 ==")
    for name, ok in results.items():
        print("  %s %s" % ("v" if ok else "x", name))
    all_ok = bool(results) and all(results.values())
    print()
    print("发布完成，两条链路都通了。" if all_ok
          else "发布结束，有未通过项，请检查上面的提示。")
    return 0 if all_ok else 1


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
    env = build_env(conf)
    ok_main = verify(conf, "主站")
    ok_mirror = verify_mirror(conf, env, "", wait=False)
    return 0 if (ok_main and ok_mirror) else 1


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
