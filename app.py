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
        
    def validate_instagram_url(self, url):
        """Validate Instagram URL"""
        try:
            parsed = urlparse(url)
            return ('instagram.com' in parsed.netloc and 
                   ('/reel/' in parsed.path or '/p/' in parsed.path))
        except:
            return False
    
    def download_instagram_reel(self, url, output_path):
        """Download Instagram reel using yt-dlp with cookies"""
        try:
            ydl_opts = {
                'outtmpl': output_path,
                'format': 'best[height<=1080][ext=mp4]/best[ext=mp4]',
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'writethumbnail': False,
                'writeinfojson': False,
                'ignoreerrors': True,
                'cookies': 'cookies.txt',  # 🔥 path to your cookies.txt
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            return os.path.exists(output_path)
        except Exception as e:
            logger.error(f"Error downloading reel: {str(e)}")
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
                '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                '-y', output_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            return result.returncode == 0 and os.path.exists(output_path)
        except Exception as e:
            logger.error(f"Error resizing video: {str(e)}")
            return False

    def create_outro_video(self, outro_video_path):
        """Create 5-second outro video from image"""
        try:
            if not os.path.exists(self.outro_image_path):
                logger.error("Outro image not found")
                return False
            
            cmd = [
                'ffmpeg', '-loop', '1', '-t', '5', '-i', self.outro_image_path,
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-vf', 'scale=1080:1920,setsar=1',
                '-r', '30', '-movflags', '+faststart',
                '-y', outro_video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return result.returncode == 0 and os.path.exists(outro_video_path)
        except Exception as e:
            logger.error(f"Error creating outro video: {str(e)}")
            return False

    def concatenate_videos(self, main_video_path, outro_video_path, output_path):
        """Concatenate main video with outro using filter_complex for better compatibility"""
        try:
            cmd = [
                'ffmpeg',
                '-i', main_video_path,
                '-i', outro_video_path,
                '-filter_complex', '[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]',
                '-map', '[v]', '-map', '[a]',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                '-y', output_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            return result.returncode == 0 and os.path.exists(output_path)
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
                'format': 'mp4'
            }
            
            if public_id:
                upload_options['public_id'] = public_id
            
            result = cloudinary.uploader.upload(file_path, **upload_options)
            return result['secure_url']
        except Exception as e:
            logger.error(f"Error uploading to Cloudinary: {str(e)}")
            return None

    def process_video(self, instagram_url, job_id):
        """Main video processing function"""
        try:
            processing_results[job_id] = {"status": "processing", "progress": 0}
            
            # Create temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Define file paths
                downloaded_video = temp_path / f"downloaded_{job_id}.mp4"
                resized_video = temp_path / f"resized_{job_id}.mp4"
                outro_video = temp_path / f"outro_{job_id}.mp4"
                final_video = temp_path / f"final_{job_id}.mp4"
                
                # Step 1: Download Instagram reel
                logger.info(f"Job {job_id}: Downloading reel from {instagram_url}")
                processing_results[job_id]["progress"] = 20
                
                if not self.download_instagram_reel(instagram_url, str(downloaded_video)):
                    processing_results[job_id] = {"status": "error", "message": "Failed to download reel"}
                    return
                
                # Step 2: Get video info
                width, height, duration = self.get_video_info(str(downloaded_video))
                if width is None or height is None:
                    processing_results[job_id] = {"status": "error", "message": "Failed to get video info"}
                    return
                
                logger.info(f"Job {job_id}: Video info - {width}x{height}, {duration}s")
                processing_results[job_id]["progress"] = 30
                
                # Step 3: Resize if needed
                video_to_process = downloaded_video
                if width != 1080 or height != 1920:
                    logger.info(f"Job {job_id}: Resizing video from {width}x{height} to 1080x1920")
                    processing_results[job_id]["progress"] = 40
                    
                    if not self.resize_video_to_portrait(str(downloaded_video), str(resized_video)):
                        processing_results[job_id] = {"status": "error", "message": "Failed to resize video"}
                        return
                    video_to_process = resized_video
                
                # Step 4: Create outro video
                logger.info(f"Job {job_id}: Creating outro video")
                processing_results[job_id]["progress"] = 60
                
                if not self.create_outro_video(str(outro_video)):
                    processing_results[job_id] = {"status": "error", "message": "Failed to create outro video"}
                    return
                
                # Step 5: Concatenate videos
                logger.info(f"Job {job_id}: Concatenating videos")
                processing_results[job_id]["progress"] = 80
                
                if not self.concatenate_videos(str(video_to_process), str(outro_video), str(final_video)):
                    processing_results[job_id] = {"status": "error", "message": "Failed to concatenate videos"}
                    return
                
                # Step 6: Upload to Cloudinary
                logger.info(f"Job {job_id}: Uploading to Cloudinary")
                processing_results[job_id]["progress"] = 90
                
                cloudinary_url = self.upload_to_cloudinary(str(final_video), f"processed_{job_id}")
                if not cloudinary_url:
                    processing_results[job_id] = {"status": "error", "message": "Failed to upload to Cloudinary"}
                    return
                
                # Success
                processing_results[job_id] = {
                    "status": "completed",
                    "progress": 100,
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
    return jsonify({
        "status": "healthy",
        "message": "Server is running",
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
            return jsonify({"error": "Invalid Instagram URL"}), 400
        
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
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)