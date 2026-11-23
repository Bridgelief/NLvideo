# 团队协作手册（Git 仓库 NLvideo）

> 本仓库名：**NLvideo**（AI 现场拍摄指导助手 / 手机端原型）。
> 默认分支：`main`。共享裸库：`D:\NLvideo.git`（可局域网直连，也可导出成 bundle 发文件）。
> 有疑问先看 [README.md](../README.md) 与 [docs/PHONE_GUIDE.md](PHONE_GUIDE.md)；本文只讲"三个人怎么一起改代码"。

---

## 1. 仓库里有什么 / 没有什么

| 入库（有用的都在这） | 不入库（`.gitignore`，可随时重建） |
|---|---|
| `web/` 手机端前端（HTML/JS/CSS/SW/PWA 图标） | `certs/*.pem`（开发证书私钥，**不能外传**） |
| `server/` FastAPI 服务 + 特征提取 | `_tools/`（本机构建 APK 用的 Node/JDK/Android SDK，约 2GB） |
| `engine/` 建议引擎 / 剪辑引擎 / EDL 导出 | `mobile/node_modules/`、`mobile/android/.gradle`、`.../build/` |
| `data/` 意图权重、50 条话术、规则库、素材/标注表 | `output/`（拍摄记录、反馈、粗剪结果等运行时数据） |
| `tools/` 证书生成、demo 帧、图标、素材清单、EDL→ffmpeg | `__pycache__/`、`*.log`、`*.zip` |
| `scripts/` 启动 HTTP/HTTPS、局域网 Git 共享 | |
| `tests/` 131 个契约测试 + 3 个端到端验收脚本 | |
| `docs/` 接口契约、架构、验收报告、手机操作手册、截图 | |
| `mobile/` Capacitor 安卓工程（**含已构建的 `shooting-guide-debug.apk`**，方便队友直接装） | |
| `reference/` 原始方案 HTML + 三人交付包（只读参考） | |

> 证书缺失不影响别人：克隆后跑 `python tools/make_dev_cert.py` 就会按**你这台电脑的局域网 IP** 重新生成。

---

## 2. 队友第一次上手（10 分钟）

### 2.1 装 Git

- 下载安装：<https://git-scm.com/download/win>（一路默认即可）
- 验证：`git --version`
- 设置身份（只需一次，写自己的名字）：

```powershell
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"
git config --global core.autocrlf false
```

### 2.2 拿到代码（三种方式按需选一种）

#### 方式 A：同一局域网（最快，可推可拉）

```powershell
# 电脑 A（仓库主人）：启动共享服务（保持窗口不关）
powershell -ExecutionPolicy Bypass -File scripts\git-serve.ps1
# 它会打印：git clone http://<IP>:8088/NLvideo.git

# 队友电脑 B/C（同一 Wi-Fi）：
git clone http://<IP>:8088/NLvideo.git NLvideo
cd NLvideo
```

- 第一次需要放行防火墙（管理员执行一次）：
  `netsh advfirewall firewall add rule name="NLvideo git 8088" dir=in action=allow protocol=TCP localport=8088`
- 共享服务由 `scripts/git-serve.ps1` 启动，`Ctrl+C` 关闭；**默认没有账号密码**，只适合家/宿舍/手机热点这类可信网络。
- 想加一道最简口令：`scripts\git-serve.ps1 -Token 123456`，队友 clone 时用户名随意、密码填 `123456`。
- 推拉都走这个地址：`git push` / `git pull` 即可（本机已实测 clone + push + pull 全通）。
- 实现说明：`tools/git_http_server.py` 用 Git 自带的 `git http-backend` 提供智能 HTTP 协议。
  （本机实测 `git daemon` 的 git:// 推送会在 Windows 上卡住，所以不用它；clone 仍可，但不推荐。）

#### 方式 B：文件包（异地/没有局域网也能用）

