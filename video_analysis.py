import os
import time
import tkinter as tk
from tkinter import filedialog
from dotenv import load_dotenv

from google import genai
from google.genai import types

# Load environment variables from .env file
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment variables.")

def select_video_file():
    # Create a hidden Tkinter window and open a file dialog
    root = tk.Tk()
    root.withdraw()
    video_path = filedialog.askopenfilename(
        title="Select a Video File",
        filetypes=[("Video Files", "*.mp4 *.mov *.avi *.mpeg *.mpg *.wmv *.flv *.mkv")]
    )
    return video_path

def main():
    video_path = select_video_file()
    if not video_path:
        print("No video file selected.")
        return

    client = genai.Client(api_key=API_KEY)

    # Check the size of the selected video file
    file_size = os.path.getsize(video_path)
    print("File size (bytes):", file_size)
    threshold = 20 * 1024 * 1024  # 20 MB

    # Hardcoded prompt to be sent with the video
    prompt = "Summarize this video."

    if file_size < threshold:
        # Use inline upload for small videos (<20 MB)
        print("Using inline upload...")
        with open(video_path, 'rb') as f:
            video_bytes = f.read()
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=types.Content(
                parts=[
                    types.Part(text=prompt),
                    types.Part(
                        inline_data=types.Blob(data=video_bytes, mime_type="video/mp4")
                    )
                ]
            )
        )
        print("Response from Gemini (inline):")
        print(response.text)
    else:
        # Use File API upload for larger videos (>=20 MB)
        print("Uploading video file via File API...")
        video_file = client.files.upload(file=video_path)
        print(f"Upload initiated. File URI: {video_file.uri}")

        # Poll until the file is fully processed
        while video_file.state.name == "PROCESSING":
            print("Processing video file...", end='', flush=True)
            time.sleep(1)
            video_file = client.files.get(name=video_file.name)
            print(".", end='', flush=True)
        print("\nFile state:", video_file.state.name)
        if video_file.state.name == "FAILED":
            raise ValueError("Video file processing failed.")

        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=[video_file, prompt]
        )
        print("Response from Gemini (file upload):")
        print(response.text)

if __name__ == "__main__":
    main()
