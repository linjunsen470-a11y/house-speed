# 房产带看视频 · 自动变速剪辑

对竖屏/横屏**带看 walkthrough** 做智能变速：**不删除任何画面**，房间、客厅等有意义实景保持原速，转角、空墙、门板、急走等过渡段自动加速（2×～3.5× 可配）。

> 全程本地 CV 规则 + ffmpeg，**不需要大模型**。装好 Python 依赖与 ffmpeg 后一条命令即可。

---

## 效果示意

| 输入 | 输出 | 说明 |
|------|------|------|
| `1.mp4` ≈ 27.5s | `1_edited.mp4` ≈ 20.7s | 约保留 75% 时长 |
| `2.mp4` ≈ 36.6s | `2_edited.mp4` ≈ 26.8s | 约保留 73% 时长 |

---

## 环境要求

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) / ffprobe 在 `PATH` 中
- 依赖：

```bash
pip install -r requirements.txt
```

`requirements.txt`：`opencv-python`、`numpy`、`PyYAML`。

---

## 快速开始

```bash
# 使用默认 config.yaml
python edit_speed.py 2.mp4

# 指定配置与输出
python edit_speed.py 2.mp4 -c config.yaml -o 2_edited.mp4

# 只看分段计划，不导出（调参时很有用）
python edit_speed.py 2.mp4 --dry-run
```

成片默认写到：`<原文件名>_edited.mp4`（后缀可在配置里改）。

中间产物：

```
frames/<视频名>/
  motion.csv           # 逐帧运动/边缘指标
  segments.json        # 最终分段与倍率
  filter_complex.txt   # ffmpeg 滤镜脚本
```

---

## 项目结构

```
clip/
├── edit_speed.py              # CLI 入口
├── config.yaml                # 精细调控（主配置）
├── requirements.txt
├── README.md
├── walkthrough_edit/          # 核心包
│   ├── __init__.py
│   ├── config.py              # 加载 / 合并 / 校验 YAML
│   ├── analyze.py             # 逐帧 motion / edge / std
│   ├── classify.py            # room|move|fast 分类与分段
│   ├── render.py              # ffmpeg 滤镜与导出
│   └── pipeline.py            # 端到端编排
├── 1.mp4 / 2.mp4              # 示例素材
└── frames/                    # 分析缓存与分段结果
```

### 流水线（无需大模型）

```
输入视频
   │
   ▼
analyze  逐帧：亮度均值、对比度 std、Canny 边缘密度、帧差 motion
   │
   ▼
classify 平滑指标 → 规则打标 room / move / fast
   │
   ▼
segments 合并过短碎片 → 应用 config 中的 overrides
   │
   ▼
render   ffmpeg：trim + setpts(加速) + atempo + concat
   │
   ▼
*_edited.mp4   （全片段保留，仅速度变化）
```

| 标签 | 默认速度 | 含义 |
|------|----------|------|
| `room` | 1.0×（可再衰减） | 结构清晰、运动不大的实景 |
| `move` | 2.2× | 过渡行走（运动偏大且结构偏弱） |
| `fast` | 3.5× | 转角 / 白墙 / 急转 |

### 方案 A：实景停留衰减（`pacing.room_hold_ramp`）

连续 `room` 段内按**已停留时长**自动提速，缓解「同一空间拍太久、信息重复」导致的拖沓；**仍不删除任何画面**。

默认曲线（相对该 room 段起点）：

| 停留 | 速度 |
|------|------|
| 0～4s | 1.0× |
| 4～7s | 1.5× |
| 7～10s | 2.0× |
| >10s | 2.8× |

在 `config.yaml` 的 `pacing` 中可改阈值；`pacing.enabled: false` 可关闭。

---

## `config.yaml` 调参指南

所有可调项都在根目录 **`config.yaml`**，修改后重新运行即可，无需改代码。

### 1. 速度（最常改）

```yaml
speeds:
  room: 1.0    # 实景；可设 0.9 略慢强调
  move: 2.2    # 走廊过渡
  fast: 3.5    # 转角；可试 3.0～5.0
```

- 成片仍偏长 → 提高 `fast` / `move`
- 转角太「闪」→ 降低 `fast`（如 `2.5`）

### 2. 分类阈值（决定谁被加速）