```powershell
# 电脑 A 生成完整仓库包（含全部历史，单个文件）
git bundle create D:\NLvideo.bundle --all

# 队友电脑 B/C 收到文件后（微信/QQ/网盘/数据线都行）：
git clone D:\NLvideo.bundle NLvideo
cd NLvideo
# 之后 A 有新提交，可以再导出增量包：
#   git bundle create update.bundle <上次同步点>..main
#   B 里：git pull D:\update.bundle main
```

#### 方式 C：云端（GitHub / Gitee，长期协作推荐）

由仓库主人在 GitHub/Gitee 新建空仓库 `NLvideo`，然后：

```powershell
# 仓库主人（电脑 A）
git remote add github https://github.com/<你的账号>/NLvideo.git
git push -u github main

# 队友（电脑 B/C）
git clone https://github.com/<你的账号>/NLvideo.git NLvideo
# 或 Gitee 同理；云端仓库里把队友加成 Collaborator / 项目成员即可推送
```

> 云端仓库有账号体系、能异地协作、有历史记录；注意**公开仓库不要放私人素材/证书**（本项目已忽略证书）。
> 如果先用了方式 A 的局域网地址，之后可随时 `git remote add github ...` 补充：一个仓库可以有多个 remote。

### 2.3 跑起来（每个人电脑上）

```powershell
cd NLvideo
python --version    # 需要 Python 3.11+
python -m pip install fastapi uvicorn pydantic numpy opencv-python pillow cryptography requests httpx
python tools/make_dev_cert.py                 # 生成本机证书（手机真机用）
powershell -ExecutionPolicy Bypass -File scripts\start.ps1        # 桌面：http://127.0.0.1:8000
# 或手机真机：powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1  → https://<本机IP>:8443
```

- 自检：`python -m unittest discover -s tests`（应看到 `Ran 131 tests … OK`）。
- 手机/APK 详细操作：见 [docs/PHONE_GUIDE.md](PHONE_GUIDE.md)。
- 安卓 App 只有在改 `mobile/` 或换网络时需要重构建：`mobile\README.md`（需要 Node/JDK/Android SDK）。

---

## 3. 三个人怎么分工（建议，可在群里调整）

按"目录所有权"分，减少冲突：

| 角色 | 主要写域 | 负责内容 |
|---|---|---|
| 算法 | `engine/` + `data/` | 评分/规则/话术/剪辑逻辑；调参后跑测试 |
| 前端 | `web/` | 界面/相机/叠加层/建议卡片/粗剪面板/PWA |
| 服务 & 文档 | `server/` `tools/` `scripts/` `tests/` `docs/` | API、特征提取、启动脚本、测试与文档 |

- **改接口字段 = 先改 `docs/INTERFACES.md` 再改代码**，并在群里说一声；契约是三个人的生命线。
- 不确定归谁改的公共文件（README、`docs/`）先发消息，避免同时改。
- `reference/` 是只读参考，不要改。

---

## 4. 日常 Git 流程（每个人每天）

```powershell
git pull --rebase            # 1) 先同步，避免冲突（本地有未提交改动先 commit）
git status                   # 2) 看我改了哪些
git add -A                   # 3) 暂存（或用 git add 具体文件）
git commit -m "前端：建议卡片增加采纳状态"   # 4) 提交，写清楚改了什么
git push                     # 5) 推回共享库
```

### 分支策略（简单版）

- `main` 始终保持"能跑、测试通过"；小改动可直接在 main 上提交（双人小团队够用）。
- 需要试验大改动时：`git switch -c feature/xxx` → 改 → `git push -u origin feature/xxx` → 群里说一下合回 main：
  ```powershell
  git switch main
  git pull --rebase
  git merge feature/xxx
  git push
  ```
- 合并前跑一遍 `python -m unittest discover -s tests`；不要提交跑不过的 main。

### 提交前自查（30 秒）

