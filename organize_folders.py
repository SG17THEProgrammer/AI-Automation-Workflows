import os
import shutil
from dotenv import load_dotenv

load_dotenv()

# Common file extensions for sorting
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.webp', '.gif', '.bmp', '.tiff', '.dng'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.3gp', '.m4v', '.wmv', '.webm'}

def organize_inside_year_folders(parent_directory):
    files_moved = 0

    # Look through the main directory for "Photos from XXXX" folders
    for entry in os.listdir(parent_directory):
        folder_path = os.path.join(parent_directory, entry)

        # Process only directories that start with "Photos from "
        if os.path.isdir(folder_path) and entry.lower().startswith("photos from "):
            print(f"\n📁 Organizing contents of: {entry}")

            # Define subfolder paths inside the year folder
            photos_dir = os.path.join(folder_path, "Photos")
            videos_dir = os.path.join(folder_path, "Videos")
            metadata_dir = os.path.join(folder_path, "Metadata")
            others_dir = os.path.join(folder_path, "Other")

            # Loop through all items inside this year folder
            for filename in os.listdir(folder_path):
                file_path = os.path.join(folder_path, filename)

                # Skip existing subfolders so we don't move a folder into itself
                if os.path.isdir(file_path):
                    continue

                # Determine file type based on suffix / extension
                lower_name = filename.lower()
                dest_subfolder = None

                if lower_name.endswith('.supplemental-metadata') or lower_name.endswith('.json'):
                    dest_subfolder = metadata_dir
                else:
                    _, ext = os.path.splitext(lower_name)
                    if ext in IMAGE_EXTENSIONS:
                        dest_subfolder = photos_dir
                    elif ext in VIDEO_EXTENSIONS:
                        dest_subfolder = videos_dir
                    else:
                        dest_subfolder = others_dir

                # Create the specific subfolder if needed and move the file
                if dest_subfolder:
                    if not os.path.exists(dest_subfolder):
                        os.makedirs(dest_subfolder)

                    destination_path = os.path.join(dest_subfolder, filename)
                    try:
                        shutil.move(file_path, destination_path)
                        files_moved += 1
                    except Exception as e:
                        print(f"Error moving {filename}: {e}")

    print(f"\nDone! Successfully sorted {files_moved} items into subfolders.")

# --- RUN THE SCRIPT ---
# Point this to the main folder containing your "Photos from [Year]" folders
target_directory = os.getenv("target_directory")

organize_inside_year_folders(target_directory)