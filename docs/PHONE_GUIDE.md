# 手机 + 电脑 操作手册

> 面向：要在**电脑上跑服务、用手机真机演示**的人（答辩/试用/自己玩）。
> 适用版本：v1.3（2026-11-23）。仓库根目录：`D:\videoshoot`。
> 三种玩法先对号入座：
>
> | 想做什么 | 电脑 | 手机 | 相机 |
> |---|---|---|---|
> | A. 在电脑上看界面/录屏 | `scripts\start.ps1`（HTTP 8000） | 不用手机 | 电脑摄像头可用 |
> | B. 手机浏览器/PWA 真机演示 | `scripts\start-https.ps1`（HTTPS 8443） | 浏览器打开 `https://<电脑IP>:8443` | 可用（需信任证书） |
> | C. 安卓 App 演示 | 同上（HTTPS 8443 必须开着） | 安装 `mobile\shooting-guide-debug.apk` | 可用（App 内置信任开发证书） |

---

## 0. 一分钟速查

```powershell
# ① 电脑（仓库根 D:\videoshoot）：起 HTTPS 服务，保持这个窗口别关
powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1
#    看到 "手机打开 : https://172.20.10.7:8443" 就成功（IP 以实际为准）

# ② 手机（和电脑连同一个 Wi-Fi）：浏览器打开上面这个地址
#    首次提示证书：装 CA（推荐，见 §4.2）或点「高级 → 继续前往」
#    允许相机 → 顶部状态变「相机就绪」

# ③ 装到桌面（可选）：浏览器菜单「安装应用 / 添加到主屏幕」
```

**手机上只需要记住 3 个操作**：点橙色「给建议」→ 点某条建议的「已按此调整」→ 点「记录镜头」；拍完点「生成粗剪」。

---

## 1. 电脑端准备

### 1.1 确认环境（一次性）

```powershell
cd D:\videoshoot
python --version        # 需要 3.11+
python -c "import fastapi, uvicorn, cv2, numpy, PIL, cryptography; print('依赖 OK')"
```

- 首次运行如果报缺包：`python -m pip install fastapi uvicorn pydantic numpy opencv-python pillow cryptography requests httpx`
- 不需要 node/JDK/Android SDK（只有重新构建 APK 才需要，见 `mobile\README.md`）。

### 1.2 找到电脑的局域网 IP

启动脚本会自动打印，也可以手动看：

```powershell
python tools\make_dev_cert.py --detect-only
# 输出：局域网 IP: 172.20.10.7
```

手机必须和电脑在**同一个 Wi-Fi/热点**下，且能互相访问（见 §4.1 的坑）。

### 1.3 放行防火墙（第一次需要）

Windows 第一次启动 uvicorn 会弹「Windows 安全中心警报」，勾选**专用网络**并点「允许访问」。
如果没弹/误点了拒绝，用管理员 PowerShell 手动加规则：

```powershell
# 管理员身份运行
netsh advfirewall firewall add rule name="videoshoot https 8443" dir=in action=allow protocol=TCP localport=8443
netsh advfirewall firewall add rule name="videoshoot http 8000"  dir=in action=allow protocol=TCP localport=8000
```

---

## 2. 电脑端：启动 HTTPS 服务（手机真机用这个）

```powershell
cd D:\videoshoot
powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1
```

脚本会做三件事，看到下面这些输出说明正常：

```
=== AI 拍摄指导 · 手机 HTTPS 启动 ===
[1/3] 准备开发证书…
[certs] 复用现有证书（剩余 396 天，SAN 覆盖 5 项）
[certs] 局域网 IP：172.20.10.7
[2/3] 探测局域网 IP…
[3/3] 检查端口并启动…

手机访问地址（同一 Wi-Fi）：
  本机自检 : https://127.0.0.1:8443
  手机打开 : https://172.20.10.7:8443
...
INFO:     Uvicorn running on https://0.0.0.0:8443 (Press CTRL+C to quit)
```

