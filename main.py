import time
import threading
import queue
from pathlib import Path
import subprocess
import shutil
import logging

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
# LOGGING
# ==================================================

LOG_DIR = BASE_DIR / "logs"

LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "archive_manager.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_FILE,
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("ArchiveManager")
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
def get_archive_contents(archive_path):

    command = [
        str(SEVEN_ZIP),
        "l",
        str(archive_path),
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        print(
            f"[ERROR] Could not inspect archive: "
            f"{archive_path.name}"
        )
        return None

    return result.stdout
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

        self.handle_event(event)

    def on_modified(self, event):

        self.handle_event(event)

    def on_moved(self, event):

        path = Path(event.dest_path)

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
        logger.error(
            f"Compression failed: {path.name} | {result.stderr}")
        return False

    logger.info(
        f"Compression successful: "
        f"{path.name} -> {archive_path.name}"
    )

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

        logger.info(
            f"Original deleted: {path.name}"
        )
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
        logger.info(
            f"Archive verified: {archive_path.name}"
        )
        return True

    print(f"[ERROR] Archive verification failed: {archive_path.name}")

    if result.stderr:
        print(result.stderr)

    return False

def decompress_item(archive_path):

    # ==================================================
    # CHECK ARCHIVE
    # ==================================================

    if not is_archive(archive_path):
        logger.info(
            f"Not a supported archive: {archive_path.name}"
        )
        return False

    # ==================================================
    # FINAL OUTPUT PATH
    # ==================================================

    output_path = archive_path.parent / archive_path.stem

    if output_path.exists():

        logger.info(
            f"Output already exists: {output_path.name}"
        )

        return False

    # ==================================================
    # DECOMPRESS
    # ==================================================

    command = [
        str(SEVEN_ZIP),
        "x",
        str(archive_path),
        f"-o{output_path}",
        "-y"
    ]

    logger.info(
        f"Decompression started: {archive_path.name}"
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # ==================================================
    # CHECK 7-ZIP RESULT
    # ==================================================

    if result.returncode != 0:

        logger.error(
            f"Decompression failed: "
            f"{archive_path.name}"
        )

        if result.stderr:
            logger.error(result.stderr)

        # Remove incomplete extraction
        if output_path.exists():

            logger.warning(
                f"Removing incomplete extraction: "
                f"{output_path.name}"
            )

            shutil.rmtree(
                output_path,
                ignore_errors=True
            )

        return False

    logger.info(
        f"Extraction completed: {archive_path.name}"
    )

    # ==================================================
    # VERIFY EXTRACTION
    # ==================================================

    if not verify_extraction(output_path):

        logger.error(
            f"Extraction verification failed: "
            f"{output_path.name}"
        )

        logger.warning(
            f"Keeping archive because verification "
            f"failed: {archive_path.name}"
        )

        # Remove incomplete extraction
        if output_path.exists():

            shutil.rmtree(
                output_path,
                ignore_errors=True
            )

        return False

    logger.info(
        f"Extraction verified: {output_path.name}"
    )

    # ==================================================
    # DELETE ARCHIVE
    # ==================================================

    try:

        archive_path.unlink()

        logger.info(
            f"Archive deleted: {archive_path.name}"
        )

    except Exception as e:

        logger.warning(
            f"Extraction succeeded, but archive "
            f"could not be deleted: {archive_path.name}"
        )

        logger.error(str(e))

        # IMPORTANT:
        # Don't delete the extracted files.

        return True

    # ==================================================
    # SUCCESS
    # ==================================================

    logger.info(
        f"Decompression completed successfully: "
        f"{output_path.name}"
    )

    return True
def verify_extraction(extracted_path):

    if not extracted_path.exists():

        logger.error(
            f"Extraction result does not exist: "
            f"{extracted_path}"
        )

        return False

    if not extracted_path.is_dir():

        logger.error(
            f"Extraction result is not a directory: "
            f"{extracted_path}"
        )

        return False

    try:

        items = list(extracted_path.iterdir())

    except Exception as e:

        logger.error(
            f"Cannot inspect extraction: {e}"
        )

        return False

    if not items:

        logger.error(
            f"Extraction directory is empty: "
            f"{extracted_path}"
        )

        return False

    logger.info(
        f"Extracted {len(items)} top-level item(s)"
    )

    return True
    if not extracted_path.exists():
        print(
            f"[ERROR] Extraction result does not exist: "
            f"{extracted_path}"
        )
        return False

    if not extracted_path.is_dir():
        return False

    try:
        items = list(extracted_path.iterdir())

    except Exception as e:
        print(f"[ERROR] Cannot inspect extraction: {e}")
        return False

    if not items:
        print(
            f"[ERROR] Extraction directory is empty: "
            f"{extracted_path}"
        )
        return False

    print(
        f"[VERIFIED] Extracted {len(items)} "
        f"top-level item(s)"
    )

    return True

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
logger.info("Archive Manager started")
logger.info(f"Watching: {TO_COMPRESS}")
logger.info(f"Watching: {TO_DECOMPRESS}")
logger.info(f"Quiet period: {QUIET_PERIOD} seconds")

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