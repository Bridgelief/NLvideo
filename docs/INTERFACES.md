# 接口契约（INTERFACES）v1.0

> 本文件是四人并行开发的唯一契约。**任何字段、路径、端口、ID 的改动都必须先改本文件并通知 Lead**，不允许私自改。
> 仓库根目录：`D:\videoshoot`。参考素材：`reference/kit/`（原始交付包）与 `reference/ai-shooting-assistant-plan.html`（方案文档），只读参考，不要修改。

---

## 0. 分工与写域（不要越界写别人的目录）

| 目录/文件 | 归属 | 内容 |
|---|---|---|
| `engine/` | **algo** | 建议引擎、剪辑引擎、动作效果表、FFmpeg/EDL 导出（纯函数，禁止网络/IO 依赖外部服务） |
| `server/`、`tools/`、`scripts/` | **server** | FastAPI 服务、画面特征提取、会话与拍摄记录、demo 帧生成、启动脚本 |
| `web/` | **web** | 前端单页：取景、按钮、建议叠加、对比、粗剪面板（原生 JS，无构建步骤） |
| `data/` | **domain** | 5 类意图权重、规则库、50 条话术、剪辑规则、素材清单、拉片标注表 |
| `tests/`、`docs/`、`README.md`、根目录文件 | **lead** | 契约测试、验收脚本、文档、集成 |

- 只写自己目录下的文件；需要别人改的地方发消息给对应 owner / Lead。
- 所有代码文件 UTF-8（无 BOM）；Python 3.11；中文注释/文案直接写 UTF-8 中文字符。
- 禁止新增第三方依赖（环境已有：fastapi、uvicorn、pydantic、numpy、cv2、PIL、requests、httpx）。测试用标准库 `unittest`，不引入 pytest。
- 机器上**没有 ffmpeg 可执行文件**：导出只能生成命令/脚本/EDL 文件，不能真的渲染视频。代码必须检测不到 ffmpeg 时优雅降级（`available: false`），不许报错退出。

---

## 1. 目录结构（目标状态）

```
D:\videoshoot\
├── engine/
│   ├── __init__.py
│   ├── action_effects.py       # 50 个动作 ID 的特征影响估计（唯一真值表）
│   ├── suggest_engine.py       # 意图解析 + 评分 + Top-3 建议（可 import，可 CLI）
│   ├── edit_engine.py          # 素材整理 + 粗剪 EDL（可 import，可 CLI）
│   └── edl_export.py           # EDL JSON / FFmpeg 命令 / .bat 生成
├── server/
│   ├── __init__.py
│   ├── app.py                  # FastAPI app: 路由 + 静态挂载
│   ├── service.py              # 纯业务函数（便于单测，不依赖 HTTP）
│   ├── features.py             # 画面特征提取（cv2）+ 主体框/构图叠加信息
│   ├── store.py                # 会话、反馈、拍摄记录落盘（output/）
│   └── static/demo/            # demo 帧 jpg + scenarios.json（由 tools 生成）
├── web/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── data/
│   ├── intent_weights.json
│   ├── rules.json
│   ├── phrases.json
│   ├── edit_rules.json
│   ├── materials.csv           # 素材清单（≥13 行，含废片/重复/低质量样本）
│   ├── shots.csv               # 拉片标注表（≥90 行，字段见 §3.6）
│   └── README.md               # 数据说明
├── tools/
│   ├── make_demo_frames.py     # 生成 demo 帧 + scenarios.json
│   ├── make_materials.py       # 扫描目录生成 materials.csv（可选）
│   └── edl_to_ffmpeg.py        # CLI：读 EDL JSON 输出 ffmpeg 命令/脚本
├── scripts/
│   ├── start.ps1               # 启动服务（唯一推荐入口）
│   └── start.bat               # cmd 版
├── tests/                      # lead
├── docs/                       # lead
├── reference/                  # 只读参考包（plan html + kit）
└── README.md                   # lead（快速启动 + 验收对照）
```

运行时生成（不入 git，代码要能自动建目录）：`output/feedback.jsonl`、`output/shot_log.csv`、`output/shots/*.jpg`、`output/edit/*.json`。

---

## 2. 引擎接口（Python，algo 负责）

### 2.1 `engine/action_effects.py`

```python
ACTION_EFFECTS: dict[str, dict]   # 50 条，键为动作 ID
```

影响字段只有四个：`r`(主体占比增量)、`c`(背景复杂度增量)、`l`(光线方向替换值)、`g`(视线空间增量)。数值为 v1 近似，可调。

**规范 50 个动作 ID**（`phrases.json`、`rules.json` 只能引用这些 ID；引擎必须校验不一致并写入 `warnings`）：

```
step_back, step_back_more, step_forward, step_forward_close, step_left, step_right,
orbit_subject, back_to_wall, longer_focal, widest, medium_focal, zoom_in_slow,
zoom_out_context, avoid_wide_face, telephoto_compress, lower_camera, raise_camera,
eye_level, crouch_down, knee_level, slightly_above, ground_level, dutch_angle,
front_on, back_view, over_shoulder, low_angle_up, high_angle_down,
side_light, back_light, front_light, golden_hour, avoid_backlight, move_to_window,
shade_subject, shift_subject_third, shift_right_third, center_subject, gaze_room,
lower_horizon, raise_horizon, leave_negative_space, frame_within_frame,
clean_background, remove_clutter, clean_edge, change_angle_bg,
step_for_separation, blur_background, slow_push
```

