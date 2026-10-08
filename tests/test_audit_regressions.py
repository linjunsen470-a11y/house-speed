"""Regression checks for parameter precedence, cache recovery and safe export."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from walkthrough_edit.classify import build_segments, validate_timeline
from walkthrough_edit.config import load_config, merge_config_file
from walkthrough_edit.pipeline import (
    _analysis_fingerprint, _load_analysis, _write_features, _write_motion, run_pipeline,
)
from walkthrough_edit.render import _run_ffmpeg_atomic, atempo_chain


def room_rows(seconds=16, fps=10):
    return [
        {'idx': i, 't': i / fps, 'mean': 100., 'std': 60., 'edge': .14,
         'motion': .2, 'appearance': np.linspace(0, 1, 20, dtype=np.float32)}
        for i in range(int(seconds * fps))
    ]


def test_custom_room_speed_controls_initial_and_relative_hold_speed():
    cfg = load_config(None)
    cfg['speeds']['room'] = 1.04
    cfg['pacing']['static_boost_enabled'] = False
    segments = build_segments(room_rows(), 16., cfg)
    assert segments[0]['speed'] == pytest.approx(1.04)
    assert segments[-1]['speed'] == pytest.approx(1.04 * 1.25)


def test_disabling_pacing_disables_both_hold_and_static_acceleration():
    cfg = load_config(None)
    cfg['pacing']['enabled'] = False
    segments = build_segments(room_rows(), 16., cfg)
    assert all(s['speed'] == cfg['speeds']['room'] for s in segments)


def test_old_sidecar_cannot_bypass_final_speed_limit(tmp_path):
    old = tmp_path / 'old.edit.yaml'
    old.write_text('speeds:\n  fast: 3.5\n', encoding='utf-8')
    cfg = merge_config_file(load_config(None), old)
    walls = [dict(row, edge=.01, motion=18.) for row in room_rows()]
    segments = build_segments(walls, 16., cfg)
    assert all(s['speed'] <= 1.30 for s in segments)


def test_final_limit_also_covers_combined_room_ramp_and_static_boost():
    cfg = load_config(None)
    cfg['speeds']['room'] = 1.2
    cfg['pacing']['static_boost_speed'] = 2.7
    segments = build_segments(room_rows(), 16., cfg)
    assert max(s['speed'] for s in segments) == 1.30


def test_invalid_speed_limit_is_rejected(tmp_path):
    path = tmp_path / 'bad.yaml'
    path.write_text('max_speed: .8\n', encoding='utf-8')
    with pytest.raises(ValueError, match='max_speed'):
        load_config(path)


@pytest.mark.parametrize('corruption', ['fps_nan', 'meta_list', 'csv_nan', 'csv_time', 'csv_missing_key', 'npz_invalid'])
def test_corrupt_cache_is_reanalyzed(tmp_path, corruption):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    cfg = load_config(None)
    rows = room_rows(seconds=.2)
    work = tmp_path / 'cache'
    work.mkdir()
    _write_motion(work / 'motion.csv', rows)
    _write_features(work / 'analysis_features.npz', rows)
    meta = {'fingerprint': _analysis_fingerprint(source, cfg), 'fps': 10.}
    if corruption == 'fps_nan':
        meta['fps'] = float('nan')
    elif corruption == 'meta_list':
        meta = []
    (work / 'analysis_cache.json').write_text(json.dumps(meta), encoding='utf-8')
    if corruption == 'csv_nan':
        broken = [dict(row, motion=float('nan')) for row in rows]
        _write_motion(work / 'motion.csv', broken)
    elif corruption == 'csv_time':
        _write_motion(work / 'motion.csv', [rows[0], dict(rows[1], t=1.0)])
    elif corruption == 'csv_missing_key':
        (work / 'motion.csv').write_text('idx,t\n0,0\n', encoding='utf-8')
    elif corruption == 'npz_invalid':
        (work / 'analysis_features.npz').write_bytes(b'broken zip')
    with patch('walkthrough_edit.pipeline.analyze_motion', return_value=(rows, 10.)) as analyze:
        _, fps, hit = _load_analysis(source, cfg, work, False)
    analyze.assert_called_once()
    assert fps == 10. and not hit
    _, _, second_hit = _load_analysis(source, cfg, work, False)
    assert second_hit


def test_explicit_missing_config_returns_json_error(tmp_path, capsys):
    from edit_speed import main
    code = main(['source.mp4', '-c', str(tmp_path / 'typo.yaml'), '--json'])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result['status'] == 'error'
    assert 'Config not found' in result['error']


@pytest.mark.parametrize('collide_with', ['source', 'output'])
def test_review_path_collision_fails_before_analysis(tmp_path, collide_with):
    source = tmp_path / 'source.mp4'
    output = tmp_path / 'output.mp4'
    source.write_bytes(b'source')
    cfg = load_config(None)
    cfg['io']['work_dir'] = str(tmp_path / 'frames')
    review = source if collide_with == 'source' else output
    with patch('walkthrough_edit.pipeline.check_tools'), patch('walkthrough_edit.pipeline._load_analysis') as analyze:
        with pytest.raises(ValueError, match='Review path'):
            run_pipeline(source, output, cfg=cfg, dry_run=True, review_path=review)
    analyze.assert_not_called()


@pytest.mark.parametrize('speed', [0., -1., float('nan'), float('inf')])
def test_invalid_audio_speed_fails_instead_of_looping(speed):
    with pytest.raises(ValueError):
        atempo_chain(speed)


@pytest.mark.parametrize('field', ['t0', 't1'])
def test_nonfinite_timeline_boundaries_are_rejected(field):
    segment = {'t0': 0., 't1': 1., 'kind': 'room', 'speed': 1.}
    segment[field] = float('nan')
    with pytest.raises(ValueError):
        validate_timeline([segment], 1.)


@pytest.mark.parametrize('fault', ['duration', 'no_video', 'missing_audio'])
def test_bad_render_does_not_replace_existing_output(tmp_path, fault):
    source, output = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    source.write_bytes(b'source')
    output.write_bytes(b'previous valid output')
    script = tmp_path / 'filter.txt'
    script.write_text('filter', encoding='utf-8')
    media = {'duration': 2., 'video_codec': 'h264', 'width': 160, 'height': 120, 'has_audio': True}
    if fault == 'duration':
        media['duration'] = .5
    elif fault == 'no_video':
        media['video_codec'] = ''
    else:
        media['has_audio'] = False

    def render_stub(command, **kwargs):
        Path(command[-1]).write_bytes(b'bad output')
        return SimpleNamespace(stdout=[], wait=lambda: 0, poll=lambda: 0)

    with patch('walkthrough_edit.render.ensure_encoder'), \
         patch('walkthrough_edit.render._filter_complex_file_args', return_value=[]), \
         patch('walkthrough_edit.render.subprocess.Popen', side_effect=render_stub), \
         patch('walkthrough_edit.render.probe_media', return_value=media):
        with pytest.raises(RuntimeError):
            _run_ffmpeg_atomic(source, output, script, ['-c:v', 'libx264'], True,
                               '+faststart', tmp_path / 'render.log', 2., output_fps=12.)
    assert output.read_bytes() == b'previous valid output'
    assert not (tmp_path / '.output.part.mp4').exists()


def test_export_failure_updates_summary_instead_of_leaving_success(tmp_path):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    cfg = load_config(None)
    cfg['io']['work_dir'] = str(tmp_path / 'frames')
    with patch('walkthrough_edit.pipeline.check_tools'), \
         patch('walkthrough_edit.pipeline._load_analysis', return_value=(room_rows(2), 10., False)), \
         patch('walkthrough_edit.pipeline.probe_duration', return_value=2.), \
         patch('walkthrough_edit.pipeline.export_video', side_effect=RuntimeError('encode failed')):
        with pytest.raises(RuntimeError, match='encode failed'):
            run_pipeline(source, cfg=cfg)
    summaries = list((tmp_path / 'frames').glob('*/summary.json'))
    summary = json.loads(summaries[0].read_text(encoding='utf-8'))
    assert summary['status'] == 'failed' and summary['error'] == 'encode failed'
