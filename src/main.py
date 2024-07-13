import os
import platform
import tarfile
from typing import Any, AsyncGenerator, Callable
import urllib.request
import zipfile

import scrypted_sdk
from scrypted_sdk import ScryptedDeviceBase, DeviceProvider, StreamService


def extract_zip(tmp, fullpath):
    print("Extracting", tmp, "to", fullpath)
    with zipfile.ZipFile(tmp, 'r') as z:
        z.extractall(os.path.dirname(fullpath))


def extract_tbz(tmp, fullpath):
    print("Extracting", tmp, "to", fullpath)
    with tarfile.open(tmp, 'r:bz2') as z:
        z.extractall(os.path.dirname(fullpath))


DOWNLOADS = {
    "windows": {
        "amd64": {
            "url": "https://github.com/aristocratos/btop4win/releases/download/v1.0.4/btop4win-x64.zip",
            "exe": "btop4win.exe",
            "extract": extract_zip,
        }
    },
    "linux": {
        "x86_64": {
            "url": "https://github.com/aristocratos/btop/releases/download/v1.3.2/btop-x86_64-linux-musl.tbz",
            "exe": "bin/btop",
            "extract": extract_tbz,
        },
        "aarch64": {
            "url": "https://github.com/aristocratos/btop/releases/download/v1.3.2/btop-aarch64-linux-musl.tbz",
            "exe": "bin/btop",
            "extract": extract_tbz,
        },
    },
}


class BtopPlugin(ScryptedDeviceBase, StreamService, DeviceProvider):

    def __init__(self, nativeId: str = None) -> None:
        super().__init__(nativeId)

        download = DOWNLOADS.get(platform.system().lower(), {}).get(platform.machine().lower())
        if not download:
            raise Exception(f"Unsupported platform {platform.system()} {platform.machine()}")

        self.install = self.downloadFile(download['url'], 'btop', download['extract'])
        self.exe = os.path.join(self.install, download['exe'])

        print("btop executable:", self.exe)

    # Management ui v2's PtyComponent expects the plugin device to implement
    # DeviceProvider and return the StreamService device via getDevice.
    async def getDevice(self, nativeId: str) -> Any:
        return self

    def downloadFile(self, url: str, filename: str, extract: Callable[[str, str], None] = None) -> str:
        try:
            filesPath = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files')
            fullpath = os.path.join(filesPath, filename)
            if os.path.isfile(fullpath):
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


def create_scrypted_plugin():
    return BtopPlugin()