> 与原始 kit 的差异（**已定稿，不要再改**）：原 kit 的 `phrases.json` 有 `side_45`（`ACTION_EFFECTS` 里没有对应效果，是死条目）且缺 `slow_push`；本轮**删除 `side_45`、补充 `slow_push`**，使动作表、话术、规则三者严格对齐为 50 条。
> `ACTION_EFFECTS` 的键 = 唯一真值：`phrases.json` 必须与它**完全一一对应**（不多不少，50 条）；`rules.json` 只能引用其中 ID。`engine.load_resources()` 负责校验。

### 2.2 `engine/suggest_engine.py`

```python
DATA_DIR = <repo>/data

def load_resources(data_dir: str | Path | None = None) -> dict:
    """返回 {"intent_data","rules_data","phrases_data","phrase_map","warnings":[...]}，带进程内缓存（按 mtime 失效）。"""

def suggest(intent_text: str,
            features: dict,
            resources: dict | None = None) -> dict:
    """纯函数。intent_text 可为空；features 见下表。返回 §2.3 结构，永不抛异常（数据坏时降级）。"""

def score_state(features_rclg: dict, intent: dict) -> dict:
    """{total, components}，按 docs 里的四个评分函数（kit 的 math_definition.md 第 4 节）。"""
```

输入 features（键名固定，允许缺省）：

| 键 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `subject_ratio` | float [0,1] | 0.30 | 主体占比；兼容别名 `r` |
| `bg_complexity` | float [0,1] | 0.30 | 背景复杂度；兼容别名 `edge_density`、`c` |
| `light_direction` | str | `"front"` | front/side/back/low/golden，其它值按"不合目标"计分 |
| `gaze_space` | float [0,1] | 0.30 | 视线留白；兼容别名 `g` |

### 2.3 `suggest()` 返回结构（= `POST /api/suggest` 的主体字段）

```json
{
  "intent_detected": "portrait_closeup",
  "intent_label": "人像特写",
  "features": {"subject_ratio": 0.18, "bg_complexity": 0.42, "light_direction": "front", "gaze_space": 0.30},
  "current_score": {"total": 0.693, "components": {"subject_ratio": 0.62, "bg_complexity": 0.71, "light_angle": 0.4, "gaze_space": 0.58}},
  "primary_gap": {"field": "subject_ratio", "weighted_gap": -0.18, "direction": "low"},
  "suggestions": [
    {
      "rank": 1,
      "action_id": "step_forward",
      "action": "往前走一步",
      "delta": "主体占比 +12%",
      "expected_gain": 0.152,
      "reason": "主体占比偏低，观众看不清表情"
    }
  ]
}
```

- `suggestions` 长度 1–3（无规则命中时必须走兜底：`intent.preferred_actions` 或按 `primary_gap.field` 映射默认动作），**不允许为空**（除非数据文件缺失，此时返回 `suggestions: []` 且 `warnings` 里说明）。
- 排序：`expected_gain` 降序 → `priority` 降序 → `action_id` 升序（保证确定性，同输入同输出）。
- `primary_gap` 无维度差距时可为 `null`。
- `intent_text` 为空/无关键词命中 → 默认 `portrait_medium`。
- 必须能被 `python -m engine.suggest_engine --intent "..." --ratio 0.18 --bg 0.42 --light front --gaze 0.3` 调用（保留原 kit CLI 参数），并支持 `--stdin` 读 `{"intent":...,"frame_features":{...}}`。

### 2.4 `engine/edit_engine.py`

```python
def load_edit_rules(path=None) -> dict
def load_materials(source) -> list[dict]   # source: Path | 文件名(相对 data/) | list[dict] | None(默认 materials.csv)
def organize(materials: list[dict], rules: dict) -> tuple[list, list, list]   # usable, excluded, duplicates
def build_rough_cut(usable: list[dict], rules: dict) -> dict
def run(materials=None, rules=None, data_dir=None) -> dict
# 返回 {"organization": {...}, "rough_cut": {...}, "source": "materials.csv", "warnings": [...]}
```

- 素材行字段（`data/materials.csv` 与 `output/shot_log.csv` 同构，`output/shot_log.csv` 额外多 `before_url,after_url` 两列，引擎忽略额外列）：
  `clip_id, filename, duration_sec, scene_type, intent_id, quality_score, stability, focus_ok, has_mistake, duplicate_of, followed_advice, in_point, out_point, notes`
- 行为沿用 kit：`duplicate_of` 非空 → duplicates；命中 `quality_filters`（按顺序首个命中）→ excluded；否则 `priority_score = quality_score + Σpriority_bonus` → usable。
- 排序：先按 `rules["ordering"]["intent_order"]` 分组，组内按原始行序；截断 `selection.max_clips`。
- 入出点：`in_point + trim_head_sec`，`out_point - trim_tail_sec`，夹在 `[min_duration_sec, max_duration_sec]`，且 `out > in + 0.5`。
- 转场：由 `rules["transitions"]` 驱动，缺省回退 kit 逻辑。schema：
  ```json
  "transitions": {
    "after_intent": "emotional",
    "after_intent_transition": "淡入淡出",
    "same_scene_type_transition": "硬切",
    "default_transition": "叠化",
    "reason_templates": {"淡入淡出": "情绪镜头后留呼吸", "硬切": "同一场地硬切最自然", "叠化": "场景或镜头类型变化用叠化过渡", "无": "开篇镜头"}
  }
  ```
