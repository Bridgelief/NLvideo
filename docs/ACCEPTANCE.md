# 验收报告（ACCEPTANCE）· 2026-11-22

> 对照 `reference/ai-shooting-assistant-plan.html` §10「Demo 验收标准」逐条自查。
> 结论：**7 条中 6 条已通过自动/代码级验证，1 条（5 人试用）需真人执行**。

## 0. 环境与复现命令

```powershell
cd D:\videoshoot
python -m unittest discover -s tests -v        # 131 个契约测试（含 PWA / 开发证书）
python tests\acceptance_live.py                # HTTP 端到端：起真实服务 → 11 项验收 → 停进程
python tests\acceptance_https_pwa.py           # 手机链路：HTTPS + 相机模式 + 离线壳 + 可安装性
powershell -ExecutionPolicy Bypass -File scripts\start.ps1        # 手动演示：http://127.0.0.1:8000
powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1  # 手机真机：https://<局域网IP>:8443
```

- 环境：Windows 10/11、Python 3.11.9、FastAPI 0.163 + uvicorn、OpenCV 5.0、**无 ffmpeg**；`cryptography` 用于生成开发证书。
- 记录时间：2026-11-23 11:00（Asia/Shanghai）。
- 契约测试结果：**Ran 131 tests … OK**（engine 21 / edit 15 / edl 5 / data 26 / features 9 / api 15 / frontend 12 / pwa 19 / dev_cert 9）。
- HTTP 端到端：**PASS 11 / FAIL 0**；`/api/suggest` 从抓帧到出建议 **72ms**（无相机 demo 帧）。
- 手机链路端到端：**PASS 9 / FAIL 0**（见 §3.5）。

## 1. 逐条验收

| # | 方案验收标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | 打开原型，画面里只有一个按钮、一个输入框、一个建议区 | **通过** | `tests/test_frontend_assets.py::test_single_primary_button`（`data-primary="true"` 全页仅 1 处且属于 `#btn-suggest`）；`index.html` 元素 ID 齐备；截图 [01-home](screenshots/01-home.png)。视觉上主按钮为唯一橙色块，其余为弱化文字按钮 |
| 2 | 按按钮、输入"电影感人物特写，背景虚化"，5 秒内出第一条建议 | **通过** | 端到端步骤 3：**72ms** 出建议（本机，含特征提取）；`tests/test_api.py::test_suggest_with_image_under_5_seconds` 断言 <5s |
| 3 | 建议是可执行动作 + 理由，不是分数/坐标/构图线 | **通过** | 引擎输出 `suggestions[{action, delta, reason, action_id}]`；规则/话术里禁止"权重/函数/评分/维度"字样（`test_engine` 与 `test_data_contract` 断言）；前端默认隐藏 `#debug-info`（`test_debug_hidden_by_default`），需点"调试"才显示分数 |
| 4 | 用户按建议调整后，画面更接近要求；导出"调整前 vs 调整后"对比图 | **通过（代码级，观感需人工）** | `#btn-compare` → 弹层并排渲染 + 下载 PNG（端到端步骤 4 检查 `调整前/调整后/toDataURL/download`）；截图 [04-compare](screenshots/04-compare.png) |
| 5 | 找 5 个人试用，至少 3 人说"这个建议我平时想不到，但确实有用" | **待真人执行** | 模板与通过线见 [DEMO_SCRIPT.md](DEMO_SCRIPT.md) §二；本仓库不含伪造的试用数据 |
| 6 | 拍摄后导入 5 条素材，系统自动挑可用片段并输出粗剪时间线 | **通过** | 端到端步骤 5–8：记录 5 条 shots → `POST /api/edit {source:"shots"}` → 时间线 5 条（总 24s，3 条 followed 加分）；`materials` 源：16 条 → 可用 10 / 剔除 5 / 重复 1 → 粗剪 10 条；`tests/test_api.py` 覆盖 |
| 7 | "按建议调整过"的片段在剪辑结果中被优先保留，且能看到保留理由 | **通过** | `data/edit_rules.json` `followed_advice==1 → +0.15`；时间线 `priority` 大于原质量分（`test_edit.py`、`test_api.py` 断言）；理由含"按拍摄建议调整过，通常质量更好"；截图 [05-cut-timeline](screenshots/05-cut-timeline.png) |

## 2. 端到端输出（acceptance_live.py 摘要）

