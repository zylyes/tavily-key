# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('assets\\tavily.ico', 'assets')]
# 新前端产物（web/ 下 vite build 输出，以项目根为基准；工作目录=项目根）。
# 缺失时构建直接失败：dashboard.py 在运行期要求 web/dist/index.html 必须
# 存在，不保留旧前端回退。
if not os.path.isfile('web\\dist\\index.html'):
    print('[Tavily.spec] ERROR: web\\dist\\index.html not found - cannot bundle the frontend;')
    print('[Tavily.spec] run "cd web && npm ci && npm run build" first (build_win.bat does this).')
    print('[Tavily.spec] Build aborted: the web frontend is REQUIRED (no legacy fallback).')
    raise SystemExit(1)
datas.append(('web\\dist', 'web/dist'))
binaries = []
hiddenimports = ['mcp_server', 'tavily_proxy', 'backup', 'updater', 'wiki_docs',
                 'routes.admin', 'routes.keys', 'routes.logs', 'routes.research',
                 'routes.services', 'routes.update', 'routes.usage',
                 'uvicorn.loops.auto', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on']
hiddenimports += collect_submodules('mcp.server')
hiddenimports += collect_submodules('mcp.shared')
hiddenimports += collect_submodules('mcp.transport')
hiddenimports += collect_submodules('mcp.session')
tmp_ret = collect_all('tavily')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app\\dashboard.py'],
    pathex=['app'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Tavily',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\tavily.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Tavily',
)
