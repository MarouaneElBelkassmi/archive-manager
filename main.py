import time
import threading
import queue
from pathlib import Path
import subprocess

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# ==================================================
# CONFIGURATION
# ==================================================

BASE_DIR = Path(__file__).parent

TO_COMPRESS = BASE_DIR / "ToCompress"
TO_DECOMPRESS = BASE_DIR / "ToDecompress"

# Number of seconds without activity before processing
QUIET_PERIOD = 5

# ==================================================
# 7-ZIP CONFIGURATION
# ==================================================

SEVEN_ZIP = Path(r"C:\Program Files\7-Zip\7z.exe")

# Compression level:
#
# 0 = Store
# 1 = Fastest
# 3 = Fast
# 5 = Normal
# 7 = Maximum
# 9 = Ultra
#
COMPRESSION_LEVEL = 5

if not SEVEN_ZIP.exists():

    print("ERROR: 7-Zip was not found.")

    print(f"Expected location:")
    print(SEVEN_ZIP)

    print()
    print("Install 7-Zip or change SEVEN_ZIP in main.py.")

    raise SystemExit(1)

# ==================================================
# CREATE FOLDERS
# ==================================================

TO_COMPRESS.mkdir(exist_ok=True)
TO_DECOMPRESS.mkdir(exist_ok=True)


# ==================================================
# QUEUE
# ==================================================

processing_queue = queue.Queue()


# ==================================================
# ACTIVITY TRACKING
# ==================================================

last_activity = {}

activity_lock = threading.Lock()


# ==================================================
# FIND TOP-LEVEL ITEM
# ==================================================

def get_top_level_item(path, root):
    """
    Convert a path inside root to the item directly
    inside root.

    Example:

        ToCompress/MyGame/data/file.bin

    becomes:

        ToCompress/MyGame
    """

    try:

        relative = path.relative_to(root)

        # The first part is the item directly inside root.
        parts = relative.parts

        if not parts:
            return None

        return root / parts[0]

    except ValueError:

        return None


# ==================================================
# EVENT HANDLER
# ==================================================

class ArchiveHandler(FileSystemEventHandler):

    def handle_event(self, event):

        path = Path(event.src_path)

        # ------------------------------------------
        # Determine which root folder this belongs to
        # ------------------------------------------

        if str(path).startswith(str(TO_COMPRESS)):

            root = TO_COMPRESS

        elif str(path).startswith(str(TO_DECOMPRESS)):

            root = TO_DECOMPRESS

        else:

            return

        # ------------------------------------------
        # Find the item directly inside the root
        # ------------------------------------------

        top_level = get_top_level_item(path, root)

        if top_level is None:
            return

        # ------------------------------------------
        # Ignore the root itself
        # ------------------------------------------

        if top_level == root:
            return

        # ------------------------------------------
        # Update activity timestamp
        # ------------------------------------------

        with activity_lock:

            last_activity[str(top_level)] = time.time()

    def on_created(self, event):

        path = Path(event.src_path)

        print(f"[EVENT] Created: {path}")

        self.handle_event(event)

    def on_modified(self, event):

        self.handle_event(event)

    def on_moved(self, event):

        path = Path(event.dest_path)

        print(f"[EVENT] Moved: {path}")

        # Create a fake event-like object for handling.
        class TempEvent:
            src_path = str(path)
            is_directory = event.is_directory

        self.handle_event(TempEvent())

    def on_deleted(self, event):

        self.handle_event(event)


# ==================================================
# ACTIVITY CHECKER
# ==================================================

def activity_checker():

    while True:

        current_time = time.time()

        ready_items = []

        with activity_lock:

            for path_string, last_time in list(last_activity.items()):

                elapsed = current_time - last_time

                if elapsed >= QUIET_PERIOD:

                    ready_items.append(path_string)

                    del last_activity[path_string]

        # ------------------------------------------
        # Put ready items into queue
        # ------------------------------------------

        for path_string in ready_items:

            path = Path(path_string)

            if path.exists():

                print(
                    f"\n[READY] {path.name} "
                    f"has been quiet for {QUIET_PERIOD} seconds."
                )

                processing_queue.put(path)

        time.sleep(1)


# ==================================================
# PROCESSING WORKER
# ==================================================

