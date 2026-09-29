# PyInstaller spec for MyfuelaiConnector.exe - build with build.ps1.
#
# One-folder (onedir) build on purpose: a one-file exe unpacks its DLLs into a new random
# %TEMP%\_MEI* folder every start, which application-control tools like ThreatLocker block and
# can't whitelist. With onedir every file sits in the install folder, where each can be signed and
# hashed once per release.
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["connector.py"],
    pathex=["."],
    hiddenimports=[
        "win32timezone",       # needed by pywin32 services
        "servicemanager",
        "win32ts",
        "pythoncom",
        "pywintypes",
        "win32com.client",
        # PDI/SmartTank are not imported by main.py while commented out; bundle them anyway so
        # re-enabling doesn't need a spec change.
        "relay.integrations.pdi",
        "relay.integrations.smarttank",
    ],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MyfuelaiConnector",
    console=True,
    upx=False,  # UPX-packed binaries trip antivirus/application control
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="MyfuelaiConnector",
)