- **这个窗口要一直开着**（服务就在里面跑）；关掉窗口/按 `Ctrl+C` 服务就停了。
- 证书过期或换网络导致 IP 变了，脚本会**自动重新生成**（`tools\make_dev_cert.py`），不用手动删。
- 换端口：`$env:HTTPS_PORT=8444; powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1`
- 端口被占用：脚本会提示占用进程；换端口或先关掉占用的程序。

### 2.1 先在电脑上自检

电脑浏览器打开 <https://127.0.0.1:8443>：

- 出现证书警告 → 点「高级 → 继续前往」（电脑上只是自检，装不装 CA 都行）；
- 能看到取景界面、点「演示场景」有 5 个竖屏场景、点「给建议」出建议 → 服务正常。
- 纯接口自检：<https://127.0.0.1:8443/api/health> 返回 JSON（`ok: true`）。

### 2.2 桌面调试用 HTTP（不需要给手机用）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
# 浏览器打开 http://127.0.0.1:8000
```

`127.0.0.1` 本身是浏览器认可的"安全上下文"，所以**电脑上 http 也能开相机**；手机上的 http://电脑IP **不能**开相机（只能演示模式），所以手机必须走 §2 的 HTTPS。

---

## 3. 手机端（浏览器 / PWA）

### 3.1 前提：同一网络、能互访

- 手机和电脑连**同一个 Wi-Fi**（注意别一个连 5GHz、一个连 2.4GHz 的"隔离"网络，一般不影响，但个别路由器会）。
- **访客 Wi-Fi / 公共 Wi-Fi 常开 AP 隔离**：手机能上网但访问不到电脑，表现为一直转圈/打不开。解决：换回主 Wi-Fi，或**用手机开热点**：
  1. 手机开"个人热点"，电脑连这个热点；
  2. 重新跑 `scripts\start-https.ps1`（新网络 IP 变了，脚本会自动重生成证书并打印新的手机地址）；
  3. 手机打开新打印的地址（通常 `https://192.168.x.x:8443`）。
- 公司/校园网可能禁设备互访，同上处理。

### 3.2 第一次打开：处理证书警告

手机浏览器地址栏输入脚本打印的地址（例如 `https://172.20.10.7:8443`），会提示"你的连接不是私密连接/证书不受信任"。二选一：

#### 方式 A（推荐）：装一次开发 CA，之后不再报警、相机/安装最稳

1. 手机浏览器打开：`https://<电脑IP>:8443/api/dev/ca.pem`
   （会提示证书错误 → 先点「继续前往」，然后下载 `learnbuddy-dev-ca.pem`）
2. 安装：
   - **Android**：设置 → 安全（或"密码与安全"）→ 加密与凭据 → **安装证书 → CA 证书 → 仍然安装** → 选刚下载的 `.pem`（不同品牌路径不同，在设置里搜"CA 证书"最快；小米/OPPO 需先"从存储设备安装"）
   - **iOS**：Safari 下载后 → 设置 → 已下载描述文件 → 安装；再进 设置 → 通用 → 关于本机 → **证书信任设置** → 打开 `LearnBuddy Dev CA` 的完全信任
3. 重新打开 `https://<电脑IP>:8443`，应该不再报警。

> 说明：这是**自签开发证书**，只用于本机演示；手机如果提示"网络可能被监控"是正常的（因为你自己装了根证书）。

#### 方式 B（快速）：点「继续前往」，不装证书

- Chrome/Edge：警告页 →「高级」→「继续前往（不安全）」；Safari：「显示详细信息」→「访问此网站」。
- 能进界面，但**部分浏览器版本会因证书不受信任而限制相机（getUserMedia）和 PWA 安装**，此时会停在"演示模式"。想用真相机、想安装到桌面，请用方式 A。

### 3.3 允许相机

