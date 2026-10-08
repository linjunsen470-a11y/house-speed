from pathlib import Path

import pytest

from walkthrough_edit.classify import build_segments, validate_timeline
from walkthrough_edit.config import load_config, merge_config_file


def rows(seconds, motion, edge, std=60.0):
    return [
        {'idx': i, 't': i / 10.0, 'mean': 100.0, 'std': std,
         'edge': edge, 'motion': motion}
        for i in range(int(seconds * 10))
    ]


def test_moving_room_pan_remains_at_original_speed():
    cfg = load_config(None)
    segments = build_segments(rows(20, 10.0, 0.14), 20.0, cfg)
    validate_timeline(segments, 20.0)
    assert all(s['kind'] == 'room' and s['speed'] == 1.0 for s in segments)


def test_short_room_pause_is_not_accelerated():
    segments = build_segments(rows(2, 0.2, 0.14), 2.0, load_config(None))
    assert all(s['speed'] == 1.0 for s in segments)


def test_long_static_room_avoids_drag_without_aggressive_speedup():
    segments = build_segments(rows(20, 0.2, 0.14), 20.0, load_config(None))
    validate_timeline(segments, 20.0)
    assert max(s['speed'] for s in segments) == pytest.approx(1.25)
    assert all(1.0 <= s['speed'] <= 1.25 for s in segments)
    assert sum((s['t1'] - s['t0']) / s['speed'] for s in segments) < 18.0


def test_low_information_fast_motion_only_gets_modest_acceleration():
    segments = build_segments(rows(8, 18.0, 0.01), 8.0, load_config(None))
    assert all(s['kind'] == 'fast' and s['speed'] == 1.30 for s in segments)


def test_all_shipped_configs_keep_natural_speed_range():
    root = Path(__file__).resolve().parents[1]
    main = load_config(root / 'config.yaml')
    for config in [main, load_config(None)] + [
        merge_config_file(main, root / rel)
        for rel in ['examples/sample.edit.yaml', 'lvhu-822.edit.yaml', 'lvhu-823.edit.yaml']
    ]:
        assert config['speeds'] == {'room': 1.0, 'move': 1.18, 'fast': 1.30}
        assert config['pacing']['static_boost_speed'] <= 1.30
        assert max(step['speed'] for step in config['pacing']['room_hold_ramp']) <= 1.30
