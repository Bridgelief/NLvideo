# data/ · 领域规则与内容库（owner: domain）

本目录是建议引擎与剪辑引擎的"燃料"：5 类意图的权重与目标、50 条可执行话术、33 条条件→动作规则、
剪辑整理规则、演示素材清单、90 条拉片标注。**所有口径以 `docs/INTERFACES.md` §3 为准**。

- 版本：v1.1（2026-11-26）
- 编码：JSON / CSV 全部 UTF-8 **无 BOM**；CSV 逗号分隔、字段内不用半角逗号
- 唯一真值：动作 ID 以 `engine/action_effects.py` 的 `ACTION_EFFECTS`（50 键）为准，
  `phrases.json` 与它**严格一一对应**，`rules.json` 只引用其中 ID

---

## 1. 文件清单与字段

### 1.1 `intent_weights.json`（5 类意图）

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | str | 数据版本 |
| `intents[]` | list | 恰好 5 条，`id` 固定为 `portrait_closeup/portrait_medium/landscape/product/emotional` |
| `intents[].label` | str | 中文名（人像特写/人物中景/风光环境/产品静物/情绪叙事） |
| `intents[].keywords` | str[] | 真实用户口语关键词，每类 **16–21 个**（契约要求 ≥12） |
| `intents[].weights` | obj | `subject_ratio/bg_complexity/light_angle/gaze_space`，四键和 = 1.0 |
| `intents[].targets` | obj | `subject_ratio`/`gaze_space` 为 `[lo,hi]`；`bg_complexity` 为 `[0,upper]`；`light_angle` 为非空子集（front/side/back/low/golden） |
| `intents[].preferred_actions` | str[] | 3–5 个合法动作 ID，规则全不命中时兜底 |

权重沿用 `reference/kit/intent_weights.json`（也就是 `math_definition.md` §3 的表），本轮只改
`keywords`（补成真实口语）、修掉 `preferred_actions` 里的非法 ID（kit 的 `wider_focal`→`widest`、
`clear_background`→`clean_background`）。

### 1.2 `phrases.json`（50 条话术）

| 字段 | 说明 |
|---|---|
| `id` | 动作 ID，必须 ∈ `ACTION_EFFECTS`（50 个，不多不少） |
| `action` | 用户照做的一句话，**≤12 字且含具体量**（"后退两步""换 3.2x 焦段""把手机降到腰部高度""往左挪半步"） |
| `category` | `distance/focal/height/angle/light/composition/background/movement` 之一 |
| `affects` | 受影响维度子集（由 `ACTION_EFFECTS` 的 `r/c/l/g` 换算，脚本校验一致） |
| `delta_hint` | 界面效果提示，带百分比（如"主体占比 -12%，背景干扰 -5%"） |
| `reason_template` | ≤40 字纯人话，**不出现"权重/函数/评分/维度"等术语** |
| `condition_hint` | 给规则同学看的口径（如 `subject_ratio > 目标上限`） |

覆盖：远近（4 条）、焦段（7）、机位高度（7）、左右走位（`step_left/step_right`）、角度（7）、
光位（7）、构图（三分/留白/居中，8）、背景清理（6）、移动（`slow_push`）。

### 1.3 `rules.json`（33 条规则）

| 字段 | 说明 |
|---|---|
| `id` | R001–R033 |
| `condition` | `{field, op, value}`；field ∈ 四维；op ∈ `< <= > >= == in not_in`；`light_angle` 只用 `in/not_in` + 字符串数组（契约要求） |
| `applies_to` | 意图 ID 数组，或 `["all"]` |
| `action_id` | 必须 ∈ `ACTION_EFFECTS` |
| `reason` | 一句人话，直接展示给用户 |
| `priority` | 1–10 整数 |

覆盖统计（脚本实测）：**7 个主体占比 / 8 个背景复杂度 / 10 个光线 / 8 个视线留白**；
每意图专属规则 `portrait_closeup 9 / portrait_medium 7 / landscape 8 / product 8 / emotional 6`。