```yaml
classify:
  wall_edge_max: 0.035      # 边缘低于此 + 低对比 → 当空墙
  wall_std_max: 45.0
  very_fast_motion: 14.0    # 帧差高于此 → fast
  transitional_motion: 10.5
  transitional_edge_max: 0.055
```

| 现象 | 建议 |
|------|------|
| 房间内缓推被误加速 | 提高 `very_fast_motion`（如 16），或降低 `transitional_edge_max` |
| 明显转角仍原速 | 降低 `very_fast_motion`（如 12） |
| 白墙没被加速 | 略提高 `wall_edge_max`（如 0.045） |
| 纹理墙被当空墙 | 降低 `wall_edge_max` 或 `wall_std_max` |

### 3. 分析与平滑

```yaml
analysis:
  resize_width: 135
  resize_height: 240
  canny_low: 50
  canny_high: 150
  smooth_window: 7    # 奇数；越大越稳，短转角越容易被抹掉
```

### 4. 短段合并

```yaml
segments:
  min_duration: 0.40   # 短于此时长的孤岛并入邻居，减少闪切
```

### 5. 实景停留衰减（方案 A）

```yaml
pacing:
  enabled: true
  room_hold_ramp:
    - after: 0.0
      speed: 1.0
    - after: 4.0
      speed: 1.5
    - after: 7.0
      speed: 2.0
    - after: 10.0
      speed: 2.8
```

- 成片仍觉得房间拖 → 提前 `after` 或提高后段 `speed`
- 房间一晃而过看不清 → 延长第一档（如 `after: 5.0` 才开始 1.5×）

### 6. 人工覆盖（精确指定某段）

自动结果里若某几秒不对，可强制 kind：

```yaml
overrides:
  - start: 10.0
    end: 12.5
    kind: room      # 强制原速
  - start: 20.0
    end: 22.0
    kind: fast      # 强制加速
```

先用 `--dry-run` 看 `segments.json` 时间轴，再写 overrides。

### 7. 编码

```yaml
encode:
  video_codec: "libx264"
  preset: "medium"
  crf: 20              # 18 更清晰更大；23 更小
  audio_bitrate: "128k"
```

### 8. 输入输出

```yaml
io:
  output_suffix: "_edited"
  work_dir: "frames"
  save_motion_csv: true
  save_segments_json: true
  save_filter_script: true
```

---

## 调参工作流（推荐）

1. `python edit_speed.py 素材.mp4 --dry-run` 看分段与预估时长  
2. 改 `config.yaml` 的 `speeds` / `classify` / `overrides`  
3. 再 dry-run，确认计划  
4. 去掉 `--dry-run` 导出成片并预览  
5. 对个别错误秒数加 `overrides` 精细修  

**不必接大模型**；若日后要做「客厅/卧室/卫生间」语义分级，可在 `classify` 之后加可选多模态标签，再映射到 `speeds`。

---

## 命令行参数

| 参数 | 说明 |
|------|------|
| `input` | 输入视频路径 |
| `-o` / `--output` | 输出路径 |
| `-c` / `--config` | 配置文件，默认 `config.yaml` |
| `--dry-run` | 只分析与打印计划，不调用 ffmpeg |

---

## 设计约束

1. **不丢弃片段**：全程 `trim` + 变速 + `concat`，时间轴完整。  
2. **规则可解释**：分段结果可从 `motion.csv` + `segments.json` 复盘。  
3. **配置外置**：阈值与倍率集中在 YAML，便于 A/B 与复用。  
4. **本地可跑**：仅 OpenCV + numpy + ffmpeg。  

---

## 常见问题

**Q: 提示找不到 ffmpeg？**  
把 ffmpeg 加入系统 `PATH`，或在安装目录下确认 `ffmpeg -version` 可用。

**Q: 音频变调/爆音？**  
`atempo` 会按相同倍率加速音频；极端倍率由多级 `atempo` 串联。无音轨视频会自动只处理画面。

**Q: 能否批量处理？**  

```bash
python edit_speed.py a.mp4
python edit_speed.py b.mp4
# 或自行 for 循环
```

**Q: 和剪映手动比？**  
适合统一风格的带看粗剪；成片若需字幕、BGM、封面，可把 `*_edited.mp4` 再导入剪辑软件。

---

## License

按需自用 / 修改。素材版权归原作者所有。