```powershell
git status                  # 确认没把 certs/*.pem、_tools/、node_modules、output/ 加进去
git diff --cached --stat    # 看这次改了哪些文件、量级是否合理
python -m unittest discover -s tests
```

### 冲突了怎么办

```powershell
git pull --rebase
# 若提示 CONFLICT：打开冲突文件，找 <<<<<<< ======= >>>>>>> 三段，改成最终想要的代码
git add <冲突文件>
git rebase --continue
# 想放弃这次 rebase：git rebase --abort
```

拿不准就**先别 push**，把 `git status` 和冲突文件截图发群里一起看。

---

## 5. 共享服务管理（局域网方式）

```powershell
# 启动（窗口别关，Ctrl+C 停止）
powershell -ExecutionPolicy Bypass -File scripts\git-serve.ps1
# 常用参数：-Port 8089（换端口）、-Repo D:\NLvideo.git（指定共享库）、-Token 123456（加口令）
# 换 Python：-PythonExe "C:\Python311\python.exe"
```

- 共享库是**裸仓库** `D:\NLvideo.git`（没有工作目录，只存历史）；`D:\videoshoot` 是带工作区的仓库，`D:\NLvideo` 是指向它的目录链接（同一份文件）。
- 服务实现：`tools/git_http_server.py` → 调用 Git 自带的 `git http-backend` 提供智能 HTTP 协议（clone/fetch/push 同一个地址）。
- 队友 push 到裸库后，仓库主人 `git pull` 一下就能拿到（或直接 push 到同一个 `origin`）。
- 脚本会自动找 Python；`tools/git_http_server.py` 会自动找 `git-http-backend.exe`（优先本仓库 `_tools/git-full/`，也支持用 `--backend` 指定）。
- 想停止：在脚本窗口 `Ctrl+C`；或用任务管理器结束对应 python 进程。

---

## 6. 常见问题

| 症状 | 原因 / 解决 |
|---|---|
| `git: 无法将"git"项识别为...` | 没装 Git 或没进 PATH；装 https://git-scm.com/download/win 后重开终端 |
| `git clone` 局域网地址超时 | 不在同一 Wi-Fi / 防火墙没放行 8088 / `git-serve.ps1` 没启动 |
| push 被拒（`non-fast-forward`） | 别人先推了：`git pull --rebase` 后再 push |
| 行尾大量变化 / 文件显示"已修改"但没动 | 行尾差异：仓库已配 `.gitattributes`（LF）+ `core.autocrlf false`；按上面设置一次即可 |
| `certificate verify failed` / 证书文件缺失 | 证书不入库，跑 `python tools/make_dev_cert.py` |
| APK 装不上 | debug 签名只能测试用；确认文件 3,799,027 字节；Android 7+ |
| `python: 找不到` | 装 Python 3.11+ 并勾选 "Add to PATH" |
| 前端改了但页面没变 | 强刷；PWA 需把 `web/sw.js` 的 `VERSION` +1 后重装/重开 |
| 想撤掉本地没提交的修改 | `git restore <文件>`；全部撤销：`git restore .`（谨慎） |
| 提交错文件想撤回（还没 push） | `git reset --soft HEAD~1` 后重新 add/commit |

---

## 7. 安全与纪律

1. **证书私钥、`_tools/`、`node_modules/`、`output/` 永不提交**（`.gitignore` 已处理，别 `git add -f`）。
2. 局域网 Git HTTP 服务**默认没有账号密码**（可用 `-Token` 加一道口令），同网设备可读写；只在自己家/宿舍/热点用，演示完关掉。
3. 不要把仓库整体（含 `.git`）发到公共网盘/公开仓库——历史里可能有本机路径；用 `git bundle` 或云端私有仓库共享更安全。
4. APK 是 debug 签名，不能上架；正式发布按 `mobile/README.md` §7 配 release 签名。
5. 每次提交写清楚：`模块：做了什么`（例：`server: /api/suggest 增加超时提示`），方便队友 review 和回滚。
