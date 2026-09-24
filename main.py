import time
import threading
import queue
from pathlib import Path
import subprocess
import shutil

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
            
            # To decompress
        elif str(path).startswith(str(TO_DECOMPRESS)):
            root = TO_DECOMPRESS
            # Only decompress archive files
            if not is_archive(path):
                return
        else:
            return
        
        top_level =  get_top_level_item(path, root)
        
        if top_level is None or top_level == root:
            return
        
        #don't put archives from ToDecompress into the queue

        if root == TO_DECOMPRESS and not is_archive(top_level):
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

            if path.parent == TO_COMPRESS:
                compress_item(path)

            elif path.parent == TO_DECOMPRESS:
                decompress_item(path)

        finally:
            processing_queue.task_done()


# COMPRESSING FUNCTION
def compress_item(path):

    # Never compress an archive
    if is_archive(path):
        print(f"[INFO] Skipping archive: {path.name}")
        return False

    archive_path = path.parent / f"{path.name}.7z"

    if archive_path.exists():
        print(f"[INFO] Archive already exists: {archive_path.name}")
        return False

    command = [
        str(SEVEN_ZIP),
        "a",
        "-t7z",
        f"-mx={COMPRESSION_LEVEL}",
        str(archive_path),
        path.name
    ]

    result = subprocess.run(
        command,
        cwd=path.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        print(f"[ERROR] Compression failed: {result.stderr}")
        return False

    print(f"[COMPRESSED] {path.name} -> {archive_path.name}")

    # ==============================
    # VERIFY ARCHIVE
    # ==============================

    if not verify_archive(archive_path):
        print(
            f"[WARNING] Keeping original because "
            f"archive verification failed."
        )
        return False

    # ==============================
    # DELETE ORIGINAL
    # ==============================

    try:

        if path.is_dir():
            import shutil
            shutil.rmtree(path)

        else:
            path.unlink()

        print(f"[DELETED] Original removed: {path.name}")
        return True

    except Exception as e:

        print(f"[ERROR] Could not delete original: {path}")
        print(e)

        return False

def verify_archive(archive_path):
    print(f"[VERIFY] Checking archive: {archive_path.name}")

    command = [
        str(SEVEN_ZIP),
        "t",
        str(archive_path),
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode == 0:
        print(f"[VERIFIED] Archive is valid: {archive_path.name}")
        return True

    print(f"[ERROR] Archive verification failed: {archive_path.name}")

    if result.stderr:
        print(result.stderr)

    return False

def decompress_item(archive_path):

    # Only process supported archives
    if not is_archive(archive_path):
        print(f"[INFO] Not a supported archive: {archive_path.name}")
        return False

    output_path = archive_path.parent / archive_path.stem

    if output_path.exists():
        print(f"[INFO] Output already exists: {output_path.name}")
        return False

    # Temporary extraction directory
    temp_path = archive_path.parent / f".{archive_path.stem}_extracting"

    if temp_path.exists():
        print(f"[INFO] Temporary directory already exists: {temp_path.name}")
        return False

    command = [
        str(SEVEN_ZIP),
        "x",
        str(archive_path),
        f"-o{temp_path}",
        "-y"
    ]

    print(f"\n[DECOMPRESS] {archive_path.name}")

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # --------------------------------
    # Extraction failed
    # --------------------------------

    if result.returncode != 0:

        print(
            f"[ERROR] Decompression failed: "
            f"{archive_path.name}"
        )

        print(result.stderr)

        # Remove incomplete extraction
        if temp_path.exists():
            shutil.rmtree(temp_path, ignore_errors=True)

        return False

    print("[EXTRACTED] Temporary extraction completed")

    # --------------------------------
    # Verify extraction
    # --------------------------------

    if not verify_extraction(temp_path):

        print(
            "[ERROR] Extraction verification failed."
        )

        shutil.rmtree(temp_path, ignore_errors=True)

        return False

    print("[VERIFIED] Extraction looks valid")

    # --------------------------------
    # Move extraction to final location
    # --------------------------------

    try:

        temp_path.rename(output_path)

    except Exception as e:

        print(
            f"[ERROR] Could not move extracted data: {e}"
        )

        shutil.rmtree(temp_path, ignore_errors=True)

        return False

    # --------------------------------
    # Delete archive
    # --------------------------------

    try:

        archive_path.unlink()

        print(
            f"[DELETED] Archive removed: "
            f"{archive_path.name}"
        )

    except Exception as e:

        print(
            f"[WARNING] Extraction succeeded, "
            f"but archive could not be deleted:"
        )

        print(e)

    print(
        f"[OK] Decompression completed: "
        f"{output_path.name}"
    )

    return True

def verify_extraction(extracted_path):
    """
    Verify that the extracted result exists
    and contains something.
    """

    if not extracted_path.exists():
        print(f"[ERROR] Extraction result does not exist: {extracted_path}")
        return False

    if extracted_path.is_dir():
        try:
            next(extracted_path.iterdir())
            return True
        except StopIteration:
            print(f"[ERROR] Extracted directory is empty: {extracted_path}")
            return False

    # For a single extracted file
    if extracted_path.is_file():
        return extracted_path.stat().st_size > 0

    return False

# =====================
# =============================
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