# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir + 无控制台）。

console=False 是「不再挂着 cmd」的根本：控制台子系统一关，就没有黑窗，
进程活多久由托盘和窗口决定，而不是由那个黑窗决定。

注意：console=False 之后 sys.stdout 是 None，项目里任何裸 print 都会抛
AttributeError —— 所以 app/ 里统一走 main._log 和 desktop.ShellLog。
"""

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app/web', 'app/web'),
        ('app/schema.sql', 'app'),
        ('assets', 'assets'),
    ],
    # 这几个是动态 import 的，PyInstaller 静态分析看不到，必须显式声明
    hiddenimports=[
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        'clr_loader',
        'pythonnet',
        'pystray._win32',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 只跑 Windows 桌面，别的 GUI 后端装不上也用不到
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'cefpython3'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='刷题调度',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='刷题调度',
)
