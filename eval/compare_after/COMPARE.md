# Dry-run compare after config tune

## Config / code deltas
| key | new |
|---|---|
| `classify.corridor_fast_motion` | 14.5 |
| `classify.dash_struct_max` | 0.048 |
| `segments.min_fast_duration` | 1.2 |
| `segments.sandwich_demote_fast_max` | 3.0 |
| `segments.structured_fast_demote_min_sec` | 2.0 |
| `segments.structured_fast_demote_edge_min` | 0.08 |
| code | `demote_long_structured_fast` after corridor promote |

## Outcome highlights
- `-1a` **7.5–12.6s**: continuous **fast 3×** (wall miss fixed)
- `2a` **18–21.6s**: **move 2.2×** (was long false fast 3×); later 21.6–30 room 1×
- `2b` short living-room whip at ~3s: **room** (was false fast)
- Garden low-edge blind + 园林: still protected as expected

## Kind duration (source seconds)

| video | before room/move/fast | after room/move/fast |
|---|---|---|
| `1-包括前花园后花园.mp4` | 54.0/9.7/4.8 | 51.44/11.1/5.97 |
| `2a.mp4` | 15.1/4.17/17.13 | 20.93/7.7/7.77 |
| `2b.mp4` | 20.87/7.87/8.43 | 24.43/9.77/2.97 |
| `3a-主人房.mp4` | 18.34/2.67/1.63 | 19.17/2.87/0.6 |
| `3b-主人房.mp4` | 18.87/3.13/0.0 | 18.8/3.2/0.0 |
| `-1a.mp4` | 22.25/9.4/16.43 | 25.09/9.03/13.97 |
| `-1b.mp4` | 11.0/5.17/21.23 | 11.63/16.77/9.0 |
| `-2.mp4` | 3.8/23.54/5.13 | 5.63/22.27/4.57 |
| `园林环境.mp4` | 11.8/0.0/0.0 | 11.8/0.0/0.0 |

## Checkpoint kinds

| video | t | before | after | note |
|---|---:|---|---|---|
| `-1a.mp4` | 8.5 | room 1.0x | fast 3.0x | wall should be fast **CHANGED** |
| `-1a.mp4` | 10.5 | fast 3.0x | fast 3.0x | wall should be fast |
| `-1a.mp4` | 20.0 | fast 3.0x | fast 3.0x | corridorish |
| `2a.mp4` | 22.0 | fast 3.0x | room 1.0x | long corridor prefer move **CHANGED** |
| `2b.mp4` | 3.0 | fast 3.0x | room 1.0x | short scan **CHANGED** |
| `2b.mp4` | 14.2 | fast 3.0x | fast 3.0x | low edge |
| `1-包括前花园后花园.mp4` | 49.5 | fast 3.0x | fast 3.0x | low-edge blind |
| `1-包括前花园后花园.mp4` | 52.5 | fast 3.0x | fast 3.0x | low-edge blind |
| `-2.mp4` | 5.0 | move 2.2x | move 2.2x | long corridor move |
| `园林环境.mp4` | 5.0 | room 1.0x | room 1.0x | scenic room |

## Key intervals after

### `-1a.mp4` [7.5-12.6]
- 2.13-12.60 **fast** 3.0x

### `2a.mp4` [18.0-27.3]
- 16.87-18.07 **room** 1.0x
- 18.07-21.60 **move** 2.2x
- 21.60-30.67 **room** 1.0x

### `2b.mp4` [2.0-4.0]
- 0.00-2.03 **room** 1.0x
- 2.03-2.90 **move** 2.2x
- 2.90-13.70 **room** 1.0x

### `1-包括前花园后花园.mp4` [48.0-55.0]
- 41.27-48.80 **room** 1.0x
- 48.80-54.77 **fast** 3.0x
- 54.77-56.40 **room** 1.0x