- 进入页面后浏览器会弹"允许使用相机？"→ **允许**。
- 顶部状态从「演示模式 · 无相机」变成 **「相机就绪」** 就说明真机相机通了。
- 若没弹窗/误点拒绝：浏览器设置 → 网站设置 → 相机 → 找到该地址改回允许；Android 还可在 系统设置 → 应用 → 浏览器 → 权限 → 相机 检查。
- 拿不到相机也没关系：点页面上的「演示场景」选一张图，后面的建议/记录/粗剪都能完整演示。

### 3.4 安装到手机桌面（PWA）

- **Android Chrome/Edge**：右上角菜单 →「安装应用」/「添加到主屏幕」；页面顶部也可能出现橙色「安装」按钮（`beforeinstallprompt` 触发时才有）。
- **iOS Safari**：分享按钮 →「添加到主屏幕」。
- 装好后从桌面图标打开是**全屏**（无地址栏），断网也能打开界面（离线壳）。

### 3.5 在手机上的完整操作流程

1. 输入框写你想要的效果（默认已填"电影感人物特写，背景虚化"），点橙色**给建议**。
2. 底部抽屉出 1–3 条建议，例如"① 往前走一步 · 主体占比 +12% · 主体偏小，观众看不清表情"。
3. 照着做（走动/换焦段/调整机位），点该条的「已按此调整」（或「有用」）。
4. 再点**给建议**刷新判断，画面改善了分数会变高。
5. 想留证据：点**记录镜头** → 顶部出现 `已记录 S0001` + 调整前/调整后缩略图（存在电脑的 `output\shots\`）。
6. 点**对比前/后** → 并排对比弹层 → 可下载 PNG。
7. 拍完点**生成粗剪** → 全屏面板：总素材/可用/剔除/重复、剔除理由、时间线（文件名/时长/转场/保留理由）、总时长；可下载 EDL JSON 与 FFmpeg `.bat`。
8. 右上角「调试」可看评分/特征/耗时（默认隐藏，答辩时可不打开）。

> 所有"记录/反馈/粗剪"的数据都在**电脑**上（手机只是操作端），换手机不影响数据；断网时建议/记录/粗剪会提示"服务未启动"或"连不上本地服务"。

### 3.6 断网/电脑服务停掉时手机是什么样

- 已经安装到桌面：**界面还能打开**（Service Worker 离线壳 + 5 个 demo 场景帧已缓存）；
- 顶部状态显示「服务未启动」，点「给建议」会提示连不上——这是预期，不是手机坏了；
- 电脑重新启动 HTTPS 服务后，手机刷新一次就好，不需要重装。

---

## 4. 安卓 APK（可选，想在手机桌面直接点图标）

APK 路径：`D:\videoshoot\mobile\shooting-guide-debug.apk`（3.62 MiB，debug 签名）。
**APK 只是壳，必须按 §2 在电脑上跑着 HTTPS 服务**，且 API 地址写死为 `https://172.20.10.7:8443`（构建时本机 IP）。

### 4.1 安装

**方式 1：数据线 + adb**

```powershell
& D:\videoshoot\_tools\android-sdk\platform-tools\adb.exe devices
& D:\videoshoot\_tools\android-sdk\platform-tools\adb.exe install -r D:\videoshoot\mobile\shooting-guide-debug.apk
```

**方式 2：传到手机点开安装**

- 微信"文件传输助手"/QQ/数据线/局域网下载都行；
- 手机上点 APK → 允许「安装未知来源应用」（小米/华为等还要在文件管理器里单独放行）；
- 如果提示"应用未安装/解析错误"，先确认文件完整（3,799,027 字节）。

### 4.2 首次启动检查清单

1. 手机和电脑同一 Wi-Fi，电脑跑着 `scripts\start-https.ps1`；
2. 打开 App「拍摄指导」→ 顶部状态应为 **「服务就绪」/「相机就绪」**；
3. 首次启动相机时系统弹「允许拍摄照片和录制视频？」→ **允许**；
4. 点「给建议」能出 3 条 → 说明 App 已成功访问电脑的 HTTPS 接口（App 内置信任开发 CA，不需要你在手机装证书）；
5. 若显示「连不上本地服务」：检查电脑服务是否在跑、手机是否同一 Wi-Fi、Windows 防火墙是否放行 8443。