- `rough_cut` 结构（沿用 kit `edit_logic.md` 第 6 节）：
  ```json
  {"clip_count": 5, "total_duration_sec": 18.4,
   "timeline": [{"order": 1, "clip_id": "C001", "filename": "shot_001.mp4", "in": 0.7, "out": 5.5,
                 "duration_sec": 4.8, "timeline_in": 0.0, "transition_in": "无",
                 "priority": 0.87, "reason": "按建议调整过；质量分 0.72；开篇镜头"}]}
  ```
- `organization` 结构沿用 kit：`{total, usable, excluded_count, duplicate_count, excluded:[{clip_id, reason, rule_id}], duplicates:[{clip_id, duplicate_of, action}], usable_ids:[...]}`。
- 同 `python engine/edit_engine.py [--materials f --rules f --json]` 可跑。

### 2.5 `engine/edl_export.py`

```python
def build_ffmpeg(timeline: list[dict], *, base_dir: str = "inputs",
                 output: str = "output/rough_cut.mp4", fps: int = 30) -> dict
def build_cmx3600(timeline: list[dict], *, fps: int = 30) -> str
def build_edl_json(rough_cut: dict, *, source: str = "materials.csv") -> dict
```

- `build_ffmpeg` 返回：
  ```json
  {"available": false, "ffmpeg_path": null,
   "single_command": "ffmpeg -y -i \"inputs/shot_001.mp4\" ... -filter_complex \"[0:v]trim=...\" -map \"[v]\" output/rough_cut.mp4",
   "batch_script": "@echo off\r\nffmpeg ...\r\n",
   "notes": ["本机未检测到 ffmpeg，命令仅供导出/在装有 ffmpeg 的机器上执行"]}
  ```
- `available` 由 `shutil.which("ffmpeg")` 决定；**检测不到也必须返回完整命令文本**，不得抛异常。
- 命令必须可被懂 ffmpeg 的人直接执行：用 `-ss/-t` 或 `trim` + `concat` 二选一，注释写清。

---

## 3. 数据契约（domain 负责）

所有 JSON：UTF-8，`indent=2`，顶层带 `"version"` 字符串。字段名用 snake_case。

### 3.1 `data/intent_weights.json`

```json
{
  "version": "1.1",
  "intents": [
    {
      "id": "portrait_closeup",
      "label": "人像特写",
      "keywords": ["特写", "人物特写", "电影感", "虚化", "情绪", "脸部", "半身"],
      "weights": {"subject_ratio": 0.40, "bg_complexity": 0.25, "light_angle": 0.20, "gaze_space": 0.15},
      "targets": {
        "subject_ratio": [0.30, 0.60],
        "bg_complexity": [0.0, 0.35],
        "light_angle": ["side", "back"],
        "gaze_space": [0.25, 0.50]
      },
      "preferred_actions": ["step_back", "longer_focal", "lower_camera", "side_light", "shift_subject_third"]
    }
  ]
}
```

- 必须恰好 5 个意图，`id` 固定为：`portrait_closeup`、`portrait_medium`、`landscape`、`product`、`emotional`。
- `weights` 四键固定且和为 1.0（±0.001）。
- `targets` 四键固定：`subject_ratio`/`gaze_space` 用 `[lo,hi]`（0≤lo<hi≤1）；`bg_complexity` 用 `[0, upper]`；`light_angle` 用非空字符串数组（取值 front/side/back/low/golden）。
- `keywords`：每类 ≥12 个中文词，覆盖真实用户说法（"想要氛围感""拍得高级点""产品细节"等），不同意图之间允许少量重叠但以最长/最多命中优先。
- `preferred_actions`：3–5 个合法动作 ID，用于兜底建议。

### 3.2 `data/phrases.json`

```json
{"version": "1.1", "phrases": [
  {"id": "step_back", "action": "后退两步", "category": "distance",
   "affects": ["subject_ratio"], "delta_hint": "主体占比下降、环境更多",
   "reason_template": "现在主体太满，退后能带出环境层次",
   "condition_hint": "subject_ratio > 目标上限"}
]}
```

- 必须与 `ACTION_EFFECTS` 的键**完全一致**（数量、ID 都一致）；`action` 是用户能照着做的一句话（≤12 字，含具体量："往左挪半步""换 3.2x""机位降到腰部"）。
- `category` 建议取值：`distance` `focal` `height` `angle` `light` `composition` `background` `movement`。
- `affects` 是受影响维度子集：`subject_ratio`/`bg_complexity`/`light_angle`/`gaze_space`。
- `delta_hint`：界面上的效果提示（如"主体占比 +12%"）。
- `reason_template`：一句人话理由（≤40 字），面向普通用户，不出现"权重/函数/评分"等词。
- 文案要求：**普通人听完不用问第二遍**（方案验收 #3）。

### 3.3 `data/rules.json`

