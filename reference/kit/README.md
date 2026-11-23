# AI 现场拍摄指导助手 · 三人协作交付包

版本 v1.1 · 2026-11-22（新增素材自动剪辑与整理）

这是方案文档（`../ai-shooting-assistant-plan.html`）里约定的交付物骨架。所有文件都能直接用，内容需要你们按真实拉片和实测结果补充。

---

## 一、文件清单

| 文件 | 负责人 | 用途 | 当前状态 |
|---|---|---|---|
| `README.md` | Bridge | 开工指引、接口说明 | 完成 |
| `intent_weights.json` | Bridge | 5 类镜头意图 → 权重向量 | 骨架完成，关键词待补 |
| `phrases.json` | 队友 B | 50 条可执行动作话术 | 骨架完成，待校验 |
| `rules.json` | 队友 B | 规则库（条件 → 动作） | 25 条示例规则 |
| `shots.csv` | 队友 B | 拉片标注表模板 | 12 行示例，需填到 90 行 |
| `suggest_engine.py` | Bridge | 建议引擎，读上述 JSON 输出 Top-3 | 已跑通 |
| `math_definition.md` | Bridge | 目标函数一页纸定义 | 完成 |
| `edit_engine.py` | Bridge + 队友 A | 剪辑引擎，整理素材并输出粗剪 EDL | 已跑通 |
| `edit_rules.json` | 队友 B | 剪辑规则（过滤 / 加权 / 叙事弧 / 转场） | 骨架完成 |
| `materials.csv` | 队友 A | 素材清单模板 | 8 行示例需扩充 |
| `edit_logic.md` | Bridge | 剪辑逻辑、闭环说明、EDL 格式、分工 | 完成 |

---

## 二、三人开工顺序

### 你（Bridge）— 算法内核

1. 先读 `math_definition.md`，确认四个评分函数和约束跟你想的一致。
2. 打开 `intent_weights.json`，把 `keywords` 换成你们真实用户会说的话（现在是我按常识写的）。
3. 跑一遍引擎，看输出跟你自己的判断差多少：
   ```
   python suggest_engine.py
   ```
4. 把 `ACTION_EFFECTS` 里的数值当成可调参数——这是你调权的旋钮。
5. 读 `edit_logic.md` 第 2 节，确认"拍中 → 拍后"的闭环字段能打通：`quality_score` 和 `followed_advice` 必须由拍摄阶段写进素材清单。
6. 跑一遍剪辑引擎：`python edit_engine.py`，看整理结果跟你自己的判断差多少。
7. 第一周末交付：`intent_weights.json` 定稿 + 一页纸数学定义 + 闭环字段对接方案。

### 队友 A — 实时工程

1. 先跑通 `suggest_engine.py`，确认它输出的 JSON 长什么样。
2. 搭网页骨架：`getUserMedia` 出画面 → 一个按钮 → 一个输入框 → 一个建议区。
3. 第一周用**假数据**把"按按钮 → 出建议 → 叠加显示"流程跑通，不要等任何人。
4. 第二周把你的 `suggest_engine.py` 包成 FastAPI 接口，前端按接口三的格式调用。
5. 第二周接剪辑侧：素材导入 + 元数据读取 + 批量抽帧，补齐 `materials.csv` 的 `scene_type`、`has_mistake`、`duplicate_of`。
6. 第三周把 `edit_engine.py --json` 输出的 EDL 转成 FFmpeg 命令，跑通导出粗剪 MP4。
7. 第一周末交付：能跑的网页骨架 + 假数据全流程。

### 队友 B — 领域规则

