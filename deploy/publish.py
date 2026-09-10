#!/usr/bin/env python3
"""Publish static master without executing repository code."""
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

STATE = Path('/var/lib/exchange-publisher')
SITE = Path('/srv/exchange-public')
REPO = STATE / 'repo.git'
URL = 'https://github.com/vetrovoy/rs-exchange-landing.git'
PAGES = ('index.html', 'steps.html', 'exchange.html')


def git(*args):
    return subprocess.check_output(['git', '--git-dir', str(REPO), *args], timeout=90)


def validate(root):
    def resource(value, parent):
        url = urlsplit(value)
        if url.scheme or url.netloc or not url.path:
            return
        target = (parent / unquote(url.path)).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError('Missing or invalid local resource: ' + value)

    class CheckHTML(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            for key in ('src', 'href'):
                if key in attrs:
                    resource(attrs[key], root)

    for name in PAGES:
        contents = (root / name).read_text()
        if not contents.strip():
            raise ValueError('Empty page: ' + name)
        CheckHTML().feed(contents)
    css = (root / 'styles.css').read_text()
    for value in re.findall(r'url\([\s\"\']*([^\)\"\']+)', css):
        resource(value.strip(), root)


def switch(target):
    temporary = SITE / 'current.next'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(SITE / 'current')


def main():
    STATE.mkdir(exist_ok=True)
    if not REPO.exists():
        subprocess.run(['git', 'init', '--bare', str(REPO)], check=True)
    git('fetch', '--depth=1', URL, 'refs/heads/master')
    revision = git('rev-parse', 'FETCH_HEAD').decode().strip()
    current = SITE / 'current'
    previous = os.readlink(current) if current.is_symlink() else None
    target = 'releases/' + revision
    if previous == target:
        print('Already published ' + revision, flush=True)
        return
    releases = SITE / 'releases'
    releases.mkdir(exist_ok=True)
    staging = releases / (revision + '.staging')
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    archive = git('archive', revision, *PAGES, 'styles.css', 'assets')
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        for item in files:
            destination = staging / item.name
            if not destination.resolve().is_relative_to(staging.resolve()):
                raise ValueError('Invalid archive path')
            if item.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif item.isfile():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(files.extractfile(item).read())
            else:
                raise ValueError('Only regular files and directories are allowed')
    validate(staging)
    release = releases / revision
    if release.exists():
        shutil.rmtree(staging)
    else:
        staging.rename(release)
    switch(target)
    try:
        if previous:
            expected = (release / 'index.html').read_bytes()
            for route in ('exchange', 'ex', 'ch', 'rub', 'vnd'):
                with urlopen(f'https://vietnamlive.ru/{route}/?deploy={revision}', timeout=15) as response:
                    if response.read() != expected:
                        raise ValueError('Public page does not match release: ' + route)
    except Exception:
        switch(previous)
        raise
    print('Published ' + revision, flush=True)
    # Keep the current version and four recent versions for rollback.
    keep = {revision, Path(previous).name if previous else ''}
    older = sorted((p for p in releases.iterdir() if re.fullmatch('[0-9a-f]{40}', p.name)), key=lambda p: p.stat().st_mtime, reverse=True)
    keep.update(p.name for p in older[:5])
    for path in older:
        if path.name not in keep:
            shutil.rmtree(path)


if __name__ == '__main__':
    main()