```json
{"version": "1.1", "rules": [
  {"id": "R001",
   "condition": {"field": "subject_ratio", "op": "<", "value": 0.30},
   "applies_to": ["portrait_closeup", "portrait_medium"],
   "action_id": "step_forward",
   "reason": "主体占比偏低，观众看不清表情",
   "priority": 8}
]}
```

- `field` ∈ {subject_ratio, bg_complexity, light_angle, gaze_space}；`op` ∈ {<, <=, >, >=, ==, in, not_in}；`light_angle` 的 `value` 为字符串数组（配 `in`/`not_in`）。
- `applies_to`：意图 ID 数组，或 `["all"]`。
- `action_id` 必须是合法动作 ID。
- 覆盖要求：≥25 条，**每个意图 ≥4 条**，四个维度每个至少 4 条；同一意图同一维度可多条（不同阈值）。
- 建议覆盖 80% 常见场景：主体过小/过大、背景过乱/过空、逆光/顺光/无侧光/顶光、视线留白不足/过多。
- `priority` 1–10 整数。

### 3.4 `data/edit_rules.json`

沿用 kit，字段：`version`、`quality_filters[]`（`{id, field, op, value, action:"exclude", reason}`）、`priority_bonus[]`（`{id, field, op, value, bonus, reason}`）、`selection{max_clips, min_duration_sec, max_duration_sec, default_duration_sec, trim_head_sec, trim_tail_sec}`、`ordering{strategy, intent_order[], keep_shooting_order_within_intent}`、`transitions{...}`（schema 见 §2.4）。

- `intent_order` 必须包含全部 5 个意图 ID。
- `quality_filters` ≥5 条，至少覆盖：`stability < 0.45`、`focus_ok == 0`、`has_mistake == 1`、`duration_sec < 1.0`、`quality_score < 0.5`。
- `priority_bonus` 至少 2 条，其中必须有 `followed_advice == 1 → bonus 0.15`（闭环验收 #7）。

### 3.5 `data/materials.csv`

表头固定（UTF-8，逗号分隔）：
`clip_id,filename,duration_sec,scene_type,intent_id,quality_score,stability,focus_ok,has_mistake,duplicate_of,followed_advice,in_point,out_point,notes`

- ≥13 行，覆盖：正常可用、抖动废片(stability<0.45)、失焦(focus_ok=0)、有失误(has_mistake=1)、重复(duplicate_of 指向某 clip_id)、远景/产品/情绪等不同意图；至少 5 行 `followed_advice=1` 且 `quality_score≥0.7`。
- `filename` 指向不存在的素材也没关系（只做 EDL 演示），但必须像真实文件名（`shot_001.mp4`）。
- 数值范围：`quality_score/stability ∈ [0,1]`，`in_point < out_point ≤ duration_sec`。

### 3.6 `data/shots.csv`

表头固定：`id,film,scene,shot_type,shot_size,subject_ratio,camera_height,focal_estimate,light_direction,gaze_space,bg_complexity,why_this_shot,ai_common_mistake,notes`

- ≥90 行（方案要求 30 个镜头 × 3 个字段维度 → 90 条标注）。可以以 30 个镜头为骨架，每个镜头 3 行（机位/光线/构图各一行），`shot_type` 区分；数据来自真实拉片或一致的合成示例都要在 `data/README.md` 里写明来源与"模板性质"。
- `subject_ratio/gaze_space/bg_complexity ∈ [0,1]`，`light_direction` 用取值域内的值。

### 3.7 `data/README.md`

说明每份数据的字段、来源（原始 kit / 本轮补充）、覆盖情况、如何验证（`python -c` 或 CLI 命令）、与引擎的对齐方式。诚实标注哪些是模板示例、哪些是实测。

---

## 4. HTTP API 契约（server 负责实现，web/lead 按此调用）

- 基址：`http://127.0.0.1:8000`（`scripts/start.ps1` 启动；端口可用环境变量 `PORT` 覆盖，默认 8000）。
- 所有请求/响应 `Content-Type: application/json; charset=utf-8`；错误响应 `{"detail": "人话错误信息"}` + 4xx/5xx。
- 时间单位：秒（浮点）；图像坐标：归一化 `[x, y, w, h] ∈ [0,1]`。
- 允许 CORS `*`（本地演示）。
- 大图传输：`data:image/jpeg;base64,...` 字符串（前端 canvas 出图质量 0.7，长边 ≤ 1280）。

### 4.1 `GET /api/health`

```json
{"ok": true, "version": "1.0.0", "python": "3.11.9", "ffmpeg": false,
 "counts": {"intents": 5, "rules": 28, "phrases": 51, "materials": 13},
 "data_dir": "D:\\videoshoot\\data", "time": "2026-11-22T22:40:00+08:00"}
```

### 4.2 `GET /api/demo`

无相机时的演示场景（server 用 `tools/make_demo_frames.py` 生成帧并抽取 `expected_features`）：

```json
{"scenarios": [
  {"id": "backlit_portrait", "label": "逆光人像 · 主体偏小",
   "intent": "电影感人物特写，背景虚化",
   "frame_url": "/static/demo/backlit_portrait.jpg",
   "expected_features": {"subject_ratio": 0.17, "bg_complexity": 0.55, "light_direction": "front", "gaze_space": 0.18}}
]}
```

