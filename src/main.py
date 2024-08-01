import asyncio
import json
import os
import platform
import shutil
import tarfile
import types
from typing import Any, AsyncGenerator, Callable
import urllib.request
import zipfile

import scrypted_sdk
from scrypted_sdk import ScryptedDeviceBase, DeviceProvider, StreamService, Settings, Setting, ScryptedInterface, ScryptedDeviceType, Scriptable, ScriptSource, Readme

import btop_config


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
            "url": "https://github.com/bjia56/btop-builder/releases/download/v1.3.2-3/btop-linux-x86_64.zip",
            "exe": "btop/bin/btop",
            "extract": extract_zip,
        },
        "aarch64": {
            "url": "https://github.com/bjia56/btop-builder/releases/download/v1.3.2-3/btop-linux-aarch64.zip",
            "exe": "btop/bin/btop",
            "extract": extract_zip,
        },
    },
    "darwin": {
        "x86_64": {
            "url": "https://github.com/bjia56/btop-builder/releases/download/v1.3.2-3/btop-darwin-universal.zip",
            "exe": "btop/bin/btop",
            "extract": extract_zip,
        },
        "arm64": {
            "url": "https://github.com/bjia56/btop-builder/releases/download/v1.3.2-3/btop-darwin-universal.zip",
            "exe": "btop/bin/btop",
            "extract": extract_zip,
        },
    },
}
DOWNLOAD_CACHE_BUST = "20240801-0"


