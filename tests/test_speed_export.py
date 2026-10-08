"""End-to-end speed-only export checks using short generated videos."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from walkthrough_edit.config import load_config
from walkthrough_edit.pipeline import run_pipeline
from walkthrough_edit.render import probe_media, export_review


@pytest.mark.skipif(
    not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
    reason='FFmpeg tools are unavailable',
)
@pytest.mark.parametrize('with_audio', [False, True])
def test_speed_export_preserves_source_and_syncs_duration(tmp_path, with_audio):
    source = tmp_path / '房产源片.mp4'
    output = tmp_path / 'output' / '变速.mp4'
    command = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=12:duration=4',
    ]
    if with_audio:
        command += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-c:a', 'aac']
    command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-t', '4', str(source)]
    subprocess.run(command, check=True, timeout=30)
    source_before = source.read_bytes()
    cfg = load_config(None)
    cfg['io']['work_dir'] = str(tmp_path / 'frames')
    cfg['encode'].update({'match_source': False, 'video_codec': 'libx264', 'preset': 'ultrafast'})
    cfg['speeds'].update({'room': 1.0, 'move': 2.0, 'fast': 4.0})
    cfg['max_speed'] = 4.0  # Explicitly exercise extreme audio tempo chaining.
    cfg['pacing']['enabled'] = False
    cfg['overrides'] = [
        {'start': 0.0, 'end': 1.0, 'kind': 'room'},
        {'start': 1.0, 'end': 2.0, 'kind': 'move'},
        {'start': 2.0, 'end': 4.0, 'kind': 'fast'},
    ]
    result = run_pipeline(source, output, config_path=None, cfg=cfg)
    assert source.read_bytes() == source_before
    assert output.is_file()
    assert result.segments[0]['t0'] == 0.0
    assert result.segments[-1]['t1'] == pytest.approx(4.0)
    assert result.duration_out == pytest.approx(2.0, abs=0.15)
    media = probe_media(output)
    streams = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)
    ], text=True))['streams']
    assert any(s['codec_type'] == 'audio' for s in streams) == with_audio
    video_stream = next(s for s in streams if s['codec_type'] == 'video')
    assert video_stream['avg_frame_rate'] == '12/1'
    if with_audio:
        audio_stream = next(s for s in streams if s['codec_type'] == 'audio')
        assert abs(float(audio_stream['duration']) - float(video_stream['duration'])) < 0.15
    assert media['width'] == 160 and media['height'] == 120
    assert 'pack_enabled' not in result.summary
    cached = run_pipeline(source, output, config_path=None, cfg=cfg, dry_run=True)
    assert cached.cache_hit
    assert cached.segments == result.segments
    review = tmp_path / 'review.mp4'
    export_review(source, review, result.segments, cfg, result.work_dir, fps=12.)
    assert probe_media(review)['duration'] == pytest.approx(2., abs=.25)


def test_removed_cli_flags_are_rejected():
    from edit_speed import main
    with pytest.raises(SystemExit) as failure:
        main(['--pack'])
    assert failure.value.code == 2


def test_old_pack_configuration_warns(tmp_path):
    path = tmp_path / 'old.yaml'
    path.write_text('pack:\n  enabled: true\n', encoding='utf-8')
    with pytest.warns(UserWarning, match='pack'):
        config = load_config(path)
    assert 'pack' not in load_config(None)