- ≥4 个场景，覆盖：逆光人像、背景杂乱人像、产品静物、风光/情绪。
- `frame_url` 必须是可 GET 的真实文件（`server/static/demo/` 挂 `/static`）。

### 4.3 `POST /api/suggest`

请求（三者择一：`frame_features` 优先，其次 `frame_base64`，否则 400）：

```json
{"session_id": "web-abc123",
 "intent": "电影感人物特写，背景虚化",
 "frame_features": {"subject_ratio": 0.18, "bg_complexity": 0.42, "light_direction": "front", "gaze_space": 0.30},
 "frame_base64": null}
```

响应 = `suggest()` 结果（§2.3）**加上**：

```json
{"session_id": "web-abc123",
 "frame_features": {"subject_ratio": 0.18, "bg_complexity": 0.42, "light_direction": "front", "gaze_space": 0.30},
 "overlay": {"subject_box": [0.32, 0.22, 0.30, 0.46], "subject_box_confidence": 0.62,
             "thirds": true, "horizon_y": 0.52},
 "timing_ms": {"extract": 12, "engine": 2, "total": 15}}
```

- `overlay.subject_box` 无法估计时可为 `null`（前端必须容错）。
- 特征提取限时：长边缩到 ≤1280 再算；总耗时目标 < 1.5s（不含网络）。
- `framing` 说明：特征提取是**启发式近似**（Haar 人脸/中心先验 + 边缘密度 + 亮度梯度 + 主体位置），必须在 `server/features.py` 顶部注释和 `/api/health` 里诚实说明，不要假装是 MediaPipe。

### 4.3.1 内部可测函数（Lead 的 tests 直接调用，不经过 HTTP）

`server/features.py` 必须提供：

- `extract(image_bytes: bytes) -> dict`：返回 `{"frame_features": {...}, "overlay": {...}, "focus_ok": 0|1, "warnings": [...]}`；坏图/空字节 → 返回默认特征 + warnings，**不抛异常**。
- `decode_image(data_url_or_b64: str) -> np.ndarray | None`：同时接受纯 base64 与 `data:image/jpeg;base64,...` 前缀；失败返回 `None`。

`/api/suggest` 的 `frame_base64` 字段同样必须同时接受这两种写法。

### 4.4 `POST /api/feedback`

```json
{"session_id": "web-abc123", "action_id": "step_forward", "rank": 1,
 "useful": true, "intent_id": "portrait_closeup"}
```

响应：`{"ok": true, "recorded": 4, "path": "output/feedback.jsonl"}`（追加一行 JSONL）。

### 4.5 `POST /api/shots`（拍中落库 → 闭环数据）

```json
{"session_id": "web-abc123",
 "intent": "电影感人物特写，背景虚化",
 "intent_id": "portrait_closeup",
 "quality_score": 0.71,
 "followed_advice": true,
 "advice_action_ids": ["step_forward", "longer_focal"],
 "duration_sec": 6.0,
 "frame_features": {"subject_ratio": 0.32, "bg_complexity": 0.30, "light_direction": "side", "gaze_space": 0.34},
 "before_image": "data:image/jpeg;base64,...",
 "after_image": "data:image/jpeg;base64,...",
 "filename": null}
```

响应：

```json
{"ok": true, "clip_id": "S0001", "count": 1,
 "shot_log": "output/shot_log.csv",
 "before_url": "/output/shots/S0001_before.jpg",
 "after_url": "/output/shots/S0001_after.jpg",
 "row": {"clip_id": "S0001", "filename": "S0001.mp4", "duration_sec": 6.0,
         "scene_type": "outdoor", "intent_id": "portrait_closeup", "quality_score": 0.71,
         "stability": 0.9, "focus_ok": 1, "has_mistake": 0, "duplicate_of": "",
         "followed_advice": 1, "in_point": 0.3, "out_point": 5.7, "notes": "..."}}
```

- 写入 `output/shot_log.csv`（表头同 §3.5）+ `output/shots/<clip_id>_before.jpg` / `_after.jpg`；`/output` 挂静态目录。
- 服务端兜底：`stability` 默认 0.9（客户端可传 `stability` 覆盖）、`focus_ok` 由拉普拉斯方差估计（阈值可调，注释说明）、`has_mistake` 默认 0；`scene_type` 默认 `outdoor`。
- **客户端可覆盖** `stability` / `focus_ok` / `has_mistake`（缺省才用服务端估计）——前端与验收脚本会用到，服务端必须接受这三个可选字段。
- `clip_id` 递增三位数，重启后从现有 `shot_log.csv` 最大值续号。

### 4.6 `GET /api/shots`

`{"count": 3, "clips": [{"clip_id": "S0001", "intent_id": "portrait_closeup", "quality_score": 0.71, "followed_advice": 1, "before_url": "/output/shots/S0001_before.jpg", "after_url": "/output/shots/S0001_after.jpg"}]}`

### 4.7 `POST /api/shots/reset`

清空 `output/shot_log.csv` 与 `output/shots/` 下的图片 → `{"ok": true, "cleared": 3}`。用于演示重来。

### 4.8 `GET /api/materials`

`{"source": "data/materials.csv", "count": 13, "rows": [ ... §3.5 行 ... ]}`

### 4.9 `POST /api/edit`