### 1.4 `edit_rules.json`（整理 + 粗剪）

| 字段 | 说明 |
|---|---|
| `quality_filters[]` | 6 条，按顺序首个命中即剔除：`stability<0.45`、`focus_ok==0`、`has_mistake==1`、`duration_sec<1.0`、`quality_score<0.5`、`duration_sec>30` |
| `priority_bonus[]` | 3 条：`followed_advice==1 → +0.15`（**闭环验收 #7**）、`quality_score>0.75 → +0.10`、`quality_score>0.85 → +0.05` |
| `selection` | `max_clips 12 / min 1.5s / max 8.0s / default 3.5s / trim_head 0.3s / trim_tail 0.3s` |
| `ordering` | `strategy=intent_arc`，`intent_order` = 环境→人物→产品→特写→情绪（含全部 5 意图） |
| `transitions` | **对象 schema**（契约 §2.4）：`after_intent/after_intent_transition/same_scene_type_transition/default_transition/reason_templates` |

### 1.5 `materials.csv`（16 行演示素材）

表头严格为：
`clip_id,filename,duration_sec,scene_type,intent_id,quality_score,stability,focus_ok,has_mistake,duplicate_of,followed_advice,in_point,out_point,notes`

覆盖：正常可用 10 条 / 抖动 1（`stability=0.31`）/ 失焦 1（`focus_ok=0`）/ 失误 1（`has_mistake=1`）/
重复 1（`C005→C002`）/ 过短 1（0.8s）/ 低质量 2（`quality_score<0.5`）；5 类意图都有；
`followed_advice=1 且 quality_score≥0.7` 共 **7 行**（契约要求 ≥5）；所有行 `in_point < out_point ≤ duration_sec`。

### 1.6 `shots.csv`（90 行 = 30 个镜头 × 3 行）

表头严格为：
`id,film,scene,shot_type,shot_size,subject_ratio,camera_height,focal_estimate,light_direction,gaze_space,bg_complexity,why_this_shot,ai_common_mistake,notes`

- 结构：**30 个镜头骨架 × 3 行**，每镜头拆成 `shot_type=机位/光线/构图` 三行（`id` S001–S090 唯一）
- 片目：5 部真实电影（《花样年华》《银翼杀手2049》《布达佩斯大饭店》《爱乐之城》《小丑》），每部 6 个镜头
- `shot_type`：按契约**表示标注维度**（机位/光线/构图）。原始 kit 把意图 ID 放在 shot_type，本轮按
  “shot_type 区分 3 行”的契约要求调整；**镜头意图写在 `notes` 的 `意图:xxx` 前缀里**
- 取值：`subject_ratio/gaze_space/bg_complexity ∈ [0,1]`；`light_direction ∈ {front,side,back,low,golden}`；
  `camera_height` 用动作 ID 词表（`eye_level/lower_camera/raise_camera/slightly_above/ground_level/...`）；
  `focal_estimate ∈ {1x,2x,3.2x,5x}`

### 1.7 本文件（`README.md`）

字段表、来源标注、覆盖统计、校验命令、与引擎的对齐方式、已知弱点。

---

## 2. 来源标注（哪些是原始、哪些是本轮补充/改写）