### 4.3 换网络 / 换电脑后怎么改 API 地址

APK 里写死的是构建时的 `https://172.20.10.7:8443`。当前网络不是这个 IP 时，要么让电脑用回这个 IP（不现实），要么重新打包：

```powershell
# 手机热点/新 Wi-Fi 下，先看电脑新 IP
python tools\make_dev_cert.py --detect-only

# 重写地址 + 同步前端 + 重新构建 + 校验签名（一键，需要 _tools 里的工具链）
powershell -ExecutionPolicy Bypass -File mobile\scripts\build-debug.ps1 -ApiBase 192.168.1.23:8443
# 产物仍是 mobile\android\app\build\outputs\apk\debug\app-debug.apk，
# 复制/替换 mobile\shooting-guide-debug.apk 后重新安装（adb install -r 覆盖安装即可）
```

或者用 Android Studio 打开 `mobile\android` 直接 Run。

> 不重打包的临时办法：手机浏览器（同网络）打开 `https://<电脑IP>:8443` 用 PWA，功能与 APK 一样，且没有写死 IP 的问题。

### 4.4 卸载/重装

```powershell
& D:\videoshoot\_tools\android-sdk\platform-tools\adb.exe uninstall com.learnbuddy.shootingguide
```

App 本身不存业务数据（记录都在电脑 `output\`），卸载不会丢素材记录。

---

## 5. 数据在哪、怎么清、怎么换真实素材

| 内容 | 位置（电脑） | 说明 |
|---|---|---|
| 拍摄记录（闭环数据） | `output\shot_log.csv` | 前端点「记录镜头」追加一行（含 quality_score / followed_advice） |
| 调整前/后图 | `output\shots\S0001_before.jpg` / `_after.jpg` | 与记录同 clip_id |
| 用户反馈 | `output\feedback.jsonl` | 每条建议的"有用/没用" |
| 粗剪结果 | `output\edit\rough_cut.json` | 每次生成粗剪落盘 |
| 服务日志 | 控制台窗口 / `output\acceptance_https.log`（仅验收脚本） | — |

- **清空拍摄记录**（重新演示）：界面上粗剪面板切到"本次拍摄"时后端有 `/api/shots/reset`；或直接删 `output\shot_log.csv` 和 `output\shots\` 下的图。
- **用真实素材做粗剪**：把视频放进一个目录，生成清单再替换 `data\materials.csv`：

  ```powershell
  python tools\make_materials.py --dir D:\我的素材 --out data\materials.csv
  ```
  然后在界面上「生成粗剪」时素材源选"示例素材"（读的就是 `data/materials.csv`）。

---

## 6. 常见问题排查

| 症状 | 最可能的原因 | 解决 |
|---|---|---|
| 手机打不开 `https://IP:8443`（一直转圈/超时） | 不在同一网络 / 访客网络 AP 隔离 / 防火墙拦截 | 同一 Wi-Fi；手机热点方案；放行 8443（§1.3） |
| 手机能打开但一直是「演示模式 · 无相机」 | ① 页面是 http ② 证书未信任 ③ 相机权限被拒 | 用 HTTPS 地址；装 CA（§3.2 方式 A）；允许相机权限 |
| 电脑浏览器 `127.0.0.1:8443` 打不开 | 服务没起/端口被占 | 看启动窗口是否 `Uvicorn running`；换 `HTTPS_PORT` 或关占用进程 |
| 手机里"给建议"提示连不上 | 服务停了 / IP 变了 | 重启 `start-https.ps1`；核对 IP；APK 需按 §4.3 重打包 |
| 手机装不上 APK | 未知来源未开 / 文件没传完 | 允许未知来源；核对文件 3,799,027 字节 |
| APK 打开白屏/闪退 | WebView 版本过旧（Android 6 及以下风险大） | 升级系统 WebView / 用 Android 7+；或改用手机浏览器 PWA |
| APK 显示「连不上本地服务」 | 电脑没跑 HTTPS 或 IP 不对 | 电脑跑 `start-https.ps1`；手机同一 Wi-Fi；不重打包就用浏览器 PWA |
| 顶部「安装」按钮不出现 | 浏览器不支持 `beforeinstallprompt`（iOS 必现） | Android 用浏览器菜单「安装应用」；iOS 用分享「添加到主屏幕」 |
| 装到桌面后还是旧界面 | Service Worker 缓存了旧壳 | 关掉桌面图标重开；或浏览器里强制刷新；改过前端需把 `web\sw.js` 的 `VERSION` +1 |
| 断网后界面能开但没建议 | 正常（接口不缓存，必须电脑在线） | 电脑起服务后刷新 |
| PowerShell 运行脚本报"中文乱码/字符串未终止/Array index" | Windows PowerShell 5.1 读取无 BOM 的 UTF-8 脚本 | 本仓库脚本已统一 UTF-8 BOM；若你手工改过脚本，请用 `pwsh`（PowerShell 7）运行或另存为 UTF-8 BOM |
| 粗剪下载 `.bat` 后运行报找不到 ffmpeg | 本机没装 ffmpeg（这是已知边界） | 在装了 ffmpeg 的机器上运行（`winget install ffmpeg` 或官网下载） |
| 证书报"已过期/不匹配" | 换了网络导致 IP 变了 | 直接重跑 `start-https.ps1`（会自动重生成证书），必要时 `python tools\make_dev_cert.py --force` |

