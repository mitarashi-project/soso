"""Offline, read-only asset inspection. Python 3.10+."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import warnings

from PIL import Image


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def pixels(im):
    rgba = im.convert('RGBA')
    alpha = rgba.getchannel('A')
    histogram = alpha.histogram()
    return rgba, alpha.getbbox(), sum(histogram[1:])


def safe_pair_path(value):
    """Validate portable relative paths before any report/output is created."""
    # Reject Windows paths even when this program is running on Linux.
    # Backslashes are not accepted: configuration uses forward slashes only.
    return (bool(value) and not value.startswith('/')
            and '\\' not in value and ':' not in value
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and all(part not in ('', '.', '..') for part in value.split('/')))


def audit(source, output, config=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    root = source if source.is_dir() else source.parent
    if not source.exists() or output == root or root in output.parents:
        raise ValueError('Input must exist; output must be outside the input directory.')
    config = config or {}
    pattern = re.compile(config.get('name_regex', r'.+'))
    tiny = config.get('min_pixels', 8)
    if type(tiny) is not int or tiny < 0:
        raise ValueError('min_pixels must be a nonnegative integer')
    pairs = config.get('pairs', [])
    if not isinstance(pairs, list) or any(not isinstance(p, list) or len(p) != 2 or
            any(not isinstance(n, str) for n in p) for p in pairs):
        raise ValueError('pairs must contain pairs of relative filenames')
    if any(not safe_pair_path(name) for pair in pairs for name in pair):
        raise ValueError('pairs must contain safe relative paths using forward slashes')
    report = {'schema_version': 1, 'files': [], 'warnings': [], 'errors': []}

    def warn(name, code, reason):
        report['warnings'].append({'file': name, 'code': code, 'reason': reason,
                                   'check': '元の素材と意図したパーツ構成を目視で確認してください。'})

    candidates = sorted(source.rglob('*')) if source.is_dir() else [source]
    files = [p for p in candidates if p.suffix.lower() in ('.png', '.psd') and p.is_file()]
    for path in files:
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or root not in path.resolve().parents:
            warn(name, 'symlink_skipped', '入力外へのリンクは検査しません。')
            continue
        item = {'path': name}
        report['files'].append(item)
        try:
            item['sha256'] = digest(path)
            if not pattern.fullmatch(path.name):
                warn(name, 'name', '指定された命名規則と一致しません。')
            if path.suffix.lower() == '.psd':
                try:
                    from psd_tools import PSDImage
                except ImportError:
                    warn(name, 'psd_unavailable', 'psd-tools未導入のためPSD内部は未検査です。')
                    item['inspection'] = 'skipped'
                    continue
                psd = PSDImage.open(path)
                item.update(size=list(psd.size), layers=[])
                for layer in psd.descendants():
                    entry = {'name': layer.name, 'visible': layer.visible,
                             'bbox': list(layer.bbox), 'kind': layer.kind, 'empty': None}
                    if not layer.is_group() and layer.has_pixels():
                        image = layer.topil()
                        if image is not None:
                            entry['empty'] = pixels(image)[2] == 0
                    item['layers'].append(entry)
                continue
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as im:
                    if im.format != 'PNG':
                        raise ValueError('Not a PNG')
                    rgba, bbox, count = pixels(im)
                    item.update(size=list(im.size), mode=im.mode,
                                alpha=('A' in im.getbands() or 'transparency' in im.info),
                                bbox=list(bbox) if bbox else None, nontransparent_pixels=count)
                    # Zero invisible RGB: visually identical transparent pixels compare equally.
                    normalized = Image.new('RGBA', rgba.size)
                    mask = rgba.getchannel('A').point(lambda a: 255 if a else 0)
                    normalized.paste(rgba, (0, 0), mask)
                    item['pixel_sha256'] = hashlib.sha256(
                        str(im.size).encode() + normalized.tobytes()).hexdigest()
                    if not item['alpha']:
                        warn(name, 'no_alpha', '透明度情報がありません。')
                    if not count:
                        warn(name, 'empty', '描画画素がありません。')
                    elif count < tiny:
                        warn(name, 'tiny', '描画画素数が設定値未満です。')
                    if count == im.width * im.height:
                        warn(name, 'full_rectangle', '非透明領域がキャンバス全体の矩形です。背景混入を確認してください。')
        except Exception as exc:
            # Do not expose exception messages containing absolute paths or layer data.
            report['errors'].append({'file': name, 'type': type(exc).__name__,
                                     'reason': 'ファイルを検査できませんでした。形式・破損・サイズを確認してください。'})

    pngs = {f['path']: f for f in report['files'] if 'pixel_sha256' in f}
    for key, code in [('sha256', 'duplicate_file'), ('pixel_sha256', 'duplicate_image')]:
        seen = {}
        for item in report['files']:
            value = item.get(key)
            if value is None:
                continue
            if value in seen:
                warn(item['path'], code, '同一内容: ' + seen[value])
            else:
                seen[value] = item['path']
    inferred = set()
    for name in pngs:
        match = re.match(r'^(.*)_([LR])(\.[^.]+)$', name, re.I)
        if match:
            inferred.add((match[1] + '_L' + match[3], match[1] + '_R' + match[3]))
    lookup = {k.casefold(): v for k, v in pngs.items()}
    for left, right in sorted(inferred | {tuple(p) for p in pairs}):
        a, b = lookup.get(left.casefold()), lookup.get(right.casefold())
        if not a or not b:
            warn(left + ' / ' + right, 'missing_pair', '左右の片方が存在しないか、読み取りに失敗しています。')
        elif a['size'] != b['size']:
            warn(left + ' / ' + right, 'pair_size', '左右のキャンバス寸法が異なります。')
    if not files:
        warn('.', 'no_assets', '対象PNG・PSDがありません。')
    report['exit_code'] = 2 if report['errors'] else (1 if report['warnings'] else 0)
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting previous reports, symlinks or source files.
    names = ['report.json', 'report.html', 'sha256.json']
    if any((output / n).exists() or (output / n).is_symlink() for n in names):
        raise ValueError('Output already contains report files; choose a new directory.')
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    manifest = {f['path']: f['sha256'] for f in report['files'] if 'sha256' in f}
    page = '<!doctype html><meta charset="utf-8"><title>Live2D素材監査</title>'
    page += '<style>body{font:16px sans-serif;max-width:1000px;margin:2em auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
    page += '<h1>Live2D素材監査</h1><p>警告: %d / 読み取り失敗: %d</p>' % (len(report['warnings']), len(report['errors']))
    page += '<p>警告は確認候補です。素材の正しさを保証するものではありません。</p><pre>' + html.escape(encoded) + '</pre>'
    for name, body in zip(names, [encoded, page, json.dumps(manifest, ensure_ascii=False, indent=2)]):
        with (output / name).open('x', encoding='utf-8') as stream:
            stream.write(body)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--config')
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding='utf-8')) if args.config else {}
        result = audit(args.input, args.output, config)
        print('Audit complete. Exit code:', result['exit_code'])
        return result['exit_code']
    except Exception as exc:
        print('Audit failed (' + type(exc).__name__ + '). Check input/config and use a fresh output directory.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