| 数据 | 来源 | 说明 |
|---|---|---|
| `intent_weights.json` weights/targets | **kit 原始** | 与 `math_definition.md` §3 表一致，未改数值 |
| `intent_weights.json` keywords | **本轮补充/改写** | kit 每类 8 个书面词；本轮扩到 16–21 个真实口语（"想要氛围感""拍得高级点""产品细节清楚""大气一点""情绪安静一点""背景干净"…） |
| `intent_weights.json` preferred_actions | kit 原始 + **修 2 个非法 ID** | `wider_focal→widest`、`clear_background→clean_background` |
| `phrases.json` 49 条 | **kit 底稿** | 保留 ID 与文案骨架；本轮按要求润色 `action`（补具体量、压到 ≤12 字）、补 `delta_hint` 百分比、`affects` 与 `ACTION_EFFECTS` 对齐、`category` 补 `movement` |
| `phrases.json` `side_45` | **删除** | kit 死条目，`ACTION_EFFECTS` 无对应效果 |
| `phrases.json` `slow_push` | **本轮新增** | 按契约补第 50 条（缓慢推近，`category=movement`） |
| `rules.json` | kit 24 条示例 → **本轮改写/扩到 33 条** | 阈值按目标区间重算；补 landscape 主体过大、product 逆光/留白过多、emotional 留白不足/背景过乱、顶光（low）两条、landscape 背景过乱专用一条（避免"换一面纯色墙"这类室内话术落到户外风光） |
| `edit_rules.json` | kit 底稿 + **契约改造** | filters 4→6（补 `duration_sec<1.0`、`quality_score` 阈值 0.35→0.5、补超长过滤）；bonus 补 1 条；**`transitions` 从 kit 的数组改成契约 §2.4 的对象 schema** |
| `materials.csv` | kit 8 行示例 → **本轮扩到 16 行** | 补过短、低质量、更多正常素材，凑齐 5 类意图与 7 行闭环数据 |
| `shots.csv` | kit 12 行"示例-待填" → **本轮重写 90 行** | 见下节诚实标注 |
| `README.md` | 本轮新写 | — |

### 诚实标注：哪些是模板、哪些是实测

- **`shots.csv` 是教学/模板性质的镜头语言拆解，不是真实拉片数据**：90 行是按 5 部影片的风格与
  公开镜头语言常识撰写的**示例标注**，`notes` 里逐行标了"教学模板示例，非逐帧实测"。
  没有真的逐帧量过主体占比/边缘密度，数值是为与引擎口径（四维 [0,1]）对齐而**人工估的**，
  不能当作测量数据引用，也不能用来算"一致率"指标。要做真实拉片，需按同一表头换成实测值。
- **`materials.csv` 不指向真实视频文件**：`shot_001.mp4` 等只用于 EDL 演示；真实使用应由拍摄阶段
  `output/shot_log.csv` 或 `tools/make_materials.py` 生成。
- `intent_weights.json` 的 weights/targets 是 kit 的设计值（**专家先验，不是拟合结果**）。
- `phrases.json` 的 `delta_hint` 百分比来自 `ACTION_EFFECTS` 的人工近似值（v1 近似模型，非物理建模）。
- `rules.json` 阈值是按目标区间挑的工程值，**不是**从 90 条标注里统计回归出来的。

---

## 3. 覆盖统计（脚本实测输出）

```
意图: 5（portrait_closeup / portrait_medium / landscape / product / emotional）
意图关键词: 16 / 18 / 21 / 18 / 20（每类 >= 12）
话术: 50 条（与 ACTION_EFFECTS 键集严格相等；category 8 类全都用到）
规则: 33 条；每意图专属 {closeup 9, medium 7, landscape 8, product 8, emotional 6}
      每维度 {subject_ratio 7, bg_complexity 8, light_angle 10, gaze_space 8}
剪辑过滤: 6 条；优先加分: 3 条（含 followed_advice +0.15）
素材: 16 行（可用 10 / 剔除 5 / 重复 1；followed_advice=1 且 q>=0.7 共 7 行）
标注: 90 行 = 30 镜头 x 3 维度；5 部片目；light_direction 分布 front 24 / side 30 / back 18 / low 6 / golden 12
```

---

## 4. 校验命令（任何人可复现）

