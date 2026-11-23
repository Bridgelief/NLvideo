# 架构说明（ARCHITECTURE）v1.0

> 契约细节看 [INTERFACES.md](./INTERFACES.md)。本文件只讲"为什么这样分层、数据怎么流、哪些是近似"。

## 1. 一句话

浏览器取景 → 抓一帧 → 服务端提特征 → 引擎算差距给 Top-3 动作 → 叠在画面上；用户按建议调整后记录"拍中"数据；拍完用同一份记录生成素材整理与粗剪 EDL。

## 2. 分层

```
┌──────────── web/（原生 JS，无构建）────────────┐
│ 取景 video / 叠加 canvas / 主按钮 / 建议卡片      │
│ 对比图导出 / 粗剪面板 / 演示模式（无相机兜底）     │
└───────────────┬────────────────────────────────┘
                │ HTTP JSON（同源）
┌───────────────▼───── server/（FastAPI）────────┐
│ app.py      路由、静态挂载、错误格式              │
│ service.py  纯业务编排（可单测，无 HTTP 依赖）     │
│ features.py 帧 → 画面特征 + 主体框（cv2 启发式）   │
│ store.py    会话 / 反馈 JSONL / shot_log.csv      │
└───────────────┬────────────────────────────────┘
                │ Python 函数调用（纯函数，无网络）
┌───────────────▼──────── engine/（决策内核）───────┐
│ suggest_engine.py 意图解析→评分→差距→动作→排序    │
│ edit_engine.py    质量筛选→意图加分→叙事排序→EDL  │
│ edl_export.py     EDL JSON / FFmpeg 命令 / .bat   │
└───────────────┬────────────────────────────────┘
                │ 读 JSON / CSV
┌───────────────▼──────── data/（知识与规则）────────┐
│ intent_weights.json  rules.json  phrases.json      │
│ edit_rules.json  materials.csv  shots.csv          │
└────────────────────────────────────────────────────┘
```

**关键约束**：`engine/` 保持纯函数（输入 dict/JSON → 输出 dict），不读摄像头、不起服务、不调外部 API。这样算法可以脱离 UI 单测、被脚本批量评估、将来换成"多模态大模型解析意图"时只替换 `parse_intent` 一层。

## 3. "拍中 → 拍后"闭环（本产品的差异点）

```
拍中：/api/suggest 返回 current_score.total + 建议 action_id
      ↓ 用户点"已按此调整"/"有用"
记录：/api/shots 把 quality_score（调整后评分）、followed_advice=1、
      advice_action_ids 写进 output/shot_log.csv（与 materials.csv 同构）
      ↓
拍后：/api/edit 用同一套 edit_engine 读 shot_log.csv
      → priority_bonus: followed_advice=1 加分 0.15
      → 时间线 reason 里明确写"按建议调整过"
```

这就是方案 §04B 说的闭环：别家只看画面质量，我们知道"用户当时想拍什么、有没有按建议改"。

## 4. 三个刻意的工程取舍（对应方案 §5）

| 取舍 | 做法 | 原因 |
|---|---|---|
| 不逐帧，采样帧 | 用户点一次按钮抓一帧（v1 不做 2–3 秒自动轮询） | 延迟/成本可控；"点一下给一次建议"本身就是方案风险 2 的兜底 |
| 不输出坐标，输出动作 | 引擎输出 `action + reason`，前端把主体框画出来只做"AI 在工作"的视觉反馈 | 3D 机位坐标在真实现场不可求；普通人要的是"退两步" |
| 端云分工 | 画面特征在本地服务端算（cv2），语义判断（意图→权重）用规则/关键词 | 原型零 API 成本、可离线演示；接大模型时只换 `parse_intent` |

## 5. 诚实的近似说明（写进代码注释与 README，不许吹）

1. **画面特征提取是启发式，不是 MediaPipe 级别的主体检测**：Haar 人脸检测（失败则中心先验）+ Canny 边缘密度 + 亮度梯度方向 + 主体位置推算。误差会在真实拍摄中暴露，v1 的目标是"粗略但方向对"，不是"像素级准确"。
2. **动作效果表（`ACTION_EFFECTS`）是人工近似**，不来自物理建模；它的作用是把"动作"换算成一个可比较的 ΔS 用于排序，具体数值可调（这份表就是调参旋钮）。
3. **动作效果对特征的线性叠加是简化**：多个动作同时执行时不做交互效应，v1 只排序单动作。
4. **`rules.json` 的规则覆盖是 80% 的常见场景，不是全部**；无规则命中时引擎用 `preferred_actions` 兜底，不保证最优。
5. **本机没有 ffmpeg**：粗剪只导出 EDL JSON / FFmpeg 命令 / `.bat`，实际渲染要在装了 ffmpeg 的机器上执行。这是本轮的边界，不是缺陷。
6. **`materials.csv` 的素材文件名不指向真实视频**：EDL 演示用。真实使用要先经 `tools/make_materials.py` 扫描素材目录生成清单（或直接拍摄后由 `/api/shots` 累积）。

## 6. 为什么不用 FastAPI 之外的东西

环境已装 fastapi/uvicorn/cv2/numpy/PIL，够用；前端原生 JS 免构建、离线可用（校赛/演示现场网络不可靠）。不引 React/Vite：三个人里只要有一个人不会构建链，原型就没人能跑起来。

## 7. 目录职责与写域

见 [INTERFACES.md §0](./INTERFACES.md)。一句话：`engine/` 归算法（algo），`server/`+`tools/`+`scripts/` 归实时工程（server），`web/` 归前端（web），`data/` 归领域规则（domain），`tests/`+`docs/`+`README.md` 归 Lead。

## 8. 手机端交付形态（v1.3）

同一套 `web/` 现在有三种打开方式，代码不分叉：

| 形态 | 入口 | 关键点 |
|---|---|---|
| 桌面开发（HTTP） | `scripts/start.ps1` → `http://127.0.0.1:8000` | `127.0.0.1` 是安全上下文，相机可用；桌面端渲染为 390×844 手机外壳 |
| 手机浏览器 / PWA | `scripts/start-https.ps1` → `https://<电脑IP>:8443` | 自签 CA + 服务器证书（`tools/make_dev_cert.py`）；Service Worker 提供离线壳；`beforeinstallprompt` → 「安装」按钮 → 主屏幕全屏 |
| Android App | `mobile/`（Capacitor 6） | WebView 加载打包进 APK 的同一份前端，`server.androidScheme=https` 使 `https://localhost` 成为安全上下文；`network_security_config` 内置信任开发 CA；API 基址由 `app-config.js` 注入 |

**跨源/跨形态的唯一开关**是 `window.__LB_API_BASE__`（或 `?api=` / `localStorage.lb_api_base`）：

```
浏览器同源：  API_BASE = ""            → /api/suggest 同源
APK / 分体：  API_BASE = "https://<电脑IP>:8443" → apiUrl('/api/suggest') 拼成绝对地址
```

因此 `app.js` 里**不允许硬编码主机**，所有请求/资源/导出链接都要过 `apiUrl()`；这条是三种形态共用同一份代码的前提。
详见 [INTERFACES.md §5.6](./INTERFACES.md)。

---

## 9. 验收映射

方案文档 §10 的 7 条验收标准在本仓库的落地位置见 [INTERFACES.md §7](./INTERFACES.md)，Lead 会用 `tests/` 逐条自动验证 1/2/3/6/7，4 由前端行为保证，5 由 `docs/DEMO_SCRIPT.md` 的试用记录表覆盖。
