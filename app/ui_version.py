"""Content-addressed workbench assets and UI request compatibility."""
import hashlib
import re
import sys
from functools import lru_cache
from pathlib import Path


def ui_manifest(web: Path):
    files = [web / 'index.html', *sorted(web.glob('*.js')), *sorted(web.glob('*.css'))]
    version_file = Path(getattr(sys, '_MEIPASS', web.parent.parent)) / 'desktop-version.txt'
    if version_file.is_file(): files.append(version_file)
    stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_ctime_ns, p.stat().st_size) for p in files)
    return _manifest_cached(str(web), stamp)


@lru_cache(maxsize=4)
def _manifest_cached(web_path, stamp):
    web = Path(web_path)
    html = (web / 'index.html').read_bytes()
    names = sorted(set(re.findall(r'/static/([\w.-]+\.(?:js|css))', html.decode('utf-8'))))
    assets = {name: hashlib.sha256((web / name).read_bytes()).hexdigest()[:16] for name in names}
    digest = hashlib.sha256((web / 'index.html').read_bytes())
    for file, *_ in stamp:
        if Path(file).name == 'desktop-version.txt': digest.update(Path(file).read_bytes())
    for name, value in assets.items():
        digest.update(f'{name}:{value}'.encode())
    return {'version': digest.hexdigest()[:20], 'assets': assets}


def version_html(html, manifest):
    html = re.sub(r'/static/([\w.-]+\.(?:js|css))(?:\?v=[^"\s]*)?',
                  lambda m: f'/static/{m[1]}?v={manifest["assets"][m[1]]}' if m[1] in manifest['assets'] else m[0], html)
    return html.replace('<head>', '<head><meta name="creatorhub-ui-version" content="'+manifest['version']+'">', 1)