```powershell
cd D:\videoshoot
# 1) 静态全量校验（本目录 7 个文件 + 与 ACTION_EFFECTS 的对齐，1974 项断言，退出码 0）
$env:PYTHONIOENCODING='utf-8'; python _scratch\domain\validate.py

# 1b) 若 _scratch 已被清理，用这条内联命令覆盖核心不变量
python -c "import json;from engine.action_effects import ACTION_EFFECTS as A;d='data/';p=json.load(open(d+'phrases.json',encoding='utf-8'))['phrases'];r=json.load(open(d+'rules.json',encoding='utf-8'))['rules'];i=json.load(open(d+'intent_weights.json',encoding='utf-8'))['intents'];e=json.load(open(d+'edit_rules.json',encoding='utf-8'));print('phrases==50且与ACTION_EFFECTS键集相等:',len(p)==50 and {x['id'] for x in p}==set(A));print('rules:',len(r),'intents:',len(i),'filters:',len(e['quality_filters']))"

# 2) 动作表自身自检
python -m engine.action_effects

# 3) 契约测试（lead 维护）
python -m unittest tests.test_data_contract -v

# 4) 引擎端到端（algo 提供）
python -m engine.suggest_engine --intent "产品 电商 质感" --ratio 0.15 --bg 0.7 --light front --gaze 0.1
python -m engine.suggest_engine --intent "想要氛围感，情绪安静一点" --ratio 0.2 --bg 0.5 --light back --gaze 0.2
python -m engine.suggest_engine --intent "风景 大气 环境" --ratio 0.12 --bg 0.6 --light golden --gaze 0.3
python engine/edit_engine.py --json
```

---

## 5. 与引擎的对齐方式

1. **动作 ID 三方对齐**：`engine/action_effects.py` 的 `ACTION_EFFECTS`（50 键）是唯一真值；
   `phrases.json` 的 `id` 集合与它严格相等；`rules.json` 的 `action_id` 全部落在其中。
   `engine.load_resources()` 若发现不一致会写 `warnings`，本目录的 `validate.py` 会直接判 FAIL。
2. **四维字段名对齐**：数据里的 `subject_ratio/bg_complexity/light_angle/gaze_space` 与
   `suggest()` 的 `features` 键一致；引擎内部单字母 `r/c/l/g` 只在 `ACTION_EFFECTS` 里出现。
3. **评分口径对齐**：`targets` 直接按 `math_definition.md` §4 的 f1/f2/f3/f4 使用
   （区间、上限、目标集合三种形态），改目标区间不需要改代码。
4. **闭环字段对齐**：`materials.csv` 的 `quality_score`/`followed_advice`/`intent_id` 与
   `output/shot_log.csv` 同构（`shot_log.csv` 只多 `before_url/after_url` 两列，引擎忽略额外列），
   所以 `/api/shots` 记录的数据能直接被 `edit_engine` 读。
5. **转场 schema 对齐**：`edit_rules.transitions` 用契约 §2.4 的对象（不是 kit 的数组）；
   `淡入淡出/硬切/叠化/无` 四个字符串是引擎输出 `transition_in` 的取值域。

---

## 6. 已知弱点 / 覆盖不到的场景

1. **规则只有 33 条，覆盖约 80% 常见场景**，不是全量：无规则命中时引擎只能靠
   `preferred_actions` 兜底，不保证最优（架构文档 §5 已声明）。
2. **光线只有 5 个粗类别**（front/side/back/low/golden）：拍不出"顶光 vs 底光"的区分
   （`low` 在 kit 口径里既指低位光也被拿来近似顶光硬光，规则里两种说法都写了 reason）；
   混合光、彩色光、平光+反光板等更细的情况覆盖不到。
3. **情绪/叙事规则偏薄**（`emotional` 只有 5 条专属规则）：情绪是开放集合，
   关键词与规则都只能覆盖"压抑/孤独/冲突/文艺/安静"这几个常见方向。
4. **`preferred_actions` 与规则可能给出同一动作**：引擎按 `action_id` 去重，
   兜底动作可能与规则推荐重叠，排序结果会略保守。
5. **`shots.csv` 数值是人工估的**（见诚实标注），不能用于统计显著性检验；
   要做"建议专业度"评测需要另找真实拉片/实拍样本重标。
6. **`materials.csv` 的 `scene_type` 只用 outdoor/studio 两种**，转场规则里
   "同场景硬切/异场景叠化"的判定因此很粗，真实场景需要更细的场景标签。
7. **无 BGM/节奏点信息**：粗剪只按意图叙事弧排序，不跟音乐节拍，真实剪辑需求会不够。
