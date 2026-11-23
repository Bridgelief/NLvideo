# 素材自动剪辑与整理 · 逻辑定义

版本 v1.0 · 2026-11-22 · 负责人：Bridge + 队友 A + 队友 B

---

## 1. 功能定位

拍摄结束后，把这次产生的所有素材自动整理、筛选、排序，输出一条可直接执行的粗剪时间线。

```
输入：素材清单 (materials.csv) + 剪辑规则 (edit_rules.json)
输出：整理分类 + 粗剪时间线 EDL (JSON)
```

**我们做**：筛选可用片段、去重、按叙事弧排序、选入出点、建议转场、输出时间线。
**我们不做**：调色、特效、字幕、配音、最终成片。那是剪辑师的事。

---

## 2. 闭环：拍中的数据如何喂给拍后

这是这个功能真正的差异化点。

| 拍摄阶段记录 | 存储位置 | 剪辑阶段怎么用 |
|---|---|---|
| 每条素材的目标意图 | `intent_id` | 按叙事弧排序的依据 |
| 画面构图评分 | `quality_score` | 来自 `suggest_engine.py` 的 S(x, I)，作为质量门槛 |
| 用户是否按建议调整 | `followed_advice` | 优先加权 +0.15，调整过的片段优先保留 |
| 场景类型 | `scene_type` | 决定相邻片段用硬切还是叠化 |

别家的剪辑工具只看画面本身；我们还知道**你当时想拍什么、有没有按建议改**。用户能看到"为什么这条被留下"——这本身就是产品说服力。

---

## 3. 素材清单字段（`materials.csv`）

| 字段 | 类型 | 含义 | 来源 |
|---|---|---|---|
| `clip_id` | string | 素材编号 | 导入时生成 |
| `filename` | string | 文件名 | 文件系统 |
| `duration_sec` | float | 素材总时长（秒） | 视频元数据 |
| `scene_type` | string | 场地类型（outdoor/studio…） | 云端识别或用户填 |
| `intent_id` | string | 镜头类型 | 拍摄阶段记录 |
| `quality_score` | float 0–1 | 构图质量分 | `suggest_engine.py` 的 S(x, I) |
| `stability` | float 0–1 | 稳定度 | 帧间抖动或陀螺仪 |
| `focus_ok` | 0/1 | 是否合焦 | 锐度检测 |
| `has_mistake` | 0/1 | 是否有闭眼/穿帮 | 多模态模型判断 |
| `followed_advice` | 0/1 | 是否按建议调整 | 拍摄阶段记录 |
| `duplicate_of` | string | 重复于哪条素材 | 相似度比对 |
| `in_point` / `out_point` | float | 候选入出点 | 用户标记或自动检测 |

---

## 4. 整理逻辑

### 4.1 去重
`duplicate_of` 非空的素材直接进 duplicates 列表，不参与后续。

### 4.2 质量过滤（任一命中即淘汰）
| 规则 | 条件 | 理由 |
|---|---|---|
| Q001 | `stability < 0.45` | 画面抖动严重 |
| Q002 | `focus_ok == 0` | 主体失焦 |
| Q003 | `has_mistake == 1` | 存在闭眼或穿帮 |
| Q004 | `quality_score < 0.35` | 构图质量分过低 |

### 4.3 优先加权
```
priority = quality_score + 0.15 × followed_advice + 0.10 × (quality_score > 0.75)
```
加权理由会写进时间线的 `reason` 字段，用户能看到。

---

## 5. 粗剪逻辑

### 5.1 叙事弧排序
```
landscape → portrait_medium → product → portrait_closeup → emotional
环境建立      人物交代           产品展示     情绪特写        收尾
```
同类型素材保持拍摄顺序。最多取 12 条，避免粗剪过长。

### 5.2 入出点
```
入点 = 候选入点 + 0.3s      （裁掉起手抖动）
出点 = 候选出点 - 0.3s      （裁掉收尾晃动）
单段时长限制在 1.5–8.0 秒，默认目标 3.5 秒
```

### 5.3 转场
| 条件 | 转场 | 理由 |
|---|---|---|
| 上一条是情绪镜头 | 淡入淡出 | 留呼吸 |
| 同场地 | 硬切 | 最自然 |
| 不同场地/镜头类型 | 叠化 | 过渡平滑 |

---

## 6. 输出 EDL 格式

```json
{
  "organization": {
    "total": 8, "usable": 4, "excluded_count": 3, "duplicate_count": 1,
    "excluded": [{"clip_id": "C004", "reason": "画面抖动严重，无法使用"}],
    "duplicates": [{"clip_id": "C005", "duplicate_of": "C002"}],
    "usable_ids": ["C001", "C002", "C003", "C007"]
  },
  "rough_cut": {
    "clip_count": 4,
    "total_duration_sec": 20.6,
    "timeline": [
      {
        "order": 1,
        "clip_id": "C001",
        "filename": "shot_001.mp4",
        "in": 0.7,
        "out": 5.5,
        "duration_sec": 4.8,
        "timeline_in": 0.0,
        "transition_in": "无",
        "priority": 0.87,
        "reason": "按拍摄建议调整过，通常质量更好"
      }
    ]
  }
}
```

队友 A 把这个 JSON 转成：
- **剪映**：导入草稿 JSON 格式（需要逆向其结构，或用剪映小助手）
- **FFmpeg**：`concat` demuxer + `xfade` 滤镜，直接命令行渲染

原型阶段先做 FFmpeg 导出——开源、可控、不需要逆向私有格式。

---

## 7. 分工调整

### 你（Bridge）
- 定 `edit_rules.json` 的权重与叙事弧
- 把 `suggest_engine.py` 的 S(x, I) 接到 `quality_score` 字段
- 维护 `edit_engine.py` 的决策逻辑
- 验收：8 条测试素材的整理结果与人工判断一致率 ≥ 80%

### 队友 A（实时工程）
- 素材导入、元数据读取、批量抽帧
- 调云端 API 补 `scene_type`、`has_mistake`、`duplicate_of`
- 把 EDL 转成 FFmpeg 命令并跑通导出
- 验收：导入 5 条素材 → 10 秒内出粗剪 MP4

### 队友 B（领域规则）
- 按不同场景定义叙事弧（口播 / 电商 / Vlog / 情绪短片各一套）
- 校验剪辑规则：什么情况该硬切、什么情况该叠化
- 对粗剪结果打"能不能用"分
- 验收：规则覆盖 ≥ 80% 常见素材问题

---

## 8. 评估指标

| 指标 | 目标 |
|---|---|
| 素材筛选一致率（与人工判断） | ≥ 80% |
| 废片误留率 | ≤ 10% |
| 粗剪可用率（用户不用大改就能发） | ≥ 50% |
| 端到端耗时（5 条素材） | < 10 秒 |

---

## 9. 运行方式

```bash
# 完整输出（整理 + 时间线）
python edit_engine.py

# 仅输出 EDL（队友 A 接 FFmpeg 时用）
python edit_engine.py --json

# 指定文件
python edit_engine.py --materials materials.csv --rules edit_rules.json
```
