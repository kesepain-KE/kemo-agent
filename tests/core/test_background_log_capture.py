"""Running jobs must expose small flushed writes before their process exits."""
import subprocess
import sys
import threading
import time

from run.tools.background_worker import _capture_stream


def test_buffered_child_pipe_is_visible_before_exit(tmp_path):
    output = tmp_path / "live.stdout.log"
    process = subprocess.Popen(
        [sys.executable, "-u", "-c",
         "import sys; print('started', flush=True); sys.stdin.readline(); print('finished', flush=True)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    reader = threading.Thread(target=_capture_stream, args=(process.stdout, output), daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if output.exists() and b"started" in output.read_bytes():
                break
            time.sleep(0.02)
        assert process.poll() is None, "the log must arrive while the child is still running"
        assert b"started" in output.read_bytes()
        assert process.stdin is not None
        process.stdin.write(b"\n")
        process.stdin.flush()
        assert process.wait(timeout=5) == 0
        reader.join(timeout=5)
        assert not reader.is_alive()
        assert b"finished" in output.read_bytes()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stdin:
            process.stdin.close()