请求：`{"source": "materials"}` 或 `{"source": "shots"}`（`shots` = 用 `output/shot_log.csv`；空时自动回退 `materials` 并在 `warnings` 说明）。

响应：

```json
{"ok": true, "source": "materials",
 "organization": {...},
 "rough_cut": {...},
 "ffmpeg": {"available": false, "single_command": "ffmpeg ...", "batch_script": "@echo off\r\n...", "notes": ["..."]},
 "edl": {"version": 1, "source": "materials.csv", "timeline": [...]},
 "warnings": []}
```

### 4.10 `GET /api/edit/export/rough_cut.json` / `.bat`

- `rough_cut.json`：`Content-Disposition: attachment; filename="rough_cut.json"`，内容 = `{"edl": ..., "rough_cut": ..., "organization": ...}`。
- `rough_cut.bat`：`text/plain`，内容 = `ffmpeg.batch_script`（以 `@echo off` 开头）。
- 允许在 `/api/edit` 之后再取；实现可以每次重新计算（幂等）。

### 4.11 `GET /api/dev/ca.pem`（开发证书，给手机装信任）

- 返回 `certs/ca.pem`（`application/x-pem-file`，`Content-Disposition: attachment; filename="learnbuddy-dev-ca.pem"`）。
- 只暴露 CA 证书，**绝不暴露 `ca-key.pem` / `server-key.pem`**。
- 证书不存在 → 404 `{"detail: "开发证书还没生成：先运行 python tools/make_dev_cert.py"}`。
- 用途：手机浏览器打开 `https://<电脑IP>:8443/api/dev/ca.pem` 下载后安装为 CA，之后 https 不再报证书错误（步骤见 README §2「手机真机」）。

### 4.12 静态资源

- `/` → `web/index.html`；`/web/*` → `web/`（`web` 目录整体挂载，别用相对路径踩坑）。
- `/static/*` → `server/static/`；`/output/*` → `output/`（不存在时自动创建空目录）。
- 未命中路由：`/` 返回前端，`/api/*` 返回 404 JSON。

---

## 5. 前端契约（web 负责）

纯原生 JS，无构建、无 CDN 依赖（离线可用），中文界面。

### 5.1 布局（单页，三段）

1. 顶部：标题 + 状态徽标（`#status`：相机就绪 / 演示模式 / 服务异常）+ 调试开关 `#debug-toggle`。
2. 取景区：`<video id="video" playsinline muted>` + `<canvas id="overlay-canvas">`（叠加主体框/三分线，drawImage 等比贴合并做镜像处理）+ 建议列表 `#suggestion-list`（卡片：序号、`action`、`delta`、`reason`、两个小按钮"有用/没用"、"已按此调整"）。
3. 底部操作条：`#intent-input`（预填示例）+ 主按钮 `#btn-suggest`（唯一醒目的按钮："给建议"）+ `#btn-record`（记录镜头）+ `#btn-compare`（对比前/后）+ `#btn-finish`（结束拍摄，生成粗剪）。

   > 验收 #1 要求"打开后画面只有一个按钮、一个输入框、一个建议区"：**主按钮唯一高亮**，其余按钮弱化为文字按钮/收进底部轻量条，把 `#btn-suggest` 做成视觉主体。

4. 粗剪面板 `#panel-cut`（默认折叠）：整理结果（总素材/可用/剔除/重复 + 剔除理由列表）、时间线表格（序号/文件名/时长/转场/保留理由）、总时长、下载按钮（`a#dl-edl` `a#dl-bat`）、"用示例素材 / 用本次拍摄"切换（`#cut-source`）。

### 5.2 必须存在的元素 ID（lead 的验收测试会检查）

`#video` `#overlay-canvas` `#intent-input` `#btn-suggest` `#suggestion-list` `#btn-record` `#btn-compare` `#btn-finish` `#panel-cut` `#status` `#debug-toggle` `#debug-info` `#demo-scenarios` `#cut-source` `#dl-edl` `#dl-bat`

另外两条**可测性硬要求**（Lead 的验收测试会断言）：

- `#btn-suggest` 必须带属性 `data-primary="true"`，且全页**只有它**有该属性（对应验收 #1"只有一个按钮"）。
- `#cut-source` 的 `<option value>` 必须恰好是 `materials` 与 `shots`。

### 5.3 行为

1. 启动：`GET /api/health`；并行 `navigator.mediaDevices.getUserMedia({video:{width:1280,height:720}})`。相机失败/拒绝 → 演示模式：`GET /api/demo` 渲染 `#demo-scenarios` 缩略图，点选后读取 `frame_url`。
2. 点 `#btn-suggest`：
   - 相机模式：canvas 抓当前帧 → `frame_base64`（JPEG 0.7，长边≤1280）；
   - 演示模式：把选中场景图 fetch 成 dataURL 一起 POST（走真实特征提取），若 3 次失败则回退传 `frame_features` = 场景 `expected_features`；
   - 3 秒超时/失败 → 状态条提示，保留上一次建议。
   - 渲染建议卡片；`overlay` 存在时在 canvas 上画主体框（橙色 1.5px）与三分线（低透明度虚线）；60fps 循环重绘以跟随视频。
   - 记录 `beforeDataUrl`（当次抓帧），供对比。
   - 调试开关打开时在 `#debug-info` 显示 `current_score`、`frame_features`、`timing_ms`、`primary_gap`（验收 #3：**默认不显示分数/坐标**）。
