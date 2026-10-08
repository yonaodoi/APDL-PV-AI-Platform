"""Refresh legacy UI colours in the current APDL Flask project."""
import argparse
import colorsys
import re
import shutil
from datetime import datetime
from pathlib import Path

HEX = re.compile(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b')
RGB = re.compile(r'(rgba?)\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(\s*,\s*[.\d]+)?\s*\)', re.I)
STYLE = re.compile(r'(<style\b[^>]*>)(.*?)(</style>)', re.I | re.S)
INLINE = re.compile(r'(\bstyle\s*=\s*)([\"\'])(.*?)\2', re.I | re.S)

def replacement(value):
    digits = value.lstrip('#')
    if len(digits) == 3:
        digits = ''.join(c * 2 for c in digits)
    red, green, blue = [int(digits[i:i+2], 16) / 255 for i in (0, 2, 4)]
    hue, light, saturation = colorsys.rgb_to_hls(red, green, blue)
    hue *= 360
    legacy = 245 <= hue <= 335 or (335 < hue <= 360 and light > .975)
    if not legacy or saturation < .025:
        return value
    if light > .985: return '#ffffff'
    if light > .945: return '#f4f9f6'
    if light > .885: return '#e5f2ed'
    if light > .77: return '#cfe1d6'
    if saturation > .5:
        return '#147d64' if light > .48 else '#0d6652'
    if light > .53: return '#71817c'
    if light > .35: return '#49685a'
    return '#18342f'

def colours(text):
    text = HEX.sub(lambda m: replacement(m.group()), text)
    def rgb_change(match):
        rgb = [int(match.group(i)) for i in (2, 3, 4)]
        if any(c > 255 for c in rgb): return match.group()
        original = '#' + ''.join(f'{c:02x}' for c in rgb)
        changed = replacement(original)
        if changed == original: return match.group()
        values = [int(changed[i:i+2], 16) for i in (1, 3, 5)]
        return match.group(1) + '(' + ', '.join(str(v) for v in values) + (match.group(5) or '') + ')'
    return RGB.sub(rgb_change, text)

def refresh(path, text):
    if path.suffix == '.css': return colours(text)
    text = STYLE.sub(lambda m: m.group(1) + colours(m.group(2)) + m.group(3), text)
    text = INLINE.sub(lambda m: m.group(1) + m.group(2) + colours(m.group(3)) + m.group(2), text)
    return text

def run(project, check):
    project = project.resolve()
    if not (project / 'app/templates/base.html').is_file():
        raise SystemExit('Choose the Flask project folder containing app/templates/base.html.')
    files = sorted((project / 'app/templates').rglob('*.html'))
    files += sorted((project / 'app/static/css').rglob('*.css'))
    changes = []
    for path in files:
        original = path.read_text(encoding='utf-8-sig')
        revised = refresh(path, original)
        if revised != original: changes.append((path, revised))
    print(f'Scanned {len(files)} templates and stylesheets; {len(changes)} files need legacy colour updates.')
    for path, _ in changes: print(path.relative_to(project).as_posix())
    if check or not changes: return
    backup = project / 'design_backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    # Back up all planned files before applying any changes.
    for path, _ in changes:
        saved = backup / path.relative_to(project)
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, saved)
    for path, revised in changes:
        temporary = path.with_name(path.name + '.design-temp')
        temporary.write_text(revised, encoding='utf-8')
        temporary.replace(path)
    print(f'Design updated. Original files backed up to: {backup}')
    print('Restart Flask and refresh your browser with Ctrl + F5.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(r'C:\Users\user\APDL_PV_AI_Platform.worktrees\user-authentication-login'))
    parser.add_argument('--check', action='store_true', help='List affected files without changing them.')
    args = parser.parse_args()
    run(args.project, args.check)
