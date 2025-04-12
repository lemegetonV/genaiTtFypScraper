import os
import json
import requests
import logging
import sqlite3
import time
from datetime import datetime
from dotenv import load_dotenv
from tikapi import TikAPI, ValidationException, ResponseException
from google import genai
from google.genai import types
import sys

# Determine base directory (executable's dir if frozen, script's dir otherwise)
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(__file__)

# Set up logging (using the globally defined base_dir)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(base_dir, 'scraper.log')),
        logging.StreamHandler()
    ]
)

# Load environment variables
# Load environment variables (using the globally defined base_dir)
load_dotenv(dotenv_path=os.path.join(base_dir, '.env'))

class TikTokScraper:
    
    def __init__(self):
        
        # Load API keys and account token
        self.api_key = os.getenv('API_KEY')
        self.rapid_api_key = os.getenv('RAPIDAPI_KEY')
        self.gemini_api_key = os.getenv('GEMINI_API_KEY_1')
        
        if not all([self.api_key, self.rapid_api_key, self.gemini_api_key]):    
            raise ValueError("Missing keys in .env file")
        
        self.api = TikAPI(self.api_key)
        
        # Use the globally defined base_dir for instance paths
        
        self.output_dir = os.path.join(base_dir, 'OUTPUT')
        self.output_dir_filtered = os.path.join(base_dir, 'OUTPUT_FILTERED')
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.output_dir_filtered, exist_ok=True)

        # List of Gemini models to alternate between
        self.gemini_models = ['gemini-2.0-flash-lite', 'gemini-2.0-flash']
        self.current_gemini_index = 0 # Index for the next model to use

        # Set up a connection to a SQLite database stored in your project directory.
        # This file will be created if it does not exist.
        # Connect to SQLite database in the base directory
        self.conn = sqlite3.connect(os.path.join(base_dir, 'video_lookup.db'))
        self.cursor = self.conn.cursor()

        # Create a table to store processed video IDs if it doesn't already exist.
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_videos (
            video_id TEXT PRIMARY KEY,
            is_downloaded TEXT DEFAULT 'no',
            is_filtered TEXT DEFAULT 'no'
            )
        ''')
        self.conn.commit()


    
    def save_video_details(self, video_data, directory):
        """Save video details to a JSON file"""
        details_file = os.path.join(directory, 'details.json')
        with open(details_file, 'w', encoding='utf-8') as f:
            json.dump(video_data, f, indent=4, ensure_ascii=False)

    def process_video(self, item, response):
        """Process each video item from the response"""
        
        try:
            # Create directory for video
            video_id = item.get('id')
            video_dir = os.path.join(self.output_dir, f"video_{video_id}")
            os.makedirs(video_dir, exist_ok=True)

            # Save video details
            self.save_video_details(item, video_dir)
            
            # Download video
            print(video_id + " downloading...")
            userid = item.get('author').get('uniqueId')
            videoUrl = f"https://www.tiktok.com/@{userid}/video/{video_id}"
            url = "https://tiktok-video-downloader-api.p.rapidapi.com/media"
            querystring = {"videoUrl":f"{videoUrl}"}
            headers = {
                "x-rapidapi-key": f"{self.rapid_api_key}",
                "x-rapidapi-host": "tiktok-video-downloader-api.p.rapidapi.com"
            }
            response2 = requests.get(url, headers=headers, params=querystring)
            response2.raise_for_status()  # Raise an HTTPError for bad responses (4XX and 5XX)
            downloadUrl = response2.json()['downloadUrl']
            print(downloadUrl)
            video_path = os.path.join(video_dir, 'video.mp4')
            with requests.get(downloadUrl, stream=True) as r:
                r.raise_for_status()
                with open(video_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            # Update the database to mark the video as downloaded
            self.cursor.execute("UPDATE processed_videos SET is_downloaded = 'yes' WHERE video_id = ?", (video_id,))
            self.conn.commit()

            # Gemini Client Initialization
            client = genai.Client(api_key=self.gemini_api_key)

            # Hardcoded prompt to be sent with the video
            # Read prompt from input_prompt.txt file
            # Look for input_prompt.txt in the base directory
            prompt_path = os.path.join(base_dir, 'input_prompt.txt')
            try:
                with open(prompt_path, 'r', encoding='utf-8') as f:
                    prompt = f.read().strip()
            except FileNotFoundError:
                logging.error("input_prompt.txt file not found")
                raise
            except Exception as e:
                logging.error(f"Error reading prompt file: {str(e)}")
                raise
            
            # Check the size of the selected video file
            file_size = os.path.getsize(video_path)
            print("File size (bytes):", file_size)
            threshold = 20 * 1024 * 1024  # 20 MB

            if file_size < threshold:
                
                # Use inline upload for small videos (<20 MB)
                print("Using inline upload...")
                with open(video_path, 'rb') as f:
                    video_bytes = f.read()
                
                # Select the next model and update the index
                selected_model_name = self.gemini_models[self.current_gemini_index]
                self.current_gemini_index = (self.current_gemini_index + 1) % len(self.gemini_models)
                print(f"Using Gemini model: {selected_model_name}")

                response3 = client.models.generate_content(
                    model=selected_model_name,
                    config={
                        'response_mime_type': 'application/json',
                    },  
                    contents=types.Content(
                        parts=[
                            types.Part(text=prompt),
                            types.Part(
                                inline_data=types.Blob(data=video_bytes, mime_type="video/mp4")
                            )
                        ]
                    )
                )
                
                # Extract the response from Gemini
                print("Response from Gemini (inline):")
                json_str = response3.text
                data = json.loads(json_str)
                if isinstance(data, list): # if Gemini response is a list instead of a dict
                    data = data[0]
                print(data)
                
                # Save the Gemini response to a file
                time.sleep(1)
                analysis_file = os.path.join(video_dir, 'analysis.json')
                time.sleep(1)
                with open(analysis_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                
                # Check the response for specific keys
                if any(data.get(key) == "yes" for key in ["filter_passed"]):
                    # Move the video directory to the OUTPUT_FILTERED directory
                    filtered_video_dir = os.path.join(self.output_dir_filtered, f"video_{video_id}")
                    try:
                        time.sleep(1)
                        os.rename(video_dir, filtered_video_dir)
                    except PermissionError as e:
                        logging.error(f"PermissionError while moving directory {video_dir} to {filtered_video_dir}: {str(e)}")
                        time.sleep(1)  # Wait briefly and retry
                        os.rename(video_dir, filtered_video_dir)
                    print(f"Video {video_id} moved to filtered directory.")
                    # Update the database to mark the video as filtered
                    self.cursor.execute("UPDATE processed_videos SET is_filtered = 'yes' WHERE video_id = ?", (video_id,))
                else:
                    # Update the database to mark the video as not filtered
                    self.cursor.execute("UPDATE processed_videos SET is_filtered = 'no' WHERE video_id = ?", (video_id,))
                    self.conn.commit()
            
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

                # Select the next model and update the index
                selected_model_name = self.gemini_models[self.current_gemini_index]
                self.current_gemini_index = (self.current_gemini_index + 1) % len(self.gemini_models)
                print(f"Using Gemini model: {selected_model_name}")

                response = client.models.generate_content(
                    model=selected_model_name,
                    config={
                        'response_mime_type': 'application/json',
                    },
                    contents=[video_file, prompt]
                )
                
                # Extract the response from Gemini
                print("Response from Gemini (FileAPI):")
                json_str = response3.text
                data = json.loads(json_str)
                if isinstance(data, list): # if Gemini response is a list instead of a dict
                    data = data[0]
                print(data)
                
                # Save the Gemini response to a file
                time.sleep(1)
                analysis_file = os.path.join(video_dir, 'analysis.json')
                time.sleep(1)
                with open(analysis_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                
                # Check the response for specific keys
                if any(data.get(key) == "yes" for key in ["filter_passed"]):
                    # Move the video directory to the OUTPUT_FILTERED directory
                    filtered_video_dir = os.path.join(self.output_dir_filtered, f"video_{video_id}")
                    try:
                        time.sleep(1)
                        os.rename(video_dir, filtered_video_dir)
                    except PermissionError as e:
                        logging.error(f"PermissionError while moving directory {video_dir} to {filtered_video_dir}: {str(e)}")
                        time.sleep(1)  # Wait briefly and retry
                        os.rename(video_dir, filtered_video_dir)
                    print(f"Video {video_id} moved to filtered directory.")
                    # Update the database to mark the video as filtered
                    self.cursor.execute("UPDATE processed_videos SET is_filtered = 'yes' WHERE video_id = ?", (video_id,))
                else:
                    # Update the database to mark the video as not filtered
                    self.cursor.execute("UPDATE processed_videos SET is_filtered = 'no' WHERE video_id = ?", (video_id,))
                    self.conn.commit()

        except Exception as e:
                    logging.error(f"Error processing video {video_id}: {str(e)}")

    def fetch_fyp_posts(self):
        """
        Fetch posts from the FYP section
        Returns:
            dict: Response data containing FYP posts
        """
        try:
            
            response = self.api.public.explore(
                count=30,
                language='en',
                region='US',
            )

            # Process each video in the response
            if hasattr(response, 'json') and 'itemList' in response.json():
                for item in response.json()['itemList']:
                    video_id = item.get('id')
                    if not video_id:
                        logging.warning("No video ID found in item, most probably it's a livestream")
                        continue
                    print(f"video - {video_id}")
                    
                    # Check if the video_id exists in the database and its 'is_downloaded' status
                    self.cursor.execute("SELECT is_downloaded FROM processed_videos WHERE video_id = ?", (video_id,))
                    result = self.cursor.fetchone()
                    if result and (result[0] == 'yes' | result[0] == 'skipped'):
                        print(f"Video {video_id} is already downloaded. Skipping.")
                        continue
                    else:
                        # Insert the new video_id into the database to mark it as processed.
                        self.cursor.execute("SELECT 1 FROM processed_videos WHERE video_id = ?", (video_id,))
                        if not self.cursor.fetchone():
                            self.cursor.execute("INSERT INTO processed_videos (video_id) VALUES (?)", (video_id,))
                        self.conn.commit()
                        print(f"Processing video - {video_id}")
                    
                    video_duration = item.get('video', {}).get('duration', 0)
                    if video_duration <= 20: # Only process videos less than 20 seconds (Change if necessary)
                        self.process_video(item, response)
                    else:
                        print(f"{video_id} Video skipped because duration exceeded threshold")
                        self.cursor.execute("UPDATE processed_videos SET is_downloaded = 'skipped' WHERE video_id = ?", (video_id,))
                        self.cursor.execute("UPDATE processed_videos SET is_filtered = 'skipped' WHERE video_id = ?", (video_id,))
                        self.conn.commit()
                return response.json()
            else:
                logging.error("Invalid response format")
                return None

        except (ValidationException, ResponseException) as e:
            logging.error(f"API Error: {str(e)}")
            return None
        
        except Exception as e:
            logging.error(f"Unexpected error: {str(e)}")
            return None

    def scrape_and_save(self):
        """
        Fetch and save FYP posts
        """
        data = self.fetch_fyp_posts()
        if not data:
            logging.error("Failed to fetch posts")
            return None
        
        logging.info(f"Scraping complete.")
    

def main():
    try:
        while True:
            try:
                runs = int(input("Enter number of runs (1-5): "))
                if 1 <= runs <= 5:
                    break
                else:
                    print("Please enter a number between 1 and 5")
            except ValueError:
                print("Please enter a valid number")
        
        scraper = TikTokScraper()
        for i in range(runs):
            print(f"\nStarting run {i+1} of {runs}")
            scraper.scrape_and_save()
        
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()