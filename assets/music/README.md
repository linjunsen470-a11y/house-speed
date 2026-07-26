# 成片 BGM

当前目录里实际保留的曲库，与 `walkthrough_edit/music_catalog.py` 一一对应。

## 1. 完播精选 `curated/`（默认）

| 配置 id | 文件 | 用途 |
|---------|------|------|
| **`pop_hook`** | `curated/bgm_pop_hook.mp3` | **默认** · 钩子短循环 |
| `pop_spark` | `curated/bgm_pop_spark.mp3` | 明亮抓耳 |
| `pop_vibe` | `curated/bgm_pop_vibe.mp3` | 中长律动 |
| `pop_soft` | `curated/bgm_pop_soft.mp3` | 柔和 |
| `pop_drive` | `curated/bgm_pop_drive.mp3` | 推进 |
| `pop_clean` | `curated/bgm_pop_clean.mp3` | 干净耐听 |

```yaml
audio:
  bgm: "pop_hook"
  volume: 0.80
```

## 2. 节奏短名单 `shortlist/`

| 配置 id | 文件 |
|---------|------|
| `sl01` … `sl12` | `shortlist/01.mp3` … `12.mp3` |

也支持路径：`shortlist/01.mp3` 或 `01`。

## 3. 本地曲库 `a*.m4a`

| 配置 id | 文件 |
|---------|------|
| `a1` … `a9`, `a11` … `a18` | 同名 `.m4a`（缺号自动跳过） |

## 4. 随机

```yaml
audio:
  bgm: "random"   # 优先 curated，否则 shortlist / a*
```

## 5. 可选 CC BY（Kevin MacLeod）

若需要可再下载（**当前默认曲库已不含这些文件**）：

```bash
python scripts/fetch_bgm.py
```

下载后可用 id：`carefree` / `easy_lemon` / `life_of_riley` / `summer_day` / `dreamlike` / `bittersweet`。  
若未下载却仍写 `carefree`，代码会**回退**到 `pop_hook`。

许可（下载后发布需署名）：`BGM: … by Kevin MacLeod (incompetech.com) · CC BY`

## 列出全部

```bash
python edit_speed.py --list-styles
```

## 自备文件

```yaml
audio:
  bgm: "assets/music/your_track.mp3"
```

## 仓库与体积

- `assets/music/**` 音频默认 **gitignore**（仅保留本 README 与子目录说明）。
- 本地自备 `curated/`、`shortlist/`、`a*.m4a` 即可；列表与是否在盘上：`python edit_speed.py --list-styles`（缺文件标 `!`）。
- 可选维护脚本（个人自用，非流水线必需）：`scripts/audit_bgm.py`、`select_rhythmic_bgm.py`、`rank_bgm_top.py`、`export_shortlist.py`。
