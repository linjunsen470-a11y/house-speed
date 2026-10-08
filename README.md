# House Speed · 房产视频自动变速

[GitHub 仓库](https://github.com/linjunsen470-a11y/house-speed) · [自动测试](https://github.com/linjunsen470-a11y/house-speed/actions/workflows/ci.yml)

专门调整房产带看视频的播放速度。通过本地 OpenCV 分析画面结构和运动，区分实景展示、过渡行走与低信息画面，自动分段变速，并保留完整源时间段。输出帧率固定，加速时可能减少采样帧，但不删掉任何源时间区间。

## 安装与运行

需要 Python 3.10+，以及 PATH 中的 FFmpeg、FFprobe。

```powershell
cd D:\work\house-speed
python -m pip install -r requirements.txt
python edit_speed.py "D:\videos\house.mp4"
```

默认在源视频旁输出 `<文件名>_edited.mp4`；保留原音并按各段速度同步调整，不覆盖原视频。已有同名结果会在新结果通过媒体校验后替换。输入视频可以继续放在桌面原素材目录，使用完整路径即可。

```powershell
# 指定输出
python edit_speed.py "D:\videos\house.mp4" -o "D:\videos\house_speed.mp4"
# 只分析，查看分段和预计时长
python edit_speed.py "D:\videos\house.mp4" --dry-run
# 导出用于检查分段速度的标注预览
python edit_speed.py "D:\videos\house.mp4" --dry-run --review
# 忽略缓存重新分析，或输出机器可读结果
python edit_speed.py "D:\videos\house.mp4" --reanalyze --json
# 输入文件名以减号开头：其他参数放在 -- 前面
python edit_speed.py --dry-run -- "-1a.mp4"
```

## 调整速度

修改 `config.yaml` 中的 `speeds`：

```yaml
max_speed: 1.30  # 最终速度上限，涵盖全部提速规则和旧视频配置
speeds:
  room: 1.0  # 房间、家具、园林等有信息实景
  move: 1.18  # 走廊和过渡行走
  fast: 1.30   # 空墙、急转和低信息穿行
```

默认采用自然带看节奏，便于后续添加 TTS 口播。正常实景展示和有信息的移动扫景保持原速；过渡行走为 1.18 倍，低信息画面为 1.30 倍。连续近静止不足 2.5 秒不做静止提速；达到阈值时，对识别出的整段近静止区间至少使用 1.12 倍，并非先播放 2.5 秒再提速。

房间停留表在第 4、8、12 秒分别增长到首档的 1.08、1.16、1.25 倍；实际倍率按 `speeds.room × 当前档 speed / 首档 speed` 计算。静止提速和停留提速取较高值，因此默认静止实景前 8 秒通常为 1.12 倍，之后按停留档位调整。仍在移动的扫景镜头跳过停留提速。

最终所有片段统一受 `max_speed` 限制，包括侧车文件中的旧高倍率配置。可以调低该上限以更保守地处理素材；手动提高上限会允许更明显的加速。速度变化仍是分段常量，不是真正的连续缓入缓出曲线，观感需结合实际素材检查。

这些是基础倍率；停留提速、静止提速和人工覆盖还会影响最终分段。`1.0` 表示原速。局部识别不准时，复制 `examples/sample.edit.yaml` 为视频旁的 `<文件名>.edit.yaml`，设置速度或 `overrides`；也可以用 `--edit-config` 指定配置文件。

分类来自本地画面特征和规则，可能误判；可用 `--dry-run --review` 检查。默认 `algorithm.mode: legacy`，实验分类器 `candidate` 保持可选。

### 配置优先级与局部调整

配置按“代码默认值 → 主配置 → 视频侧车配置”合并。未指定 `-c` 时，优先读取当前工作目录的 `config.yaml`，否则使用项目中的文件。显式指定的 `-c`、`--edit-config` 文件必须存在；不会因拼写错误悄悄退回默认值。未知配置键会提示警告。`config.local.yaml` 不会自动加载，使用它时需显式传给 `-c`。

默认侧车文件在输入视频旁，命名为 `<文件名>.edit.yaml`。视频仍放在桌面时，项目目录中同名侧车不会自动生效，可以通过 `--edit-config "D:\work\house-speed\lvhu-822.edit.yaml"` 指定。

`overrides` 只覆盖 `room/move/fast` 类别，之后仍会应用节奏规则；需要所有 room 区间保持设定倍率时，在侧车中设置：

```yaml
pacing:
  enabled: false  # 同时关闭停留提速和静止提速；最终速度上限仍有效
```

### 用于后续 TTS 配音

本项目只输出变速视频，不生成配音，也不将画面自动拉伸到某段 TTS 的长度。先检查变速结果，再按最终画面节奏安排口播，配音使用成片时间而非原片时间。剪辑软件中可关闭原音，另铺 TTS。不要为了强行压到固定口播时长提高速度上限。

## 文件结构

- `edit_speed.py`：命令行入口。
- `walkthrough_edit/analyze.py`、`classify.py`、`scene_aba.py`：画面分析、分类与分段保护。
- `walkthrough_edit/render.py`、`pipeline.py`：音画变速、缓存、导出和运行报告。
- `walkthrough_edit/config.py`、`config.yaml`：配置读取与校验。
- `evaluate_segments.py`、`scripts/tune_segments.py`：变速分段评估和参数调优。
- `tests/`：速度处理相关测试。

`frames/` 保存可重建的分析缓存、分段计划、预览和报告，不提交 Git。

`segments.json` 中 `t0/t1` 是源视频秒数；每段成片时长约为 `(t1-t0)/speed`，累加后得到成片时间。`summary.json` 包含预计与实际时长、缓存是否命中、`max_speed_limit` 和 `max_speed_used`。改变速度配置会重新分段和导出，不必重分析；损坏或不兼容的分析缓存会重新计算。

`--dry-run` 仍会写分析缓存与报告；同时传 `--review` 时会实际编码预览，但不导出最终视频。预览路径必须与源视频及最终输出不同。

## 已知边界

- 默认自然速度通过规则和上限控制，不能保证所有素材都像未经剪辑的真人拍摄；急转、运动模糊和复杂走廊仍需检查。
- 分析以平均 FPS 和帧序号估计源时间，变帧率素材的分段时间可能有偏差；严格同步前宜使用恒定帧率素材。分析图固定缩放到 135×240，横屏素材的特征判断仍需额外检查。
- 正常导出显式使用源平均 FPS 的恒定帧率；加速会减少采样帧，不承诺逐帧保留。原音使用 `atempo` 同步，不等同于 TTS 口播适配。
- 请串行处理同一输入或输出路径；当前没有并发写入锁。媒体校验检查流、尺寸和时长，不替代完整解码或人工审片。

## 验证

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

本轮审计验证、修复项和历史标注评估见 [AUDIT.md](AUDIT.md)。历史素材留在桌面，当前仓库不包含视频和评测缓存。

## 仓库卫生与自动测试

Git 只提交代码、通用配置、测试和 Markdown 文档。视频、音频、字体贴纸、分析缓存、归档文件、密钥以及私人侧车配置由 `.gitignore` 排除。`examples/sample.edit.yaml` 是可提交的通用配置示例；实际视频的同名配置留在本机。

GitHub Actions 在 `main` 推送和 Pull Request 时使用 Python 3.10、3.12 安装 FFmpeg 并执行测试。历史素材不在仓库，相关测试会跳过；合成视频的音画变速测试仍执行。
