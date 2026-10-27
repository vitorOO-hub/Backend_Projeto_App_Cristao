"""Validation of uploaded videos with ffprobe; neither the file name nor the MIME type is trusted."""
import json
import math
import subprocess

from app.constants import MAX_VIDEO_SECONDS
from app.errors import ApiError

MAX_OUTPUT_BYTES = 256 * 1024


def invalid_media():
    return ApiError(422, 'invalid_media',
                    'O arquivo precisa ser um MP4 H.264 (áudio AAC opcional) de até 90 segundos.')


def dimension(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def summarize(info):
    """Reduce ffprobe's JSON to the technical fields stored in `videos.file`."""
    if not isinstance(info, dict):
        raise invalid_media()
    container, streams = info.get('format'), info.get('streams')
    if not isinstance(container, dict) or not isinstance(streams, list):
        raise invalid_media()
    if 'mp4' not in str(container.get('format_name', '')).split(','):
        raise invalid_media()
    streams = [item for item in streams if isinstance(item, dict)]
    video = [item for item in streams if item.get('codec_type') == 'video'
             and not (item.get('disposition') or {}).get('attached_pic')]
    audio = [item for item in streams if item.get('codec_type') == 'audio']
    if (len(video) != 1 or video[0].get('codec_name') != 'h264' or len(audio) > 1
            or (audio and audio[0].get('codec_name') != 'aac')):
        raise invalid_media()
    try:
        duration = float(container['duration'])
    except (KeyError, TypeError, ValueError):
        raise invalid_media() from None
    if not math.isfinite(duration) or not 0 < duration <= MAX_VIDEO_SECONDS:
        raise invalid_media()
    return {'duration_seconds': round(duration, 3), 'video_codec': 'h264',
            'audio_codec': 'aac' if audio else None,
            'width': dimension(video[0].get('width')), 'height': dimension(video[0].get('height'))}


def probe_video(path, ffprobe_path='ffprobe', timeout=10):
    """Inspect a server-generated temporary file. A missing ffprobe is a 503, never a pass."""
    with open(path, 'rb') as stream:
        if stream.read(12)[4:8] != b'ftyp':
            raise invalid_media()
    command = [ffprobe_path, '-v', 'error', '-protocol_whitelist', 'file', '-print_format', 'json', '-show_format',
               '-show_streams', '-i', str(path)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise invalid_media() from None
    except OSError:
        raise ApiError(503, 'media_unavailable', 'Validação de vídeo indisponível no momento.') from None
    if result.returncode != 0 or len(result.stdout) > MAX_OUTPUT_BYTES:
        raise invalid_media()
    try:
        return summarize(json.loads(result.stdout))
    except ValueError:
        raise invalid_media() from None
