# 房产带看视频 · 自动变速剪辑

对竖屏/横屏**带看 walkthrough** 做智能变速：**不删除任何画面**，房间、客厅等有意义实景保持原速，转角、空墙、门板、急走等过渡段自动加速（2×～3.5× 可配）。

> 全程本地 CV 规则 + ffmpeg，**不需要大模型**。装好 Python 依赖与 ffmpeg 后一条命令即可。

本仓库只包含**生产代码与配置**；输入视频、成片与分析缓存请放在本地（已由 `.gitignore` 忽略）。

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
# 使用默认 config.yaml（把路径换成你的素材）
python edit_speed.py path/to/input.mp4

# 指定配置与输出
python edit_speed.py path/to/input.mp4 -c config.yaml -o path/to/output.mp4

# 只看分段计划，跳过最终成片（调参时很有用）
python edit_speed.py path/to/input.mp4 --dry-run

# dry-run + review 代理：仍会编码低分辨率标注片，但不导出最终成片
python edit_speed.py path/to/input.mp4 --dry-run --review
```

成片默认写到：`<原文件名>_edited.mp4`（后缀可在配置里改）。

运行时会在本地生成中间产物（可删，可忽略提交）：

```
frames/<stem>-<sha1前8位>/
  motion.csv           # 逐帧运动/边缘指标
  analysis_cache.json  # 分析缓存指纹
  segments.json        # 最终分段与倍率
  filter_complex.txt   # ffmpeg 滤镜脚本
  summary.json         # 机器可读运行摘要
  review.mp4           # 可选：--review 代理
```

---

## 项目结构

```
clip/
├── edit_speed.py              # CLI 入口
├── evaluate_segments.py       # 分段结果 vs 手标评估
├── config.yaml                # 主配置
├── requirements.txt
├── README.md
├── tests/                     # 单元测试（不依赖视频素材）
└── walkthrough_edit/          # 核心包
    ├── __init__.py
    ├── config.py              # 加载 / 合并 / 校验 YAML
    ├── analyze.py             # 逐帧 motion / edge / std
    ├── classify.py            # room|move|fast 分类与分段
    ├── render.py              # ffmpeg 滤镜与导出
    └── pipeline.py            # 端到端编排
```

### 流水线

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
segments 合并过短碎片 → overrides → 停留衰减 → 时间线校验
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

在 `config.yaml` 的 `pacing` 中可改；`pacing.enabled: false` 可关闭。

### 阳台 / 外景保护（`scenic_edge_min`）

外景扫楼、树、天际线时**运动大但边缘很密**，旧逻辑会当成「急转」3.5×。  
现规则：边缘密度 ≥ `classify.scenic_edge_min`（默认 0.16）时，高速画面仍标为 `room`；  
停留衰减时若子段边缘 ≥ `pacing.scenic_skip_edge_min`（默认 0.18）则保持 1×，避免外景被越看越快。

| 调参 | 作用 |
|------|------|
| 外景仍被快进 | 略降 `scenic_edge_min`（如 0.14） |
| 室内急转被误保护 | 略升 `scenic_edge_min`（如 0.18） |
| 外景后半仍被衰减 | 略降 `scenic_skip_edge_min` |

仍不准时可用 `overrides` 强制某段 `kind: room`。

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

### 6. 人工覆盖（精确指定某段）

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
也可为单条视频放 `<素材stem>.edit.yaml`（自动加载，勿提交敏感/临时覆盖）。

### 7. 编码与体积

变速必须**重编码**，不能直接 copy 原轨。  
若使用 `libx264` + 低 CRF，成片码率会远高于微信导出的 **HEVC 低码率** 原片，出现「剪短了反而更大」——这是编码设置问题，不是分段逻辑错误。

```yaml
encode:
  match_source: true    # 推荐：贴近原片码率；HEVC 原片 → libx265
  bitrate_scale: 1.0    # <1 更小；>1 更清晰
  video_codec: "auto"
  # match_source: false 时改用 CRF：
  # crf: 28
  # video_codec: "libx264"
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

---

## 命令行参数

| 参数 | 说明 |
|------|------|
| `input` | 输入视频路径 |
| `-o` / `--output` | 输出路径 |
| `-c` / `--config` | 配置文件，默认 `config.yaml` |
| `--dry-run` | 只分析与打印计划，跳过最终成片导出（若同时加 `--review` 仍会编码 review 代理） |
| `--review [PATH]` | 额外导出低分辨率标注代理；即使有 `--dry-run` 也会编码 |
| `--reanalyze` | 忽略匹配的 motion 缓存，强制重新分析 |
| `--edit-config PATH` | 指定每视频 YAML 覆盖层 |
| `--json` | 仅向 stdout 打印机器可读 JSON 结果 |

---

## Agent / 自动化

分析步骤会缓存；`analysis` 参数与输入文件指纹一致时显示 `cache hit`。

```bash
python edit_speed.py path/to/input.mp4 --dry-run --reanalyze
python edit_speed.py path/to/input.mp4 --dry-run --review
python edit_speed.py path/to/input.mp4 --dry-run --json
```

- `--reanalyze`：忽略缓存重算  
- `--review [PATH]`：生成带标签的低分代理  
- `--json`：stdout 仅输出 JSON，便于 IDE Agent 解析  

每视频覆盖示例 `素材.edit.yaml`：

```yaml
overrides:
  - start: 10.0
    end: 12.5
    kind: room
```

工作目录：`frames/<stem>-<hash>/`，内含 `summary.json` 等运行摘要。

### 分段评估

手标 YAML 示例：

```yaml
labels:
  - start: 3.2
    end: 6.0
    kind: room
```

```bash
python evaluate_segments.py frames/<stem-hash>/segments.json labels.yaml
python -m unittest discover -v
```

---

## 设计约束

1. **不丢弃片段**：全程 `trim` + 变速 + `concat`，时间轴完整。  
2. **规则可解释**：分段结果可从 `motion.csv` + `segments.json` 复盘。  
3. **配置外置**：阈值与倍率集中在 YAML，便于 A/B 与复用。  
4. **本地可跑**：仅 OpenCV + numpy + ffmpeg。  
5. **仓库无素材**：视频与缓存不进版本库，避免体积膨胀。  

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

按需自用 / 修改。素材版权归原作者所有；请勿将未授权视频提交进仓库。
