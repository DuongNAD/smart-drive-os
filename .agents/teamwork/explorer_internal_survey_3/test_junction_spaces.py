import os
import tempfile
import subprocess
import shutil

td = tempfile.mkdtemp(prefix='test space ')
try:
    target = os.path.join(td, 'target dir with space')
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, 'data.txt'), 'w', encoding='utf-8') as f:
        f.write('content')
    junction = os.path.join(td, 'junction link with space')
    
    # We should pass as list to subprocess.run without shell=True to avoid cmd quoting bugs
    # Note: mklink is an internal cmd.exe command, so we do ['cmd.exe', '/c', 'mklink', '/J', junction, target]
    cmd = ['cmd.exe', '/c', 'mklink', '/J', junction, target]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print('mklink returncode:', res.returncode)
    print('mklink stdout:', res.stdout.strip())
    print('mklink stderr:', res.stderr.strip())
    print('os.path.exists junction:', os.path.exists(junction))
    target_read = os.readlink(junction)
    print('os.readlink raw:', target_read)
    normalized_target = target_read.replace('\\\\?\\', '').replace('\\??\\', '')
    print('normalized:', normalized_target)
    print('matches:', os.path.normpath(normalized_target).lower() == os.path.normpath(target).lower())
finally:
    try:
        os.unlink(junction)
    except Exception as e:
        print('unlink error:', e)
    shutil.rmtree(td, ignore_errors=True)