def processing_worker():

    while True:

        path = processing_queue.get()

        try:

            print(f"\n[QUEUE] Processing: {path}")

            # ======================================
            # ONLY PROCESS ITEMS IN ToCompress
            # ======================================

            if path.parent == TO_COMPRESS:

                archive_path = compress_item(path)

                if archive_path:

                    # ==================================
                    # VERIFY
                    # ==================================

                    if verify_archive(archive_path):

                        print(
                            f"[SUCCESS] Verified: "
                            f"{archive_path.name}"
                        )

                        # ==================================
                        # DELETE ORIGINAL
                        # ==================================

                        print(
                            f"[DELETE] Removing original: "
                            f"{path.name}"
                        )

                        if path.is_dir():

                            import shutil

                            shutil.rmtree(path)

                        else:

                            path.unlink()

                        print("[DONE] Original removed.")

                    else:

                        print(
                            "[SAFETY] Archive is invalid."
                        )

                        print(
                            "[SAFETY] Original was NOT deleted."
                        )

            # ======================================
            # DECOMPRESSION WILL COME NEXT
            # ======================================

            elif path.parent == TO_DECOMPRESS:

                print(
                    "[INFO] Decompression will be "
                    "implemented next."
                )

        except Exception as error:

            print(f"[ERROR] {error}")

        finally:

            processing_queue.task_done()


# COMPRESSING FUNCTION
def compress_item(path):
    """
    Compress a file or folder using 7-Zip.
    """

    archive_path = path.parent / f"{path.name}.7z"

    print()
    print("====================================")
    print(f"Compressing: {path.name}")
    print(f"Archive:     {archive_path.name}")
    print("====================================")

    # ------------------------------------------
    # Build the input path
    # ------------------------------------------

    if path.is_dir():

        # Add everything inside the folder
        source = str(path / "*")

    else:

        # Normal file
        source = str(path)

    # ------------------------------------------
    # 7-Zip command
    # ------------------------------------------

    command = [
        str(SEVEN_ZIP),
        "a",
        "-t7z",
        f"-mx={COMPRESSION_LEVEL}",
        str(archive_path),
        source
    ]

    print("[7-Zip] Starting compression...")

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # ------------------------------------------
    # Check result
    # ------------------------------------------

    if result.returncode != 0:

        print("[ERROR] Compression failed!")

        print(result.stdout)
        print(result.stderr)

        if archive_path.exists():
            archive_path.unlink()

        return None

    print("[7-Zip] Compression completed.")

    return archive_path


def verify_archive(archive_path):
    """
    Verify that the 7z archive is readable and
    passes 7-Zip's integrity test.
    """

    print(f"[VERIFY] Checking {archive_path.name}...")

    command = [
        str(SEVEN_ZIP),
        "t",
        str(archive_path)
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode == 0:

        print("[VERIFY] Archive is valid.")

        return True

    print("[VERIFY] Archive verification FAILED.")

    print(result.stderr)

    return False
# ==================================================
# START WATCHER
# ==================================================

event_handler = ArchiveHandler()

observer = Observer()

# IMPORTANT:
# We now watch recursively so that we can receive
# activity events from inside a folder.
#
# BUT we do NOT scan the folder ourselves.

observer.schedule(
    event_handler,
    str(TO_COMPRESS),
    recursive=True
)

observer.schedule(
    event_handler,
    str(TO_DECOMPRESS),
    recursive=True
)

observer.start()


# ==================================================
# START BACKGROUND THREADS
# ==================================================

checker_thread = threading.Thread(
    target=activity_checker,
    daemon=True
)

checker_thread.start()


worker_thread = threading.Thread(
    target=processing_worker,
    daemon=True
)

worker_thread.start()


# ==================================================
# START PROGRAM
# ==================================================

print("====================================")
print("      Archive Manager Started")
print("====================================")

print(f"Watching: {TO_COMPRESS}")
print(f"Watching: {TO_DECOMPRESS}")

print()
print(f"Quiet period: {QUIET_PERIOD} seconds")
print("Waiting for files...")
print("Press CTRL+C to stop.")
print()


# ==================================================
# KEEP RUNNING
# ==================================================

try:

    while True:

        time.sleep(1)

except KeyboardInterrupt:

    print("\nStopping Archive Manager...")

    observer.stop()

observer.join()