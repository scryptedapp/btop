import asyncio
import os
import platform
import shutil
import tarfile
import types
from typing import Any, AsyncGenerator, Callable
import urllib.request
import zipfile

import scrypted_sdk
from scrypted_sdk import ScryptedDeviceBase, DeviceProvider, StreamService, Settings, Setting, ScryptedInterface


# patch SystemManager.getDeviceByName
def getDeviceByName(self, name: str) -> scrypted_sdk.ScryptedDevice:
    for check in self.systemState:
        state = self.systemState.get(check, None)
        if not state:
            continue
        checkInterfaces = state.get('interfaces', None)
        if not checkInterfaces:
            continue
        interfaces = checkInterfaces.get('value', [])
        if ScryptedInterface.ScryptedPlugin.value in interfaces:
            checkPluginId = state.get('pluginId', None)
            if not checkPluginId:
                continue
            pluginId = checkPluginId.get('value', None)
            if not pluginId:
                continue
            if pluginId == name:
                return self.getDeviceById(check)
        checkName = state.get('name', None)
        if not checkName:
            continue
        if checkName.get('value', None) == name:
            return self.getDeviceById(check)
scrypted_sdk.systemManager.getDeviceByName = types.MethodType(getDeviceByName, scrypted_sdk.systemManager)


def extract_zip(tmp, fullpath):
    print("Extracting", tmp, "to", fullpath)
    with zipfile.ZipFile(tmp, 'r') as z:
        z.extractall(fullpath)


def extract_tbz(tmp, fullpath):
    print("Extracting", tmp, "to", fullpath)
    with tarfile.open(tmp, 'r:bz2') as z:
        z.extractall(fullpath)


DOWNLOADS = {
    "windows": {
        "amd64": {
            "url": "https://github.com/aristocratos/btop4win/releases/download/v1.0.4/btop4win-x64.zip",
            "exe": "btop4win/btop4win.exe",
            "extract": extract_zip,
        }
    },
    "linux": {
        "x86_64": {
            "url": "https://github.com/aristocratos/btop/releases/download/v1.3.2/btop-x86_64-linux-musl.tbz",
            "exe": "btop/bin/btop",
            "extract": extract_tbz,
        },
        "aarch64": {
            "url": "https://github.com/aristocratos/btop/releases/download/v1.3.2/btop-aarch64-linux-musl.tbz",
            "exe": "btop/bin/btop",
            "extract": extract_tbz,
        },
    },
    "darwin": {
        "x86_64": {
            "url": "https://github.com/bjia56/btop-builder/releases/download/v1.3.2-0/btop-darwin-universal.zip",
            "exe": "btop/bin/btop",
            "extract": extract_zip,
        },
        "arm64": {
            "url": "https://github.com/bjia56/btop-builder/releases/download/v1.3.2-0/btop-darwin-universal.zip",
            "exe": "btop/bin/btop",
            "extract": extract_zip,
        },
    },
}


class BtopPlugin(ScryptedDeviceBase, StreamService, DeviceProvider, Settings):

    def __init__(self, nativeId: str = None) -> None:
        super().__init__(nativeId)
        self.downloaded = asyncio.ensure_future(self.do_download())

    async def do_download(self) -> None:
        try:
            download = DOWNLOADS.get(platform.system().lower(), {}).get(platform.machine().lower())
            if not download:
                raise Exception(f"Unsupported platform {platform.system()} {platform.machine()}")

            self.install = self.downloadFile(download['url'], f'btop-{platform.system()}-{platform.machine()}', download['extract'])
            self.exe = os.path.realpath(os.path.join(self.install, download['exe']))

            if platform.system() != 'Windows':
                os.chmod(self.exe, 0o755)

            print("btop executable:", self.exe)

            # restructure themes
            if platform.system() != "Windows":
                bin_dir = os.path.dirname(self.exe)
                base_dir = os.path.dirname(bin_dir)
                themes_dir = os.path.join(base_dir, 'themes')

                os.makedirs(os.path.join(base_dir, 'share', 'btop'), exist_ok=True)
                try:
                    shutil.copytree(themes_dir, os.path.join(base_dir, 'share', 'btop', 'themes'), dirs_exist_ok=False)
                except:
                    pass

            await self.restart_btop_camera()
        except:
            import traceback
            traceback.print_exc()
            await scrypted_sdk.deviceManager.requestRestart()
            await asyncio.sleep(3600)

    async def restart_btop_camera(self) -> None:
        btop_camera = scrypted_sdk.systemManager.getDeviceByName("@scrypted/btop-camera")
        if not btop_camera:
            return
        await btop_camera.putSetting("btop_restart", None)

    # Management ui v2's PtyComponent expects the plugin device to implement
    # DeviceProvider and return the StreamService device via getDevice.
    async def getDevice(self, nativeId: str) -> Any:
        return self

    def downloadFile(self, url: str, filename: str, extract: Callable[[str, str], None] = None) -> str:
        try:
            filesPath = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files')
            fullpath = os.path.join(filesPath, filename)
            if os.path.exists(fullpath):
                return fullpath
            tmp = fullpath + '.tmp'
            print("Creating directory for", tmp)
            os.makedirs(os.path.dirname(fullpath), exist_ok=True)
            print("Downloading", url)
            response = urllib.request.urlopen(url)
            if response.getcode() < 200 or response.getcode() >= 300:
                raise Exception(f"Error downloading")
            read = 0
            with open(tmp, "wb") as f:
                while True:
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    read += len(data)
                    print("Downloaded", read, "bytes")
                    f.write(data)
            if extract:
                extract(tmp, fullpath)
            else:
                os.rename(tmp, fullpath)
            return fullpath
        except:
            print("Error downloading", url)
            import traceback
            traceback.print_exc()
            raise

    async def connectStream(self, input: AsyncGenerator[Any, Any] = None, options: Any = None) -> Any:
        core = scrypted_sdk.systemManager.getDeviceByName("@scrypted/core")
        termsvc = await core.getDevice("terminalservice")
        termsvc_direct = await scrypted_sdk.sdk.connectRPCObject(termsvc)
        return await termsvc_direct.connectStream(input, {
            'cmd': [self.exe, '--utf-force']
        })

    async def getSettings(self) -> list[Setting]:
        await self.downloaded
        return [
            {
                "key": "btop_executable",
                "title": "btop Executable Path",
                "description": "Path to the downloaded btop executable.",
                "value": self.exe,
                "readonly": True,
            }
        ]

    async def putSetting(self, key: str, value: str) -> None:
        # this allows the btop-camera plugin to update the configs
        if key == "btop_config":
            print("Migration not yet implemented, what we got:\n" + value)


def create_scrypted_plugin():
    return BtopPlugin()