class BtopPlugin(ScryptedDeviceBase, StreamService, DeviceProvider, Settings):

    def __init__(self, nativeId: str = None) -> None:
        super().__init__(nativeId)
        self.config = None
        self.thememanager = None
        self.downloaded = asyncio.ensure_future(self.do_download())
        self.discovered_devices = asyncio.ensure_future(self.do_device_discovery())

    async def do_download(self) -> None:
        try:
            download = DOWNLOADS.get(platform.system().lower(), {}).get(platform.machine().lower())
            if not download:
                raise Exception(f"Unsupported platform {platform.system()} {platform.machine()}")

            if self.should_force_download():
                shutil.rmtree(os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files'), ignore_errors=True)

            self.install = self.downloadFile(download['url'], f'btop-{platform.system()}-{platform.machine()}', download['extract'])
            self.exe = os.path.realpath(os.path.join(self.install, download['exe']))

            if platform.system() != 'Windows':
                try:
                    os.chmod(self.exe, 0o755)
                except:
                    pass

            print("btop executable:", self.exe)

            await self.restart_btop_camera()
        except:
            import traceback
            traceback.print_exc()
            await scrypted_sdk.deviceManager.requestRestart()
            await asyncio.sleep(3600)

    async def do_device_discovery(self) -> None:
        await self.downloaded
        await scrypted_sdk.deviceManager.onDeviceDiscovered({
            "nativeId": "config",
            "name": "btop Configuration",
            "type": ScryptedDeviceType.API.value,
            "interfaces": [
                ScryptedInterface.Readme.value,
                ScryptedInterface.Scriptable.value,
            ],
        })
        await scrypted_sdk.deviceManager.onDeviceDiscovered({
            "nativeId": "thememanager",
            "name": "Theme Manager",
            "type": ScryptedDeviceType.API.value,
            "interfaces": [
                ScryptedInterface.Readme.value,
                ScryptedInterface.Settings.value,
            ],
        })

    async def get_btop_camera(self) -> Any:
        return scrypted_sdk.systemManager.getDeviceByName("@scrypted/btop-camera")

    async def restart_btop_camera(self) -> None:
        btop_camera = await self.get_btop_camera()
        if not btop_camera:
            return
        await btop_camera.putSetting("btop_restart", None)

    async def getDevice(self, nativeId: str) -> Any:
        if nativeId == "config":
            if not self.config:
                self.config = BtopConfig(nativeId, self)
            return self.config
        if nativeId == "thememanager":
            if not self.thememanager:
                self.thememanager = BtopThemeManager(nativeId, self)
            return self.thememanager

        # Management ui v2's PtyComponent expects the plugin device to implement
        # DeviceProvider and return the StreamService device via getDevice.
        return self

    def should_force_download(self) -> bool:
        try:
            filesPath = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files')
            cachebustPath = os.path.join(filesPath, f'cachebust-{platform.system()}-{platform.machine()}')
            if not os.path.exists(cachebustPath):
                return True
            with open(cachebustPath) as f:
                return f.read() != DOWNLOAD_CACHE_BUST
        except:
            return True

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
            cachebustPath = os.path.join(filesPath, f'cachebust-{platform.system()}-{platform.machine()}')
            with open(cachebustPath, 'w') as f:
                f.write(DOWNLOAD_CACHE_BUST)
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

        config = await self.getDevice("config")
        await config.config_reconciled

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
            if not self.storage.getItem('btop_config_migrated'):
                config = await self.getDevice("config")
                await config.saveScript({ "script": value })
                self.storage.setItem('btop_config_migrated', '1')
        elif key == "btop_theme_urls":
            if not self.storage.getItem('btop_theme_urls_migrated'):
                thememanager = await self.getDevice("thememanager")
                await thememanager.putSetting("theme_urls", value)
                self.storage.setItem('btop_theme_urls_migrated', '1')


class BtopConfig(ScryptedDeviceBase, Scriptable, Readme):
    DEFAULT_CONFIG = btop_config.BTOP_CONFIG
    CONFIG = os.path.expanduser(f'~/.config/btop/btop.conf')
    HOME_THEMES_DIR = os.path.expanduser(f'~/.config/btop/themes')

    def __init__(self, nativeId: str, parent: BtopPlugin) -> None:
        super().__init__(nativeId)
        self.parent = parent
        self.config_path = asyncio.ensure_future(self.find_config())
        self.config_reconciled = asyncio.ensure_future(self.reconcile_from_disk())
        self.themes = []

    async def find_config(self) -> str:
        await self.parent.downloaded
        btop = self.parent.exe
        assert btop is not None

        bin_dir = os.path.dirname(btop)
        if platform.system() == 'Windows':
            return os.path.join(bin_dir, 'btop.conf')
        else:
            return BtopConfig.CONFIG

    async def reconcile_from_disk(self) -> None:
        await self.parent.downloaded

        thememanager = await self.parent.getDevice('thememanager')
        await thememanager.themes_loaded

        try:
            btop = self.parent.exe
            assert btop is not None

            config = await self.config_path

            if not os.path.exists(config):
                os.makedirs(os.path.dirname(config), exist_ok=True)
                with open(config, 'w') as f:
                    f.write(BtopConfig.DEFAULT_CONFIG)
            self.print(f"Using config file: {config}")

            with open(config) as f:
                data = f.read()

            while self.storage is None:
                await asyncio.sleep(1)

            if self.storage.getItem('config') and data != self.config:
                with open(config, 'w') as f:
                    f.write(self.config)

            if not self.storage.getItem('config'):
                self.storage.setItem('config', data)

            bin_dir = os.path.dirname(btop)
            if platform.system() == 'Windows':
                theme_dir = os.path.realpath(os.path.join(bin_dir, 'themes'))
                self.print(f"Using themes dir: {theme_dir}")
                if os.path.exists(theme_dir):
                    self.themes = [
                        theme.removesuffix('.theme')
                        for theme in os.listdir(theme_dir)
                        if theme.endswith('.theme')
                    ]
            else:
                config_dir = os.path.realpath(os.path.join(os.path.dirname(bin_dir), 'share', 'btop', 'themes'))
                self.print(f"Using themes dir: {config_dir}, {BtopConfig.HOME_THEMES_DIR}")
                if os.path.exists(config_dir):
                    self.themes = [
                        theme.removesuffix('.theme')
                        for theme in os.listdir(config_dir)
                        if theme.endswith('.theme')
                    ]
                if os.path.exists(BtopConfig.HOME_THEMES_DIR):
                    self.themes.extend([
                        theme.removesuffix('.theme')
                        for theme in os.listdir(BtopConfig.HOME_THEMES_DIR)
                        if theme.endswith('.theme')
                    ])
            self.themes.sort()

            await self.onDeviceEvent(ScryptedInterface.Readme.value, None)
            await self.onDeviceEvent(ScryptedInterface.Scriptable.value, None)
        except:
            import traceback
            traceback.print_exc()

    @property
    def config(self) -> str:
        if self.storage:
            return self.storage.getItem('config') or BtopConfig.DEFAULT_CONFIG
        return BtopConfig.DEFAULT_CONFIG

    async def eval(self, source: ScriptSource, variables: Any = None) -> Any:
        raise Exception("btop configuration cannot be evaluated")

    async def loadScripts(self) -> Any:
        await self.config_reconciled

        return {
            "btop.conf": {
                "name": "btop Configuration",
                "script": self.config,
                "language": "ini",
            }
        }

    async def saveScript(self, script: ScriptSource) -> None:
        await self.config_reconciled
        config = await self.config_path

        self.storage.setItem('config', script['script'])
        await self.onDeviceEvent(ScryptedInterface.Scriptable.value, None)

        updated = False
        with open(config) as f:
            if f.read() != script['script']:
                updated = True

        if updated:
            if not script['script']:
                os.remove(config)
            else:
                with open(config, 'w') as f:
                    f.write(script['script'])

            self.print("Configuration updated, will restart...")
            await scrypted_sdk.deviceManager.requestRestart()

    async def getReadmeMarkdown(self) -> str:
        await self.config_reconciled
        return f"""
# `btop` Configuration

Additional themes can be downloaded from the theme manager page.

Available themes:
{'\n'.join(['- ' + theme for theme in self.themes])}
"""


class DownloaderBase(ScryptedDeviceBase):
    def __init__(self, nativeId: str | None = None):
        super().__init__(nativeId)

    def downloadFile(self, url: str, filename: str):
        try:
            filesPath = os.path.join(os.environ['SCRYPTED_PLUGIN_VOLUME'], 'files')
            fullpath = os.path.join(filesPath, filename)
            if os.path.isfile(fullpath):
                return fullpath
            tmp = fullpath + '.tmp'
            self.print("Creating directory for", tmp)
            os.makedirs(os.path.dirname(fullpath), exist_ok=True)
            self.print("Downloading", url)
            response = urllib.request.urlopen(url)
            if response.getcode() is not None and response.getcode() < 200 or response.getcode() >= 300:
                raise Exception(f"Error downloading")
            read = 0
            with open(tmp, "wb") as f:
                while True:
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    read += len(data)
                    self.print("Downloaded", read, "bytes")
                    f.write(data)
            os.rename(tmp, fullpath)
            return fullpath
        except:
            self.print("Error downloading", url)
            import traceback
            traceback.print_exc()
            raise


class BtopThemeManager(DownloaderBase, Settings, Readme):
    LOCAL_THEME_DIR = os.path.expanduser(f'~/.config/btop/themes')

    def __init__(self, nativeId: str, parent: BtopPlugin) -> None:
        super().__init__(nativeId)
        self.parent = parent
        self.themes_dir = asyncio.ensure_future(self.find_themes_dir())
        self.themes_loaded = asyncio.ensure_future(self.load_themes())

    async def find_themes_dir(self) -> str:
        await self.parent.downloaded
        btop = self.parent.exe
        assert btop is not None

        bin_dir = os.path.dirname(btop)
        if platform.system() == 'Windows':
            return os.path.realpath(os.path.join(bin_dir, 'themes'))
        else:
            return BtopThemeManager.LOCAL_THEME_DIR

    async def load_themes(self) -> None:
        themes_dir = await self.themes_dir
        self.print("Using themes dir:", themes_dir)
        os.makedirs(themes_dir, exist_ok=True)
        try:
            urls = self.theme_urls
            for url in urls:
                filename = url.split('/')[-1]
                fullpath = self.downloadFile(url, filename)
                target = os.path.join(themes_dir, filename)
                shutil.copyfile(fullpath, target)
                self.print("Installed", target)
        except:
            import traceback
            traceback.print_exc()

    @property
    def theme_urls(self) -> list[str]:
        if self.storage:
            urls = self.storage.getItem('theme_urls')
            if urls:
                return json.loads(urls)
        return []

    async def getSettings(self) -> list[Setting]:
        theme_dir = await self.themes_dir
        return [
            {
                "key": "theme_urls",
                "title": "Theme URLs",
                "description": f"List of URLs to download themes from. Themes will be downloaded to {theme_dir}.",
                "value": self.theme_urls,
                "multiple": True,
            },
        ]

    async def putSetting(self, key: str, value: str, forward=True) -> None:
        self.storage.setItem(key, json.dumps(value))
        await self.onDeviceEvent(ScryptedInterface.Settings.value, None)

        self.print("Themes updated, will restart...")
        await scrypted_sdk.deviceManager.requestRestart()

    async def getReadmeMarkdown(self) -> str:
        themes_dir = await self.themes_dir
        return f"""
# Theme Manager

List themes to download and install in the local theme directory. Themes will be installed to `{themes_dir}`.
"""


def create_scrypted_plugin():
    return BtopPlugin()