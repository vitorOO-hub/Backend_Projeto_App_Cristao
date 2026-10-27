import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from app.errors import ApiError
from app.media import probe_video, summarize
from app.storage import FileTooLarge, LocalStorage

GOOD = {'format': {'format_name': 'mov,mp4,m4a,3gp,3g2,mj2', 'duration': '42.5'},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920},
                    {'codec_type': 'audio', 'codec_name': 'aac'}]}


def variant(**changes):
    info = json.loads(json.dumps(GOOD))
    for path, value in changes.items():
        target = info
        *parents, last = path.split('__')
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        target[int(last) if last.isdigit() else last] = value
    return info


@pytest.fixture
def mp4(tmp_path):
    path = tmp_path / 'clip.part'
    path.write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'0' * 50)
    return path


def fake_run(monkeypatch, stdout=None, returncode=0, error=None):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if error:
            raise error
        return SimpleNamespace(returncode=returncode, stdout=json.dumps(stdout if stdout is not None else GOOD).encode()
                               if not isinstance(stdout, bytes) else stdout)

    monkeypatch.setattr(subprocess, 'run', run)
    return calls


def test_valid_video_is_summarized(monkeypatch, mp4):
    calls = fake_run(monkeypatch)
    assert probe_video(mp4, 'ffprobe') == {'duration_seconds': 42.5, 'video_codec': 'h264',
                                            'audio_codec': 'aac', 'width': 1080, 'height': 1920}
    (command, kwargs), = calls
    assert command[0] == 'ffprobe' and command[-2:] == ['-i', str(mp4)]
    assert kwargs['timeout'] == 10 and kwargs['check'] is False and not kwargs.get('shell')


def test_audio_is_optional_and_attached_pictures_are_ignored():
    info = variant()
    info['streams'] = [info['streams'][0], {'codec_type': 'video', 'codec_name': 'mjpeg',
                                           'disposition': {'attached_pic': 1}}]
    assert summarize(info)['audio_codec'] is None


@pytest.mark.parametrize('info', [
    None, [], 'x', {}, {'format': {}, 'streams': []}, {'format': 'x', 'streams': []}, {'format': {}, 'streams': 'x'},
    variant(format__format_name='matroska,webm'), variant(format__format_name=None),
    variant(streams__0__codec_name='hevc'), variant(streams__1__codec_name='mp3'),
    variant(format__duration='90.01'), variant(format__duration='0'), variant(format__duration='-5'),
    variant(format__duration='nan'), variant(format__duration='inf'), variant(format__duration='abc'),
    variant(format__duration=None)])
def test_unacceptable_media_is_422(info):
    with pytest.raises(ApiError) as error:
        summarize(info)
    assert error.value.status == 422 and error.value.code == 'invalid_media'


def test_stream_count_rules():
    two_video = variant()
    two_video['streams'].append({'codec_type': 'video', 'codec_name': 'h264'})
    two_audio = variant()
    two_audio['streams'].append({'codec_type': 'audio', 'codec_name': 'aac'})
    no_video = variant()
    no_video['streams'] = [no_video['streams'][1]]
    for info in (two_video, two_audio, no_video):
        with pytest.raises(ApiError):
            summarize(info)


def test_duration_boundary_and_dimensions():
    assert summarize(variant(format__duration='90'))['duration_seconds'] == 90
    assert summarize(variant(format__duration='0.001'))['duration_seconds'] == 0.001
    odd = summarize(variant(streams__0__width=True, streams__0__height='1920'))
    assert odd['width'] is None and odd['height'] is None


def test_header_must_look_like_mp4_before_ffprobe_runs(monkeypatch, tmp_path):
    calls = fake_run(monkeypatch)
    for content in (b'', b'GIF89a' + b'0' * 20, b'\x00\x00\x00\x18moovmp42' + b'0' * 20):
        path = tmp_path / 'x.part'
        path.write_bytes(content)
        with pytest.raises(ApiError) as error:
            probe_video(path)
        assert error.value.status == 422
    assert calls == []


def test_missing_ffprobe_is_503_never_a_pass(monkeypatch, mp4):
    fake_run(monkeypatch, error=FileNotFoundError('ffprobe'))
    with pytest.raises(ApiError) as error:
        probe_video(mp4, 'ffprobe-que-nao-existe')
    assert error.value.status == 503 and error.value.code == 'media_unavailable'
    assert 'ffprobe-que-nao-existe' not in error.value.message


@pytest.mark.parametrize('kwargs', [
    {'error': subprocess.TimeoutExpired('ffprobe', 10)}, {'returncode': 1},
    {'stdout': b'not json'}, {'stdout': b'x' * (256 * 1024 + 1)}])
def test_ffprobe_failures_are_422(monkeypatch, mp4, kwargs):
    fake_run(monkeypatch, **kwargs)
    with pytest.raises(ApiError) as error:
        probe_video(mp4)
    assert error.value.status == 422


# --- storage -----------------------------------------------------------------------------------

def test_storage_receives_commits_and_deletes(tmp_path):
    storage = LocalStorage(tmp_path / 'up')
    temp, size = storage.receive(io.BytesIO(b'a' * 3000), 5000)
    assert size == 3000 and temp.parent == storage.temp
    key = storage.new_key()
    storage.commit(temp, key)
    assert storage.path(key).read_bytes() == b'a' * 3000 and not temp.exists()
    storage.delete(key)
    storage.delete(key)
    assert not storage.path(key).exists()


def test_storage_stops_at_the_limit_and_leaves_no_temp_file(tmp_path):
    storage = LocalStorage(tmp_path)
    with pytest.raises(FileTooLarge):
        storage.receive(io.BytesIO(b'a' * 101), 100)
    assert list(storage.temp.iterdir()) == []
    assert storage.receive(io.BytesIO(b'a' * 100), 100)[1] == 100


def test_storage_receive_cleans_up_when_the_stream_fails(tmp_path):
    class Broken(io.BytesIO):
        def read(self, size=-1):
            raise OSError('connection reset')

    storage = LocalStorage(tmp_path)
    with pytest.raises(OSError):
        storage.receive(Broken(b'x'), 100)
    assert list(storage.temp.iterdir()) == []


@pytest.mark.parametrize('key', [
    '../secret.mp4', '..\\secret.mp4', '/etc/passwd', 'a' * 32, 'a' * 32 + '.mov', 'A' * 32 + '.mp4',
    'a' * 31 + '.mp4', 'a' * 32 + '.mp4/../x', '', None, 5])
def test_storage_rejects_keys_that_are_not_server_generated(tmp_path, key):
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).path(key)


def test_generated_keys_are_unique_and_valid(tmp_path):
    storage = LocalStorage(tmp_path)
    keys = {storage.new_key() for _ in range(50)}
    assert len(keys) == 50
    assert all(storage.path(key).parent == storage.root for key in keys)
