"""Release wiring checks; no network, credentials, or real publication."""
from pathlib import Path
import os
import re
import subprocess
import sys
import unittest
import configparser

import yaml

ROOT = Path(__file__).resolve().parents[1]


class WindowsReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load(
            (ROOT / '.github/workflows/windows-installer.yml').read_text(encoding='utf-8'),
            Loader=yaml.BaseLoader,
        )
        cls.jobs = cls.workflow['jobs']

    def test_triggers_and_permissions(self):
        self.assertEqual(self.workflow['on']['push']['tags'], ['v*'])
        self.assertIn('workflow_dispatch', self.workflow['on'])
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})
        release = self.jobs['release']
        self.assertEqual(release['needs'], 'windows')
        self.assertEqual(release['permissions'], {'contents': 'write'})
        self.assertIn("github.event_name == 'push'", release['if'])
        self.assertIn("'refs/tags/v'", release['if'])
        self.assertEqual(self.workflow['concurrency']['cancel-in-progress'], 'false')

    def test_version_validation(self):
        step = next(s for s in self.jobs['windows']['steps'] if s.get('id') == 'version')
        pattern = re.search("-cnotmatch '([^']+)'", step['run']).group(1)
        for value in ('0.3.0', '1.2.3.4', '0.10.0'):
            self.assertRegex(value, pattern)
        for value in ('', 'v0.3.0', '1.2', '1.2.3-rc1', '../1.2.3', '1.2.3;echo bad'):
            self.assertIsNone(re.fullmatch(pattern, value))
        self.assertIn('GITHUB_REF_NAME.Substring(1)', step['run'])
        self.assertIn('GITHUB_OUTPUT', step['run'])

    def test_chinese_notes_are_validated_before_build_and_used_for_release(self):
        from desktop.release import release_notes
        steps = self.jobs['windows']['steps']
        names = [s.get('name', s.get('uses')) for s in steps]
        self.assertLess(names.index('Prepare Chinese release notes'), names.index('Build desktop renderer'))
        notes = next(s for s in steps if s.get('name') == 'Prepare Chinese release notes')
        self.assertIn('--notes-output dist/installer/RELEASE_NOTES.md', notes['run'])
        script = self.jobs['release']['steps'][-1]['run']
        self.assertIn('--notes-file RELEASE_NOTES.md', script)
        self.assertNotIn('--generate-notes', script)
        default_version = self.workflow['on']['workflow_dispatch']['inputs']['version']['default']
        self.assertRegex(release_notes(ROOT, default_version), r'[\u4e00-\u9fff]')

    def test_builder_and_installer_preview_defaults_are_consistent(self):
        version = '0.2.0'
        builder = (ROOT / 'desktop/build_windows.py').read_text(encoding='utf-8')
        installer = (ROOT / 'desktop/installer.iss').read_text(encoding='utf-8')
        self.assertIn(f'parser.add_argument("--version", default="{version}")', builder)
        self.assertIn(f'#define AppVersion "{version}"', installer)

    def test_windows_build_preserves_chinese_output_in_redirected_subprocesses(self):
        environment = {**os.environ, 'PYTHONIOENCODING': 'cp1252', 'PYTHONUTF8': '0'}
        environment.update(self.jobs['windows'].get('env', {}))
        self.assertEqual(environment['PYTHONUTF8'], '1')
        self.assertEqual(environment['PYTHONIOENCODING'], 'utf-8')
        # 模拟英文构建机的管道输出，验证中文日志和子进程 UTF-8 模式。
        result = subprocess.run([sys.executable, '-c',
                                 'import sys; print("中文发布说明"); print(sys.flags.utf8_mode)'],
                                env=environment, capture_output=True, check=True, timeout=15)
        self.assertEqual(result.stdout.decode('utf-8').splitlines(), ['中文发布说明', '1'])

    def test_build_order_and_checksums(self):
        steps = self.jobs['windows']['steps']
        names = [s.get('name', s.get('uses')) for s in steps]
        self.assertLess(names.index('Test commercial desktop contracts'),
                        names.index('Package Python, application and offline guide'))
        self.assertLess(names.index('Smoke-test frozen resources and service lifecycle'), names.index('Compile installer'))
        self.assertLess(names.index('Download verified WebView2 bootstrapper'), names.index('Compile installer'))
        compile_step = next(s for s in steps if s.get('name') == 'Compile installer')
        self.assertIn('-Algorithm SHA256', compile_step['run'])
        self.assertIn('-Encoding ascii', compile_step['run'])
        self.assertIn('desktop/check_installer_language.py --compiler', compile_step['run'])
        upload = next(s for s in steps if s.get('uses', '').startswith('actions/upload-artifact@'))
        download = self.jobs['release']['steps'][0]
        self.assertEqual(upload['with']['name'], download['with']['name'])

    def test_renderer_assets_are_verified_in_the_frozen_client(self):
        steps = self.jobs['windows']['steps']
        names = [s.get('name', s.get('uses')) for s in steps]
        render = next(s for s in steps if s.get('name') == 'Build desktop renderer')['run']
        self.assertLess(render.index('npm ci'), render.index('npm run build:ui'))
        self.assertLess(render.index('npm run build:ui'), render.index('npm run build:desktop'))
        self.assertIn('if ($LASTEXITCODE -ne 0)', render.split('npm run build:ui')[1])
        self.assertLess(names.index('Build desktop renderer'), names.index('Package Python, application and offline guide'))
        smoke = (ROOT / 'desktop/smoke_windows.py').read_text(encoding='utf-8')
        for asset in ('app.js', 'engine-settings.js', 'workbench.js', 'workbench.css',
                      'appearance.js', 'appearance.css'):
            self.assertIn(f'"{asset}"', smoke)
        self.assertIn('response.read() ==', smoke)
        self.assertIn('"/guide/guide.css"', smoke)
        self.assertIn('bundled.read_bytes() ==', smoke)
        self.assertIn('"desktop" / "web" / name', smoke)

    def test_draft_upload_publish_and_rerun_guard(self):
        script = self.jobs['release']['steps'][-1]['run']
        self.assertLess(script.index('sha256sum --check'), script.index('gh release create'))
        self.assertLess(script.index("'.isDraft == true'"), script.index('gh release upload'))
        self.assertLess(script.index('gh release upload'), script.index('gh release edit'))
        self.assertIn('--verify-tag --draft', script)
        self.assertIn('--draft=false', script)
        self.assertIn('aispace-Setup-$APP_VERSION-windows-x64.exe', script)

    def test_runtime_is_verified_and_required(self):
        script = (ROOT / 'desktop/prepare_webview2.ps1').read_text(encoding='utf-8')
        installer = (ROOT / 'desktop/installer.iss').read_text(encoding='utf-8')
        self.assertIn('Get-AuthenticodeSignature', script)
        self.assertIn('Microsoft Corporation', script)
        self.assertIn("$signature.Status -ne 'Valid'", script)
        self.assertIn('Flags: dontcopy', installer)
        self.assertIn('function PrepareToInstall', installer)
        self.assertIn('if HasWebView2() then Exit', installer)
        self.assertIn("'/silent /install'", installer)
        self.assertIn('HKLM32', installer)
        self.assertIn('HKCU', installer)

    def test_commercial_bundle_contains_updater_and_license_resources(self):
        builder = (ROOT / 'desktop/build_windows.py').read_text(encoding='utf-8')
        self.assertIn('"--onefile"', builder)
        self.assertIn('"aispaceUpdater"', builder)
        self.assertIn('(helper, "desktop")', builder)
        for filename in ("COMMERCIAL-NOTICE.txt", "brand.json", "license_public.pem"):
            self.assertIn(filename, builder)

    def test_installer_accepts_an_explicit_build_directory(self):
        installer = (ROOT / 'desktop/installer.iss').read_text(encoding='utf-8')
        self.assertIn('#ifndef AppSourceDir', installer)
        self.assertIn('Source: "{#AppSourceDir}\\*"', installer)
        self.assertIn('#define AppSourceDir "..\\dist\\windows\\aispace"', installer)

    def test_installer_keeps_commercial_upgrade_identity_and_data_location(self):
        installer = (ROOT / 'desktop/installer.iss').read_text(encoding='utf-8')
        self.assertIn('AppId={{93FD4B37-6436-4CE0-8249-BC487CC5A062}', installer)
        self.assertIn(r'DefaultDirName={localappdata}\Programs\aispace', installer)
        self.assertIn(r'{localappdata}\CreatorHub\user-data', installer)
        self.assertIn("FileExists(AddBackslash(Location) + 'aispace.exe')", installer)
        self.assertIn("FileExists(AddBackslash(Location) + 'CreatorHub.exe')", installer)
        self.assertIn('IsSubPathOrEqual(SelectedDir, DataDir)', installer)
        self.assertNotIn('AppUpdatesURL=', installer)

    def test_delta_inventory_is_built_after_setup_and_published_with_checksums(self):
        steps = self.jobs['windows']['steps']
        names = [s.get('name', s.get('uses')) for s in steps]
        self.assertLess(names.index('Compile installer'), names.index('Build file delta and release inventory'))
        self.assertLess(names.index('Fetch verified previous release inventory'), names.index('Build file delta and release inventory'))
        script = self.jobs['release']['steps'][-1]['run']
        self.assertIn('aispace-Update-$APP_VERSION-windows-x64.json', script)
        self.assertIn('aispace-Delta-*-to-', script)
        self.assertIn('"${assets[@]}"', script)
        dependencies = next(step for step in steps if step.get('name') == 'Install build dependencies')
        self.assertIn('-c desktop/constraints-windows.txt', dependencies['run'])

    def test_installer_defaults_to_vendored_simplified_chinese(self):
        installer = (ROOT / 'desktop/installer.iss').read_text(encoding='utf-8')
        languages = installer.split('[Languages]', 1)[1].split('[LangOptions]', 1)[0]
        self.assertIn('Name: "chinesesimplified"; MessagesFile: "languages\\ChineseSimplified.isl"', languages)
        self.assertEqual(len(re.findall(r'^Name:', languages, re.M)), 1)
        self.assertIn('ShowLanguageDialog=no', installer)
        self.assertIn('UsePreviousLanguage=no', installer)
        from app.brand import PRODUCT_NAME
        self.assertIn(f'AppVerName={PRODUCT_NAME} v{{#AppVersion}}', installer)
        self.assertNotIn('compiler:Languages', languages)
        self.assertNotIn('Default.isl', languages)
        self.assertIn('DialogFontName=Microsoft YaHei UI', installer)

    def test_chinese_messages_cover_wizard_buttons_errors_and_uninstall(self):
        messages = configparser.ConfigParser(interpolation=None)
        messages.optionxform = str
        messages.read(ROOT / 'desktop/languages/ChineseSimplified.isl', encoding='utf-8-sig')
        self.assertEqual(messages['LangOptions']['LanguageID'], '$0804')
        for key in ('SetupWindowTitle', 'ButtonNext', 'ButtonBack', 'ButtonCancel', 'ButtonInstall',
                    'ButtonFinish', 'ButtonWizardBrowse', 'WizardSelectDir', 'SelectDirDesc',
                    'SelectDirBrowseLabel', 'DiskSpaceMBLabel', 'WizardSelectTasks', 'WizardReady',
                    'WizardPreparing', 'WizardInstalling', 'FinishedHeadingLabel',
                    'ConfirmUninstall', 'UninstallStatusLabel', 'SetupFileCorrupt'):
            with self.subTest(key=key):
                self.assertRegex(messages['Messages'][key], r'[\u4e00-\u9fff]')
        self.assertIn('[name]', messages['Messages']['SelectDirDesc'])
        self.assertIn('[mb]', messages['Messages']['DiskSpaceMBLabel'])
        self.assertIn('%1', messages['Messages']['SetupWindowTitle'])

    def test_custom_installer_messages_and_translation_license_are_wired(self):
        installer = (ROOT / 'desktop/installer.iss').read_text(encoding='utf-8')
        for key in ('InstallingWebView2', 'WebView2StartFailed', 'WebView2Required', 'UninstallKeepData'):
            self.assertIn(f"CustomMessage('{key}')", installer)
            value = re.search(rf'^{key}=(.+)$', installer, re.M).group(1)
            self.assertRegex(value, r'[\u4e00-\u9fff]')
        self.assertIn('Description: "{cm:CreateDesktopIcon}"', installer)
        self.assertIn('Description: "{cm:LaunchCreatorHub}"', installer)
        self.assertNotIn('Description: "Create a desktop shortcut"', installer)
        self.assertNotIn('Description: "Launch CreatorHub"', installer)
        license_path = ROOT / 'desktop/languages/ChineseSimplified-LICENSE.txt'
        self.assertIn('MIT License', license_path.read_text(encoding='utf-8'))
        self.assertIn('ChineseSimplified-LICENSE.txt', (ROOT / 'desktop/build_windows.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
