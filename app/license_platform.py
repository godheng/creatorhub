"""Stable platform identity and suspend-aware elapsed clocks; no calendar time."""
import ctypes
from functools import lru_cache
import hashlib
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path


def _windows_id():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        return winreg.QueryValueEx(key, 'MachineGuid')[0]


def _mac_id():
    data = subprocess.check_output(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice', '-a'], timeout=5)
    value = plistlib.loads(data)[0]['IOPlatformUUID']
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
        raise ValueError('无法读取设备标识')
    return value


def _linux_id():
    for name in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            value = Path(name).read_text(encoding='ascii').strip()
        except OSError:
            continue
        if re.fullmatch('[0-9a-fA-F]{32}', value) and int(value, 16):
            return value
    raise ValueError('无法读取设备标识')


@lru_cache(maxsize=1)
def device_digest():
    try:
        if sys.platform == 'win32':
            value, prefix = _windows_id(), 'creatorhub-device-v1:'
        elif sys.platform == 'darwin':
            value, prefix = _mac_id(), 'creatorhub-device-v1:macos:'
        elif sys.platform.startswith('linux'):
            value, prefix = _linux_id(), 'creatorhub-device-v1:linux:'
        else:
            raise ValueError('不支持当前平台')
        if not isinstance(value, str) or not value.strip():
            raise ValueError('设备标识为空')
        return hashlib.sha256((prefix + value.strip().lower()).encode()).hexdigest()
    except Exception as exc:
        raise ValueError('无法读取设备标识，请联系管理员') from exc


def _windows_clock():
    awake = ctypes.c_ulonglong()
    tick_count = ctypes.windll.kernel32.GetTickCount64
    tick_count.restype = ctypes.c_ulonglong
    total = tick_count()
    if not ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(awake)):
        raise OSError('无法读取运行计时器')
    return total / 1000, awake.value / 1e7


def _mac_clock():
    # Apple: continuous includes sleep; absolute excludes sleep. Timebase -> ns.
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    class Timebase(ctypes.Structure):
        _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]
    info = Timebase()
    lib.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
    lib.mach_timebase_info.restype = ctypes.c_int
    if lib.mach_timebase_info(ctypes.byref(info)) or not info.denom:
        raise OSError('无法读取运行计时器')
    for fn in (lib.mach_continuous_time, lib.mach_absolute_time):
        fn.restype = ctypes.c_uint64
        fn.argtypes = []
    scale = info.numer / info.denom / 1e9
    return lib.mach_continuous_time() * scale, lib.mach_absolute_time() * scale


def clock():
    if sys.platform == 'win32':
        return _windows_clock()
    if sys.platform == 'darwin':
        return _mac_clock()
    if sys.platform.startswith('linux'):
        return time.clock_gettime(time.CLOCK_BOOTTIME), time.clock_gettime(time.CLOCK_MONOTONIC)
    raise OSError('当前平台不支持可信运行计时器')
