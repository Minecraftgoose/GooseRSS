
import os
import sys

ROOT = os.path.abspath(SPECPATH)

datas = [
    (os.path.join(ROOT, 'web'), 'web'),
    (os.path.join(ROOT, 'assets'), 'assets'),
    (os.path.join(ROOT, 'licenses'), 'licenses'),
]

hiddenimports = [
    'webview',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
    'webview.platforms.mshtml',
    'pystray',
    'pystray._win32',
    'winotify',
    'requests',
    'urllib3',
    'feedparser',
    'feedparser.datetimes',
    'feedparser.api',
    'feedparser.encodings',
    'feedparser.sanitizer',
    'feedparser.html',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'email.mime.multipart',
    'xml.etree.ElementTree',
]

excludes = [
    'tkinter',
    'unittest',
    'pydoc',
    'doctest',
    'lib2to3',
    'numpy',
    'scipy',
    'matplotlib',
    'pytest',
    'IPython',
    'notebook',
]

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GooseRSS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    version=os.path.join(ROOT, 'version_info.txt'),
    icon=os.path.join(ROOT, 'assets', 'app.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        'python38.dll',
        'python3.dll',
        'qwindows.dll',
        'vcruntime140.dll',
        'msvcp140.dll',
    ],
    name='GooseRSS',
)