```
[PASS] 0. /api/health 正常且数据齐备 —— phrases=50 rules=33 materials=16 ffmpeg=False
[PASS] 1. 单按钮 + 输入框 + 建议区（验收 #1） —— 元素齐备，data-primary 唯一
[PASS] 2. 无相机时的 demo 场景（≥4 个） —— 5 个场景，帧均可下载
[PASS] 3. 按按钮 5 秒内出动作+理由（验收 #2/#3） —— 72ms 出建议：往前走一步｜主体偏小，观众看不清表情…
[PASS] 4. 调整前后对比图（验收 #4，代码级） —— 含 before/after 合成与下载逻辑
[PASS] 5. 记录 5 条拍摄素材 + 前后图落盘（验收 #6 前半） —— S0001…S0005，前后图可访问
[PASS] 6. 自动整理 + 粗剪时间线（验收 #6 后半） —— 5 条入剪 / 24.0s；followed 优先 3 条
[PASS] 7. 按建议调整过优先保留且理由可见（验收 #7） —— 理由含"按拍摄建议调整过"
[PASS] 8. 示例素材库整理 + 粗剪（素材清单驱动） —— 16 条 / 可用 10 / 剔除 5 / 重复 1；粗剪 10 条
[PASS] 9. EDL JSON / FFmpeg .bat 可下载 —— JSON 8426B + BATCH 1321B；本机无 ffmpeg，仅导出命令
[PASS] 10. 一键清空拍摄记录（可重复演示） —— 拍摄记录已清空
结果：PASS 11 / FAIL 0 / 共 11 项
```

## 3. 补充验证

- **UI 真实渲染**：用 Edge（Playwright channel=msedge）以 390×844 手机视口 + 触摸 + 2x DPR 打开页面，走完「选场景 → 给建议（3 条卡片）→ 采纳 → 记录镜头（前后图 + "按建议调整过"）→ 对比弹层 → 生成粗剪（10 条时间线）→ 调试面板」；移动端 `documentElement.scrollWidth == 390`（无横向溢出）；桌面端手机外壳 390×844 居中。截图见 `docs/screenshots/`。
- **服务真实启动**：`scripts/start.ps1` 起 uvicorn，`/api/health` 返回 counts 齐备；端口占用时脚本提示并退出 1；测完进程树已清理（无端口残留）。

## 3.5 手机真机链路验收（HTTPS + PWA，2026-11-23）

`python tests/acceptance_https_pwa.py` 自起 HTTPS 服务（0.0.0.0:8444，TLS 用 `certs/server.pem`），
Playwright（本机 Edge）以 390×844 手机视口 + 假摄像头（`--use-fake-device-for-media-stream`）验证：

```
[PASS] 0. 开发证书 —— certs/ca.pem + server.pem（CA 签发）就绪
[PASS] 1. HTTPS 服务 + CA 校验 —— counts={intents:5, rules:33, phrases:50, materials:16}
[PASS] 2. 自签证书默认被拒（对应手机首次提示） —— 浏览器默认信任库会拦截自签证书
[PASS] 3. PWA 静态资源可访问 —— manifest/sw/图标/app-config 全部 200，manifest 可安装字段齐全
[PASS] 4. https 下真实相机链路（相机模式，非演示） —— status=「相机就绪」，video=1280x720
[PASS] 5. 相机模式按按钮出建议 —— 相机抓帧 → 3 条建议：往前走一步｜主体偏小，观众看不清表情…
[PASS] 6. Service Worker 注册并接管页面 —— 缓存：['lb-shell-v3', 'lb-data-v3']
[PASS] 7. 断网仍能打开离线壳（PWA 核心） —— 断网后 App Shell 仍打开（状态「服务未启动」）
[PASS] 8. PWA 可安装性（CDP 检查） —— Chrome 判定可安装（0 个 installability error）
结果：PASS 9 / FAIL 0 / 共 9 项
```

| 关键点 | 证据 |
|---|---|
| 证书链可用 | `requests` 用 `cafile=certs/ca.pem` 校验通过；客户端默认信任库会拒绝（对应手机首次提示） |
| HTTPS 下相机可用 | 假摄像头 → `video=1280x720`，状态「相机就绪」，按按钮出 3 条建议（截图 [07](screenshots/07-https-camera-mode.png)） |
| 离线壳 | Service Worker 接管后断网 reload，界面完整打开（截图 [08](screenshots/08-offline-shell.png)） |
| 可安装 | 用持久化 profile（无痕会报 `in-incognito`）+ CDP `Page.getInstallabilityErrors` = 0 |
| 安装入口 | 截图 07 顶部出现「安装」按钮（`beforeinstallprompt` 触发） |

**尚未在真实手机上跑过**：本机没有可连接的 Android/iOS 设备，手机端到端用 Chromium + 假摄像头 + 自签证书链等价验证；
真实手机需要用户按 README §2「手机真机」步骤操作（装 CA 或点继续 → 允许相机 → 添加到主屏幕），
这一步属于 #5 试用时一并确认。

## 3.6 Android APK 构建验收（Capacitor 套壳，2026-11-23）

本机无 node/JDK/Android SDK，全部临时装在 `_tools/`（便携工具链，约 2 GB，可删）。构建与校验结果：

