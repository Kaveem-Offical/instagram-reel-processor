import os
import tempfile
import subprocess
import logging
import time
import uuid
from flask import Flask, request, jsonify
import yt_dlp
import cloudinary
import cloudinary.uploader
from pathlib import Path
import shutil
import json
from urllib.parse import urlparse
import threading
import queue

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configure Cloudinary
cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET')
)

# Global variables for processing queue
processing_queue = queue.Queue()
processing_results = {}

class VideoProcessor:
    def __init__(self):
        self.outro_image_path = "outro.png"
        self.cookies_file = "cookies.txt"  # Use the copied cookies file
        
    def validate_instagram_url(self, url):
        """Validate Instagram URL"""
        try:
            parsed = urlparse(url)
            return ('instagram.com' in parsed.netloc and 
                   ('/reel/' in parsed.path or '/p/' in parsed.path))
        except:
            return False
    
    def check_cookies_file(self):
        """Check if cookies file exists and is readable"""
        if not os.path.exists(self.cookies_file):
            logger.error(f"Cookies file not found at {self.cookies_file}")
            return False
        
        try:
            with open(self.cookies_file, 'r') as f:
                content = f.read().strip()
                if not content:
                    logger.error("Cookies file is empty")
                    return False
                logger.info(f"Cookies file found with {len(content.splitlines())} lines")
                return True
        except Exception as e:
            logger.error(f"Error reading cookies file: {str(e)}")
            return False
    
    def download_instagram_reel(self, url, output_path):
        """Download Instagram reel using yt-dlp with cookies and multiple fallback methods"""
        logger.info(f"Starting download for URL: {url}")
        
        # Method 1: Using cookies file
        if self.check_cookies_file():
            try:
                logger.info("Attempting download with cookies file...")
                ydl_opts = {
                    'outtmpl': output_path,
                    'format': 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
                    'cookiefile': self.cookies_file,
                    'quiet': False,
                    'no_warnings': False,
                    'extract_flat': False,
                    'writethumbnail': False,
                    'writeinfojson': False,
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                        'Sec-Fetch-Dest': 'document',
                        'Sec-Fetch-Mode': 'navigate',
                        'Sec-Fetch-Site': 'none',
                        'Cache-Control': 'max-age=0',
                    },
                    'sleep_interval': 1,
                    'max_sleep_interval': 3,
                    'retries': 3,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                
                if os.path.exists(output_path):
                    logger.info("Successfully downloaded using cookies method")
                    return True
                    
            except Exception as e:
                logger.error(f"Cookies method failed: {str(e)}")
        
        # Method 2: Using credentials if available
        username = os.environ.get('INSTAGRAM_USERNAME')
        password = os.environ.get('INSTAGRAM_PASSWORD')
        
        if username and password:
            try:
                logger.info("Attempting download with username/password...")
                ydl_opts = {
                    'outtmpl': output_path,
                    'format': 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
                    'username': username,
                    'password': password,
                    'quiet': False,
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    },
                    'sleep_interval': 2,
                    'max_sleep_interval': 5,
                    'retries': 2,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                
                if os.path.exists(output_path):
                    logger.info("Successfully downloaded using credentials method")
                    return True
                    
            except Exception as e:
                logger.error(f"Credentials method failed: {str(e)}")
        
        # Method 3: Basic attempt without authentication (last resort)
        try:
            logger.info("Attempting basic download without authentication...")
            ydl_opts = {
                'outtmpl': output_path,
                'format': 'best[height<=1080][ext=mp4]/best[ext=mp4]/best',
                'quiet': False,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                },
                'sleep_interval': 3,
                'retries': 1,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            if os.path.exists(output_path):
                logger.info("Successfully downloaded using basic method")
                return True
                
        except Exception as e:
            logger.error(f"Basic method failed: {str(e)}")
        
        logger.error("All download methods failed")
        return False

    def get_video_info(self, video_path):
        """Get video dimensions and duration using ffprobe"""
        try:
            cmd = [
                'ffprobe', '-v', 'quiet', '-print_format', 'json',
                '-show_streams', '-show_format', video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"ffprobe failed: {result.stderr}")
                return None, None, None
                
            data = json.loads(result.stdout)
            
            width = height = duration = None
            
            for stream in data['streams']:
                if stream['codec_type'] == 'video':
                    width = int(stream.get('width', 0))
                    height = int(stream.get('height', 0))
                    break
            
            if 'format' in data and 'duration' in data['format']:
                duration = float(data['format']['duration'])
            
            return width, height, duration
        except Exception as e:
            logger.error(f"Error getting video info: {str(e)}")
            return None, None, None

    def resize_video_to_portrait(self, input_path, output_path):
        """Resize video to 1080x1920 portrait format with optimization"""
        try:
            cmd = [
                'ffmpeg', '-i', input_path,
                '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                '-y', output_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                logger.error(f"ffmpeg resize failed: {result.stderr}")
                return False
                
            return os.path.exists(output_path)
        except Exception as e:
            logger.error(f"Error resizing video: {str(e)}")
            return False

    def create_outro_video(self, outro_video_path):
        """Create 5-second outro video from image"""
        try:
            if not os.path.exists(self.outro_image_path):
                logger.error(f"Outro image not found at {self.outro_image_path}")
                return False
            
            cmd = [
                'ffmpeg', '-loop', '1', '-t', '5', '-i', self.outro_image_path,
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-vf', 'scale=1080:1920,setsar=1',
                '-r', '30', '-movflags', '+faststart',
                '-y', outro_video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                logger.error(f"ffmpeg outro creation failed: {result.stderr}")
                return False
                
            return os.path.exists(outro_video_path)
        except Exception as e:
            logger.error(f"Error creating outro video: {str(e)}")
            return False

    def concatenate_videos(self, main_video_path, outro_video_path, output_path):
        """Concatenate main video with outro using filter_complex"""
        try:
            cmd = [
                'ffmpeg',
                '-i', main_video_path,
                '-i', outro_video_path,
                '-filter_complex', '[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]',
                '-map', '[v]', '-map', '[a]',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                '-y', output_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                logger.error(f"ffmpeg concatenation failed: {result.stderr}")
                return False
                
            return os.path.exists(output_path)
        except Exception as e:
            logger.error(f"Error concatenating videos: {str(e)}")
            return False

    def upload_to_cloudinary(self, file_path, public_id=None):
        """Upload video to Cloudinary with optimization"""
        try:
            upload_options = {
                'resource_type': 'video',
                'folder': 'instagram_reels',
                'use_filename': True,
                'unique_filename': True,
                'overwrite': True,
                'quality': 'auto',
                'format': 'mp4',
                'timeout': 300
            }
            
            if public_id:
                upload_options['public_id'] = public_id
            
            logger.info(f"Uploading video to Cloudinary: {file_path}")
            result = cloudinary.uploader.upload(file_path, **upload_options)
            logger.info(f"Successfully uploaded to Cloudinary: {result['secure_url']}")
            return result['secure_url']
        except Exception as e:
            logger.error(f"Error uploading to Cloudinary: {str(e)}")
            return None

    def process_video(self, instagram_url, job_id):
        """Main video processing function"""
        try:
            processing_results[job_id] = {"status": "processing", "progress": 0, "message": "Starting processing..."}
            
            # Create temporary directory
            with tempfile.TemporaryDirectory(dir="/app/temp") as temp_dir:
                temp_path = Path(temp_dir)
                
                # Define file paths
                downloaded_video = temp_path / f"downloaded_{job_id}.mp4"
                resized_video = temp_path / f"resized_{job_id}.mp4"
                outro_video = temp_path / f"outro_{job_id}.mp4"
                final_video = temp_path / f"final_{job_id}.mp4"
                
                # Step 1: Download Instagram reel
                logger.info(f"Job {job_id}: Downloading reel from {instagram_url}")
                processing_results[job_id].update({"progress": 20, "message": "Downloading Instagram reel..."})
                
                if not self.download_instagram_reel(instagram_url, str(downloaded_video)):
                    processing_results[job_id] = {"status": "error", "message": "Failed to download reel. Please check if the URL is valid and accessible."}
                    return
                
                # Step 2: Get video info
                processing_results[job_id].update({"progress": 30, "message": "Analyzing video..."})
                width, height, duration = self.get_video_info(str(downloaded_video))
                if width is None or height is None:
                    processing_results[job_id] = {"status": "error", "message": "Failed to analyze video properties"}
                    return
                
                logger.info(f"Job {job_id}: Video info - {width}x{height}, {duration}s")
                
                # Step 3: Resize if needed
                video_to_process = downloaded_video
                if width != 1080 or height != 1920:
                    logger.info(f"Job {job_id}: Resizing video from {width}x{height} to 1080x1920")
                    processing_results[job_id].update({"progress": 40, "message": "Resizing video to portrait format..."})
                    
                    if not self.resize_video_to_portrait(str(downloaded_video), str(resized_video)):
                        processing_results[job_id] = {"status": "error", "message": "Failed to resize video"}
                        return
                    video_to_process = resized_video
                
                # Step 4: Create outro video
                logger.info(f"Job {job_id}: Creating outro video")
                processing_results[job_id].update({"progress": 60, "message": "Creating outro..."})
                
                if not self.create_outro_video(str(outro_video)):
                    processing_results[job_id] = {"status": "error", "message": "Failed to create outro video"}
                    return
                
                # Step 5: Concatenate videos
                logger.info(f"Job {job_id}: Concatenating videos")
                processing_results[job_id].update({"progress": 80, "message": "Combining videos..."})
                
                if not self.concatenate_videos(str(video_to_process), str(outro_video), str(final_video)):
                    processing_results[job_id] = {"status": "error", "message": "Failed to combine videos"}
                    return
                
                # Step 6: Upload to Cloudinary
                logger.info(f"Job {job_id}: Uploading to Cloudinary")
                processing_results[job_id].update({"progress": 90, "message": "Uploading to cloud..."})
                
                cloudinary_url = self.upload_to_cloudinary(str(final_video), f"processed_{job_id}")
                if not cloudinary_url:
                    processing_results[job_id] = {"status": "error", "message": "Failed to upload to cloud storage"}
                    return
                
                # Success
                processing_results[job_id] = {
                    "status": "completed",
                    "progress": 100,
                    "message": "Processing completed successfully!",
                    "cloudinary_url": cloudinary_url,
                    "original_dimensions": f"{width}x{height}",
                    "duration": duration
                }
                
                logger.info(f"Job {job_id}: Successfully processed. URL: {cloudinary_url}")
                
        except Exception as e:
            logger.error(f"Job {job_id}: Unexpected error: {str(e)}")
            processing_results[job_id] = {"status": "error", "message": f"Unexpected error: {str(e)}"}

# Initialize processor
processor = VideoProcessor()

def worker():
    """Background worker for processing videos"""
    while True:
        try:
            job = processing_queue.get()
            if job is None:
                break
            
            job_id, instagram_url = job
            processor.process_video(instagram_url, job_id)
            processing_queue.task_done()
        except Exception as e:
            logger.error(f"Worker error: {str(e)}")

# Start background worker
worker_thread = threading.Thread(target=worker, daemon=True)
worker_thread.start()

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    cookies_status = processor.check_cookies_file()
    return jsonify({
        "status": "healthy",
        "message": "Server is running",
        "cookies_available": cookies_status,
        "queue_size": processing_queue.qsize(),
        "active_jobs": len(processing_results)
    })

@app.route('/process-reel', methods=['POST'])
def process_reel():
    """Main endpoint to process Instagram reel"""
    try:
        data = request.get_json()
        if not data or 'url' not in data:
            return jsonify({"error": "Instagram reel URL is required"}), 400
        
        instagram_url = data['url'].strip()
        
        # Validate URL
        if not processor.validate_instagram_url(instagram_url):
            return jsonify({"error": "Invalid Instagram URL. Please provide a valid Instagram reel or post URL."}), 400
        
        # Check if cookies are available
        if not processor.check_cookies_file():
            logger.warning("No cookies available - this may cause download failures")
        
        # Generate job ID
        job_id = str(uuid.uuid4())
        
        # Add to processing queue
        processing_queue.put((job_id, instagram_url))
        
        return jsonify({
            "job_id": job_id,
            "status": "queued",
            "message": "Video processing started",
            "check_status_url": f"/status/{job_id}"
        }), 202
        
    except Exception as e:
        logger.error(f"Error in process_reel: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/status/<job_id>', methods=['GET'])
def check_status(job_id):
    """Check processing status"""
    if job_id not in processing_results:
        return jsonify({"error": "Job not found"}), 404
    
    result = processing_results[job_id]
    
    # Clean up completed/failed jobs after 1 hour
    if result["status"] in ["completed", "error"]:
        if "timestamp" not in result:
            result["timestamp"] = time.time()
        elif time.time() - result["timestamp"] > 3600:  # 1 hour
            del processing_results[job_id]
    
    return jsonify(result)

@app.route('/queue-status', methods=['GET'])
def queue_status():
    """Get queue status"""
    return jsonify({
        "queue_size": processing_queue.qsize(),
        "active_jobs": len(processing_results),
        "jobs": {k: v.get("status", "unknown") for k, v in processing_results.items()}
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    # Check setup on startup
    logger.info("Starting Instagram Reel Processor...")
    logger.info(f"Cookies available: {processor.check_cookies_file()}")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)