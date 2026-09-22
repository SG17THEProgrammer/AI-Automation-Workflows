import os
import shutil
import re
from dotenv import load_dotenv

load_dotenv()


def organize_media_files(directory_path):
    # This regex looks for common prefixes (IMG, VID, PXL, Screenshot, MVIMG) 
    # followed optionally by a dash or underscore, and captures the 4-digit year.
    pattern = re.compile(r'^(?:IMG|VID|PXL|Screenshot|MVIMG|Screenrecorder|vidma_recorder|null)[_-]?(\d{4})', re.IGNORECASE)

    # Count for a summary at the end
    files_moved = 0
    
    # Loop through everything in the target directory
    for filename in os.listdir(directory_path):
        file_path = os.path.join(directory_path, filename)

        # Skip folders, we only want to move files
        if os.path.isdir(file_path):
            continue

        # Check if the filename matches our expected pattern
        match = pattern.search(filename)
        
        if match:
            year = match.group(1)
            folder_name = f"Photos from {year}"
            folder_path = os.path.join(directory_path, folder_name)

            # Create the year folder if it doesn't already exist
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
                print(f"Created folder: {folder_name}")

            # Define where the file is going
            destination_path = os.path.join(folder_path, filename)

            # Move the file (handles both the media file and the .supplemental-metadata file)
            try:
                shutil.move(file_path, destination_path)
                print(f"Moved: {filename} -> {folder_name}/")
                files_moved += 1
            except Exception as e:
                print(f"Error moving {filename}: {e}")

    print(f"\nDone! Successfully moved {files_moved} files.")

# --- RUN THE SCRIPT ---
# Replace the path below with the actual path to your unzipped folder
target_folder = os.getenv("target_folder") 

organize_media_files(target_folder)