3. 点卡片"已按此调整"→ 本地标记 `adjusted=true`、当前建议标记已采纳（不影响抓帧）；"有用/没用"→ `POST /api/feedback`，`useful` 对应 true/false。
4. 点 `#btn-record`：抓当前帧作为 `after`，`POST /api/shots`（`quality_score` 取最近一次 `current_score.total`，`followed_advice` = `adjusted`，`advice_action_ids` = 采纳过的动作 ID）；成功后就地显示 before/after 缩略图与 `clip_id`，`#btn-compute`…
5. 点 `#btn-compare`：把 `beforeDataUrl` 与当前帧并排画到临时 canvas（带"调整前 / 调整后"文字标签），支持下载 PNG（验收 #4）。无 before 时提示"先点一次给建议"。
6. 点 `#btn-finish`：展开 `#panel-cut`，`POST /api/edit`（`source` 取 `#cut-source` 的值，默认 `materials`），渲染整理结果 + 时间线（含每条 reason）+ 总时长；`#dl-edl` href=`/api/edit/export/rough_cut.json`，`#dl-bat` href=`/api/edit/export/rough_cut.bat`；切换素材源后重新请求。
7. 全局：无任何 `alert()`；错误统一顶部 toast（3 秒自动消失）；按钮请求中禁用并显示"处理中…"；移动端竖屏可用（宽度 <760px 时建议卡片堆叠、取景区 16:9）。

### 5.4 视觉

深色取景界面（近黑底）+ 橙色强调（方案 HTML 的 `--orange:#E8752C`），卡片圆角 10px，字体 `system-ui, "Microsoft YaHei"`，无第三方字体/图标库（用 SVG inline 或字符）。

---

### 5.5 手机端形态（v1.2 起：这是"手机软件"）

- 页面是**手机 App 形态**：`.phone > .screen` 外壳，手机浏览器里铺满 100dvh（含 `env(safe-area-inset-*)`），桌面浏览器里居中渲染成一台 390×844 的手机供录屏/答辩。
- 取景区**全屏**（相机 `object-fit:cover`；演示场景用 `contain` 完整显示竖屏 demo 帧，不裁构图），顶部悬浮状态栏，底部固定操作条（输入框 + 唯一主按钮 + 三枚文字按钮），建议为**底部抽屉**（最大 44vh，可滚动），粗剪面板为**全屏 sheet**。
- 触控规范：主按钮/输入框 ≥52px 高，卡片操作按钮 ≥34px，禁止 `alert()`；窄屏（≤480px）时粗剪时间线由表格改为**卡片式**（`td` 用 flex 重排，隐藏表头）。
- PWA 元信息：`web/manifest.json` + `web/icon.svg`（`display:standalone`、`orientation:portrait`），`<meta name="apple-mobile-web-app-*">` 齐全，可"添加到主屏幕"后全屏使用。
- demo 帧必须是**竖屏 720×1280**（`tools/make_demo_frames.py`），横屏帧在手机竖屏里裁切会毁构图。
- 相机不可用时的兜底不变：`GET /api/demo` → 场景缩略图条 → 点选后把该图当当前画面。

---

### 5.6 PWA / HTTPS / 跨源部署（v1.3 起）

**文件与职责**

| 文件 | 职责 |
|---|---|
| `web/manifest.json` | PWA 可安装性：`display:standalone`、`orientation:portrait`、`start_url:"./"`、图标 192/512/maskable（PNG 必须真实存在且尺寸匹配）、`shortcuts` 可选 |
| `web/sw.js` | Service Worker：离线 App Shell + demo 场景缓存；**接口不缓存**（`/api/*` 只走网络，`/api/demo` 网络优先 + 离线兜底）；改前端静态文件必须把 `VERSION` 加一 |
| `web/app-config.js` | **必须**在 `app.js` 之前加载，默认 `window.__LB_API_BASE__ = ""`（同源）；Capacitor/分体部署时改为 `window.__LB_API_BASE__ = "https://<电脑IP>:8443"` |
| `web/icon-{180,192,512}.png`、`icon-maskable-512.png` | 由 `tools/make_app_icons.py` 生成（幂等） |
| `#btn-install` | 安装入口：默认 `hidden`；`beforeinstallprompt` 触发后显示；**不得**带 `data-primary`（唯一主按钮仍是 `#btn-suggest`） |
| `certs/` | 开发证书：`ca.pem`（手机装信任）、`server.pem`/`server-key.pem`（uvicorn TLS）、`ca-key.pem`（仅本机，勿外传） |
| `scripts/start-https.ps1` / `.bat` | 手机真机启动：生成/复用证书 → 探测局域网 IP → `uvicorn --host 0.0.0.0 --port 8443 --ssl-keyfile certs/server-key.pem --ssl-certfile certs/server.pem`；端口用 `HTTPS_PORT` 覆盖 |

**API 基址规则（`app.js`）**

- 优先级：`window.__LB_API_BASE__`（构建注入）> URL `?api=https://...`（会写入 `localStorage.lb_api_base`）> `localStorage.lb_api_base` > 空（同源）。
- 所有 `/api/*` 请求、demo 场景图（`/static/demo/*.jpg`）、粗剪导出链接都必须经 `apiUrl(path)` 拼接；不得在代码里硬编码主机。
- 跨源时后端已开 CORS `*`；HTTPS 服务用自签 CA，Android App 内置信任该 CA（Capacitor 工程里 `network_security_config`）。