1. 打开 `shots.csv`，照着 12 行示例，把 30 个镜头的标注填到 90 行。字段说明见 `math_definition.md` 第 2 节。
2. 逐条校验 `phrases.json` 的 50 句话术——标准是"普通人听完不用问第二遍"。
3. 从标注表里提炼规则，按 `rules.json` 的格式补充到覆盖 80% 常见场景。
4. 第二周对引擎输出的 20 条建议打"专不专业"分。
5. 按 `edit_rules.json` 的格式，为口播 / 电商 / Vlog / 情绪短片各写一套叙事弧和转场偏好。
6. 对 `edit_engine.py` 输出的粗剪打"能不能用"分，标出不该留的片段和不该用的转场。
7. 第一周末交付：90 条标注表 + 50 条校验过的话术。

---

## 三、如何运行引擎

环境：Python 3.10+，无需安装任何第三方库。

```bash
# 默认示例（人像特写）
python suggest_engine.py

# 自定义参数
python suggest_engine.py \
  --intent "电影感人物特写，背景虚化" \
  --ratio 0.18 \
  --bg 0.42 \
  --light front \
  --gaze 0.30

# 从 stdin 读完整 JSON（队友 A 接 API 时用这个格式）
echo '{"intent":"产品 电商 质感","frame_features":{"subject_ratio":0.15,"bg_complexity":0.7,"light_direction":"front","gaze_space":0.1}}' | python suggest_engine.py --stdin

# ---- 素材剪辑 ----
# 完整输出：整理分类 + 粗剪时间线
python edit_engine.py

# 仅输出 EDL（队友 A 接 FFmpeg 时用）
python edit_engine.py --json
```

---

## 四、接口约定

### 输入 `frame_features`

| 字段 | 类型 | 含义 | 获取方式 |
|---|---|---|---|
| `subject_ratio` | float 0–1 | 主体占画面比例 | 端侧主体框面积 / 画面面积 |
| `bg_complexity` | float 0–1 | 背景复杂度 | 背景区域边缘密度归一化 |
| `light_direction` | string | 光线方向 | front / side / back / low / golden |
| `gaze_space` | float 0–1 | 视线方向留白比例 | 主体朝向一侧的空白面积 |

### 输出

```json
{
  "intent_detected": "portrait_closeup",
  "intent_label": "人像特写",
  "current_score": {"total": 0.693, "components": {...}},
  "suggestions": [
    {
      "rank": 1,
      "action": "往前走一步",
      "delta": "主体占比上升",
      "expected_gain": 0.152,
      "reason": "主体占比偏低，观众看不清表情"
    }
  ]
}
```

前端只需要读 `suggestions[].action` 和 `suggestions[].reason` 显示给用户，其余字段用于调试。

### 剪辑输出 `rough_cut.timeline`

队友 A 只需要读 `timeline[]` 这几个字段：

| 字段 | 用途 |
|---|---|
| `clip_id` / `filename` | 找到源素材 |
| `in` / `out` | FFmpeg 裁剪区间（秒） |
| `timeline_in` | 在时间线上的起始位置（秒） |
| `transition_in` | 与上一条的转场：无 / 硬切 / 叠化 / 淡入淡出 |
| `reason` | 展示给用户看"为什么留下这条" |

完整 EDL 结构见 `edit_logic.md` 第 6 节。`organization` 部分用于展示整理结果，`rough_cut` 部分用于渲染。

---

## 五、协作规则

1. **接口字段一旦定下，第一周内不许私改。** 要改在群里说一声，同步更新这个 README 和版本号。
2. **每周一晚 30 分钟碰头**：只看上周产出、确认接口没变、定下周优先级。不开务虚会。
3. **每周日晚各自把产出放进这个目录**，覆盖骨架文件时在群里说一声改了什么。
4. **队友 B 的规则和话术是引擎的燃料**——B 不完成，你和 A 做出来的东西就是空壳。第一周优先保 B 的进度。
5. **闭环字段不能断**：拍摄阶段必须把 `intent_id`、`quality_score`、`followed_advice` 写进素材清单，否则剪辑引擎拿到的就是瞎猜。这是产品从"拍中"连到"拍后"唯一的线。
