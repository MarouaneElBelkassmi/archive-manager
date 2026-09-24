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
ARCHIVE_EXTENSIONS = {
    ".7z",
    ".zip",
    ".rar",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
}

def is_archive(path):
    return path.is_file() and path.suffix.lower() in ARCHIVE_EXTENSIONS


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
        path=Path(event.src_path)

        if str(path).startswith(str(TO_COMPRESS)):
            root = TO_COMPRESS

            # Dont compress existing archives

            if is_archive(path):
                return
        elif str(path).startswith(str(TO_DECOMPRESS)):
            root = TO_DECOMPRESS
        else:
            return
        
        top_level =  get_top_level_item(path, root)
        
        if top_level is None or top_level == root:
            return
        
        #don't put archives from ToDecompress into the queue

        if root == TO_DECOMPRESS and is_archive(top_level):
            return
        
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

    # Never compress an archive
    if is_archive(path):
        print(f"[INFO] Skipping archive: {path.name}")
        return False
    archive_path= path.parent / f"{path.name}.7z"

    if archive_path.exists():
        print(f"[INFO] Archive already exists: {archive_path.name}")
        return False
    
    command = [
        str(SEVEN_ZIP),
        "a",  # Add to archive
        "-t7z",
        f"-mx={COMPRESSION_LEVEL}",  # Compression level
        str(archive_path),
        path.name
    ]

    result = subprocess.run(
        command,
        cwd = path.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        print(f"[ERROR] Compression failed: {result.stderr}")
        return False
    print(f"[COMPRESSED] {path.name} -> {archive_path.name}")
    return True

    
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