---

## 7. 安全与发布注意（别直接拿去公网）

1. `certs\` 里含**私钥**（`ca-key.pem`、`server-key.pem`），只在本机开发用，**不要提交仓库/发给别人**。
2. 开发 CA 是自签的，只用于局域网演示；正式发布必须换成有公网证书的 HTTPS 域名，并删除 App 里对 `@raw/lb_dev_ca` 的信任。
3. HTTPS 服务监听 `0.0.0.0`，同一局域网的人都能访问（接口无鉴权）；在公共 Wi-Fi 演示完记得 `Ctrl+C` 关掉。
4. APK 是 **debug 签名**，只能内部试用；上架/分发要按 `mobile\README.md` §7 配 release 签名（keystore 密码别写进仓库）。
5. 手机端不上传任何数据到外网：所有请求只到你自己电脑上的 `8443`（除非你在 `app-config.js` 改了地址）。

---

## 8. 命令速查

```powershell
# 电脑
cd D:\videoshoot
powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1   # 手机真机（HTTPS 8443）
powershell -ExecutionPolicy Bypass -File scripts\start.ps1         # 桌面调试（HTTP 8000）
python tools\make_dev_cert.py --detect-only                        # 看局域网 IP
python tools\make_dev_cert.py --force                              # 强制重生成证书
$env:HTTPS_PORT=8444; powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1  # 换端口
python -m unittest discover -s tests                               # 自检（129 项）

# 手机（APK）
& D:\videoshoot\_tools\android-sdk\platform-tools\adb.exe install -r D:\videoshoot\mobile\shooting-guide-debug.apk
powershell -ExecutionPolicy Bypass -File mobile\scripts\build-debug.ps1 -ApiBase 192.168.1.23:8443
```

| 地址 | 用途 |
|---|---|
| `http://127.0.0.1:8000` | 电脑自己看（相机可用） |
| `https://127.0.0.1:8443` | 电脑自检 HTTPS |
| `https://<电脑IP>:8443` | **手机真机/PWA 用这个** |
| `https://<电脑IP>:8443/api/dev/ca.pem` | 手机下载开发 CA |
| `http://<电脑IP>:8000` | 手机快速看界面（无相机，仅演示模式） |