**Service Worker 规则**

- 只在安全上下文注册（https / localhost / Capacitor 的 `https://localhost`）；http 局域网访问时不注册。
- 预缓存 App Shell：`/`、`/index.html`、`/app.js`、`/app-config.js`、`/styles.css`、`/manifest.json`、`/icon.svg`、`icon-192/512/180`；demo 帧单独缓存。
- 导航请求网络优先、断网回退缓存；`/api/*`（除 `/api/demo`）不缓存，避免给用户过期建议。

**验收测试钩子（Lead 的 tests 依赖）**

- `tests/test_pwa.py`：manifest 字段/图标尺寸、sw.js 生命周期与预缓存清单、`app-config.js` 加载顺序、`#btn-install`、`__LB_API_BASE__`/`apiUrl` 存在。
- `tests/test_dev_cert.py`：证书 SAN 含 localhost/127.0.0.1/手动 IP、CA 签名校验、真实 TLS 握手（CA 信任成功 / 默认信任库拒绝）、幂等与 `--force`。
- `tests/acceptance_https_pwa.py`：真实 HTTPS 服务 + Playwright。**注意**：`playwright` 的 `ignore_https_errors` 只放行页面导航，Service Worker 脚本抓取仍会失败；必须用 `--ignore-certificate-errors-spki-list=<服务器证书公钥 SPKI 指纹>` 模拟"证书已受信"。可安装性检查必须用持久化 profile（无痕上下文会报 `in-incognito`）。

---

## 6. 启动与运行

```powershell
# 桌面开发（HTTP，相机可用因为 127.0.0.1 是安全上下文）
powershell -ExecutionPolicy Bypass -File D:\videoshoot\scripts\start.ps1
# 手机真机（HTTPS，同一 Wi-Fi；脚本会打印手机访问地址）
powershell -ExecutionPolicy Bypass -File D:\videoshoot\scripts\start-https.ps1
```

- `scripts/start.ps1`：`Set-Location <repo>`、`$env:PYTHONPATH=<repo>`、`python -m uvicorn server.app:app --host 127.0.0.1 --port 8000`；启动前打印 Python/端口/数据文件检查结果；端口占用时给出明确提示。
- `scripts/start-https.ps1`：先跑 `python tools/make_dev_cert.py`（缺证书/局域网 IP 变了会重新生成），再以 TLS 起 `0.0.0.0:8443`；手机首次访问需装 `certs/ca.pem` 或点「继续前往」。
- 引擎自检命令（任何人可跑）：
  ```powershell
  cd D:\videoshoot
  python -m engine.suggest_engine --intent "电影感人物特写，背景虚化" --ratio 0.18 --bg 0.42 --light front --gaze 0.3
  python engine/edit_engine.py --json
  python tools/make_demo_frames.py
  python tools/make_app_icons.py
  python tools/make_dev_cert.py
  ```

---

## 7. 验收标准映射（方案第 10 节 7 条 → 本仓库）

| # | 方案验收 | 落地位置 | 验证方式（lead） |
|---|---|---|---|
| 1 | 一个按钮 + 输入框 + 建议区 | `web/index.html` | `tests/test_frontend_assets.py` 检查元素 ID/唯一主按钮 |
| 2 | 按按钮 5 秒内出建议 | `/api/suggest` + web | 契约测试 + 演示录屏脚本；API `timing_ms.total` 记录 |
| 3 | 建议 = 动作 + 理由，不显示分数 | engine `suggestions[].action/reason`；web 默认隐藏 `#debug-info` | 单元测试断言字段；前端默认折叠 |
| 4 | 调整前后对比图 | `#btn-compare` 导出 PNG | 手动/验收脚本说明 |
| 5 | 5 人试用 ≥3 人说有用 | `docs/DEMO_SCRIPT.md` 试用记录表 | Lead 提供模板，用户填 |
| 6 | 导入 5 条素材 → 自动粗剪 | `/api/edit` + `#panel-cut` | `tests/test_api.py` 断言 rough_cut 非空 |
| 7 | 按建议调整过优先保留 + 看到理由 | `priority_bonus` + timeline `reason` | `tests/test_edit.py` 断言 followed_advice 加分与 reason 含关键词 |

---

## 8. 协作纪律

1. **先读契约再动手**；契约冲突立即在共享任务里留言给 Lead，不要各写各的。
2. 每个目录交付前必须自测：引擎跑 CLI、服务跑 `python -c "from server.app import app"`、前端至少 `python -m http.server` 静态打开无 JS 报错（可用浏览器 devtools 描述，或说明无法验证项）。
3. 不要修改他人目录；跨目录需要的文件先确认是否已由 owner 创建，否则自己建在自测临时目录并说明。
4. `reference/` 只读，不得改动；`data/` 定稿后（90 条标注 + 50 条话术 + ≥25 规则）通知 Lead，Lead 跑全量测试。
5. 完成后在任务里回报：改了哪些文件、验证命令与结果、已知限制。**报告要能让我不打开文件就判断真假**（贴命令与输出摘要）。