| 项 | 结果 |
|---|---|
| Gradle 构建 | `BUILD SUCCESSFUL in 2m 4s`（Gradle 8.2.1 / AGP / compileSdk 34） |
| APK | `mobile/shooting-guide-debug.apk`，**3,799,027 字节**（3.62 MiB），SHA-256 `574CD494…29635AAB` |
| 包信息（aapt2） | `com.learnbuddy.shootingguide` v1.0，label「拍摄指导」，launchable `MainActivity`，minSdk 22 / targetSdk 34 |
| 权限 | `INTERNET` + `CAMERA`；`uses-feature android.hardware.camera required=false` |
| 签名（apksigner） | v1 + v2 通过，1 个签名者 `CN=Android Debug`（debug 包，不可上架） |
| 网络安全配置 | `network_security_config.xml` 信任 system + `@raw/lb_dev_ca`；`res/raw/lb_dev_ca.pem` 与 `certs/ca.pem` 逐字节一致（1208 字节） |
| 前端资产 | `assets/public/` 含 index.html / app.js / sw.js / app-config.js 等；`app-config.js` = `window.__LB_API_BASE__="https://172.20.10.7:8443"` |
| 真机运行 | ❌ **未验证**：本机没有可连手机/模拟器，只做静态校验；首次真机按 mobile/README §2 走一遍 |

> 构建坑：本机 gradle-wrapper 用 Java 直连 `services.gradle.org` 会卡在 0 字节，改用 python 预下载
> `gradle-8.2.1-all.zip` 并预置到 `GRADLE_USER_HOME` 后正常；换正常联网机器直接 `gradlew.bat` 即可。

## 4. 未通过 / 未验证项（诚实清单）

| 项 | 原因 | 影响 | 下一步 |
|---|---|---|---|
| 5 人试用（#5） | 需要真人 | 无法自动验收 | 按 DEMO_SCRIPT 表格执行 5 人试用 |
| 真实渲染 mp4 | 本机无 ffmpeg | 只能导出 EDL/命令/`.bat` | 在装 ffmpeg 的机器跑 `.bat` 或后续加渲染服务 |
| 真实手机上的相机/安装 | 本机无可连手机，用 Chromium + 假摄像头 + 自签链等价验证 | 浏览器策略差异（尤其 iOS）可能让相机/安装入口不可用 | 用户按 README §2 在真机走一遍；反馈问题再补 |
| 构图观感（#4 视觉效果） | 无人工评审 | 只验证了代码路径与截图 | 用人眼确认对比图并收集 5 人反馈 |
| 特征提取精度 | 启发式（无 Haar XML） | 建议方向正确但±误差存在 | 换 MediaPipe/云端多模态，或真实拉片校准 |
| Android APK（可选） | 需要 Node/JDK/Android SDK（本机无，临时装在 `_tools/`） | 能跑则多一个原生入口，跑不出也不影响 PWA | 见 `mobile/README.md` 的构建记录与结论 |

## 5. 失败过又修掉的问题（避免重复踩坑）

1. `web/styles.css` 缺 `#debug-info` 默认隐藏规则 → 已补 `display:none` + `:not([hidden])` 白名单，保证"界面不出现分数"。
2. 记录镜头后标记误显示"未按建议调整"（state 先清零再渲染）→ 改为请求前快照 `followed` 并透传给渲染函数。
3. demo 帧横向 4:3 在手机竖屏被 cover 裁切、构图全毁 → demo 帧改竖屏 720×1280，且演示模式叠加层用 `contain` 完整显示，不裁构图。
4. 粗剪面板 sticky 头部透出下方内容 → 改不透明背景 + 阴影；窄屏时间线表格横向滚动难用 → ≤480px 改卡片式。
5. 演示模式顶部"正在启动相机…"提示与场景条重叠 → demo 模式隐藏相机提示与角标。
6. Playwright `ignore_https_errors` 只放行页面，**Service Worker 脚本抓取仍因证书被拒**（`navigator.serviceWorker.ready` 永远 pending）→ 验收脚本改用 `--ignore-certificate-errors-spki-list=<证书公钥指纹>`，等价于"证书已被信任"，SW/相机能力真实生效。
7. PWA 可安装性检查报 `in-incognito`（Playwright 默认无痕上下文）→ 验收改用 `launch_persistent_context`（持久 profile）后为 0 error。
8. 手机 http 打开时相机一定被禁且界面无提示 → 前端检测非安全上下文时 toast 明确提示改用 `scripts/start-https.ps1`。
9. **`scripts/start-https.ps1` 没加 UTF-8 BOM** → 用 `powershell`（Windows PowerShell 5.1）运行直接解析失败（中文被打乱成乱码字符，报 `Array index expression is missing` / 字符串未终止）；补 BOM 后实测启动正常（`Uvicorn running on https://0.0.0.0:8443`，health/ca.pem/index 全 200）。已给所有 `.ps1` 加 BOM，并加回归测试 `tests/test_pwa.py::TestScriptEncoding`。`start-https.bat` 实测也能起（端口 8450 冒烟通过）。
