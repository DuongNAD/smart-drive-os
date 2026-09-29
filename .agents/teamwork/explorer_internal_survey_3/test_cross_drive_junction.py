import os
import subprocess
import tempfile
import shutil

# C: is NTFS (Windows system drive)
# D: is our target drive (let's check D: filesystem and junction to D:)
test_dir_d = r"D:\test_junction_probe_sd"
os.makedirs(test_dir_d, exist_ok=True)
with open(os.path.join(test_dir_d, "test_file.txt"), "w") as f:
    f.write("Cross drive test content")

c_temp = tempfile.mkdtemp(dir=os.environ.get("TEMP", r"C:\Users\Admin\AppData\Local\Temp"))
c_link = os.path.join(c_temp, "link_to_d")

try:
    cmd = ["cmd.exe", "/c", "mklink", "/J", c_link, test_dir_d]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print("mklink returncode:", res.returncode)
    print("mklink stdout:", res.stdout.strip())
    print("mklink stderr:", res.stderr.strip())

    if res.returncode == 0:
        probe_file = os.path.join(c_link, "test_file.txt")
        print("Probe file exists through C: link:", os.path.exists(probe_file))
        if os.path.exists(probe_file):
            with open(probe_file, "r") as f:
                print("Content read through junction:", f.read())
            # Test writing through junction
            with open(os.path.join(c_link, "from_c.txt"), "w") as f:
                f.write("written via junction on C")
            print("File on D exists:", os.path.exists(os.path.join(test_dir_d, "from_c.txt")))
        
        target_read = os.readlink(c_link)
        print("os.readlink:", target_read)
finally:
    try:
        os.unlink(c_link)
    except Exception as e:
        print("unlink error:", e)
    shutil.rmtree(c_temp, ignore_errors=True)
    shutil.rmtree(test_dir_d, ignore_errors=True)
