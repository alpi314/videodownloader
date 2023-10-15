from flask import Flask, render_template, request, session, redirect, jsonify, send_file
import psycopg2
import json
import subprocess
import time
from random import choices
import os
from dotenv import load_dotenv
import multiprocessing
from functools import wraps
import threading
import re
import shutil
import importlib  

# Downloader
downloader = importlib.import_module('youtube-dl.youtube_dl')

# Load ENV variables
load_dotenv()
DB_USERNAME = os.getenv('DB_USERNAME')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')
DB_HOST = os.getenv('DB_HOST')

TEMP_FOLDER = os.getenv('TEMP_FOLDER')
UPLOADS_FOLDER = os.path.join(TEMP_FOLDER, 'uploads')
DOWNLOADS_FOLDER = os.path.join(TEMP_FOLDER, 'downloads')
OUTPUT_FOLDER = os.path.join(TEMP_FOLDER, 'output')
MODULE_NAME = os.getenv('MODULE_NAME')
MODULE_PATH = os.getenv('MODULE_PATH')
DEFAULT_COOKIES_FILE = os.getenv('DEFAULT_COOKIES_FILE')

SESSION_SECRET_KEY = os.getenv('SESSION_SECRET_KEY')

HELP_FILE = os.getenv('HELP_FILE')

CHECKBOX_PREFIX = os.getenv('CHECKBOX_PREFIX')
CHECKBOX_PREFIX_LEN = len(CHECKBOX_PREFIX)
INPUT_PREFIX = os.getenv('INPUT_PREFIX')
INPUT_PREFIX_LEN = len(INPUT_PREFIX)

DEBUG = os.getenv('DEBUG') == 'True'

# Create app
app = Flask(__name__)
app.secret_key = SESSION_SECRET_KEY

# Connect to database
conn = psycopg2.connect(
    host=DB_HOST,
    database=DB_NAME,
    user=DB_USERNAME,
    password=DB_PASSWORD
)

# Create temporary folder
os.makedirs(TEMP_FOLDER, exist_ok=True)
os.makedirs(UPLOADS_FOLDER, exist_ok=True)
os.makedirs(DOWNLOADS_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Create shared variable for download progress
manager = multiprocessing.Manager()
download_progress = manager.dict()

# Login required decorator
def logged_in(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'logged_in' in session and session['logged_in']:
            return f(*args, **kwargs)

        return redirect('/login')

    return decorated

# Unique key generator
def generate_key(k=10):
    key = ''.join(choices('abcdefghijklmnopqrstuvwxyz', k=k))
    seconds = int(time.time())

    return f'{key}_{seconds}'

def sanitize_key(key):
    return ''.join([c for c in key if c.isalnum() or c == '_'])

def debug_file_path(key):
    key = sanitize_key(key)
    return os.path.join(OUTPUT_FOLDER, f'{key}_debug.txt')

def download_file_path(key):
    key = sanitize_key(key)
    return os.path.join(OUTPUT_FOLDER, f'{key}_download.txt')

def downloads_for_key(key):
    key = sanitize_key(key)
    return os.path.join(DOWNLOADS_FOLDER, key)

# Progress regexes
video_title_regex = r'.*Destination:(.*)'
is_playlist_regex = r'.*Downloading playlist:.*'
playlist_progress_regex = r'.*Downloading video (\d+) of (\d+).*'
download_progress_regex = r'^[a-z\[\]\s]*(\d+)(?:\.(\d+))?%.*ETA (\d+):(\d+).*'

# Update download progress by parsing the output line
def update_download_progress(download_progress, key, line):
    if key not in download_progress:
        download_progress[key] = {
            'time_started': time.time(),
            'time_finished': None,
            'is_playlist': False,
            'total_videos': 1,
            'downloaded_videos': 0,
            'total_eta': 0,
            'current_video_progress': 0,
            'current_video_eta': 0,
            'current_video_title': '',
            'finished': False,
        }
    
    current_progress = download_progress[key]
    if not current_progress['is_playlist'] and re.match(is_playlist_regex, line):
        current_progress['is_playlist'] = True

    if current_progress['is_playlist']:
        match = re.match(playlist_progress_regex, line)
        if match:
            current_progress['total_videos'] = int(match.group(2))
            current_progress['downloaded_videos'] = int(match.group(1))
    
    if current_progress['current_video_progress'] == 0:
        match = re.match(video_title_regex, line)
        if match:
            current_progress['current_video_title'] = match.group(1).split('/')[-1].strip()
    
    if current_progress['current_video_title'] != '':
        match = re.match(download_progress_regex, line)
        if match:
            current_progress['current_video_progress'] = int(match.group(1)) / 100
            current_progress['current_video_eta'] = int(match.group(3)) * 60 + int(match.group(4))
        
    if current_progress['current_video_progress'] == 1:
        time_taken = time.time() - current_progress['time_started']
        remaining_videos = max(current_progress['total_videos'] - current_progress['downloaded_videos'], 0)
        per_video_time = 0 if current_progress['downloaded_videos'] == 0 else time_taken / current_progress['downloaded_videos']
        current_progress['total_eta'] = per_video_time * remaining_videos

        current_progress['current_video_title'] = ''
        current_progress['current_video_progress'] = 0
        current_progress['full_video_eta'] = 0
        current_progress['current_video_eta'] = 0
        current_progress['downloaded_videos'] += 1
        if current_progress['downloaded_videos'] >= current_progress['total_videos']:
            current_progress['downloaded_videos'] = current_progress['total_videos']
            current_progress['finished'] = True
            current_progress['time_finished'] = time.time()

    download_progress[key] = current_progress


# Download process
def download_process(url, flags, key, download_progress):
    # Create temporary folder
    os.makedirs(downloads_for_key(key), exist_ok=True)

    # Generate output files
    debug_output_file_path = debug_file_path(key)
    download_output_file_path = download_file_path(key)

    debug_output_file = open(debug_output_file_path, 'w')
    download_output_file = open(download_output_file_path, 'w')

    # Debug regex
    has_source_regex = r'^\[.*\] .*$'
    debug_regex = r'^\[debug\]'

    # Start download
    cwd = os.path.join(os.getcwd(), MODULE_PATH)
    process = subprocess.Popen(['python', '-m', MODULE_NAME, *flags, url], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd)

    # Function to read output from the subprocess and write it to the file
    def write_to_file():
        is_previous_debug = False
        while True:
            output = process.stdout.readline()
            if output == '':
                break
            
            is_debug = True
            # We know the source of the log
            if re.match(has_source_regex, output):
                is_debug = re.match(debug_regex, output) is not None
            else:
                # Write to the same file as before
                is_debug = is_previous_debug

            if is_debug:
                debug_output_file.write(output)
                debug_output_file.flush()
            else:
                download_output_file.write(output)
                download_output_file.flush()
            
            is_previous_debug = is_debug
            update_download_progress(download_progress, key, output)


    # Start a thread to write output to the file
    thread = threading.Thread(target=write_to_file)
    thread.start()

    # Wait for the subprocess to complete
    process.wait()
    thread.join()

    # Close output files
    debug_output_file.close()
    download_output_file.close()

    # Create a zip file
    shutil.make_archive(downloads_for_key(key), 'zip', DOWNLOADS_FOLDER, sanitize_key(key))

# Download File
@app.route('/download/<key>', methods = ['GET'])
@logged_in
def download_file(key):
    # Get directory for key
    directory = downloads_for_key(key)

    # Check if key is valid
    if not os.path.isdir(directory):
        return "Invalid key"

    # Check if download is finished
    progress = download_progress.get(key, {})
    if not progress.get('finished', False):
        return "Download not finished"

    # Check if zip file exists
    zip_file_path = f'{directory}.zip'
    if not os.path.isfile(zip_file_path):
        return "Zip file not found"

    # Send file
    return send_file(zip_file_path, as_attachment=True)

# Output page
@app.route('/output/<key>', methods = ['GET'])
@logged_in
def output(key):
    return render_template('output.html', key=key)

# Output file logs
@app.route('/output/logs', methods = ['POST'])
@logged_in
def output_logs():
    # JSON response
    data = request.get_json()

    # Get key
    key = data['key']

    # Get file paths
    debug_file = debug_file_path(key)
    download_progress_file = download_file_path(key)

    # Set default text
    debug = "Invalid key"
    download_progress = "Invalid key"

    # Check if files exist
    if os.path.isfile(debug_file):
        # Read debug file
        with open(debug_file, 'r') as file:
            debug = file.read()
    if os.path.isfile(download_progress_file):
        # Read progress file
        with open(download_progress_file, 'r') as file:
            download_progress = file.read()

    return jsonify(debug=debug, download_progress=download_progress)

# Output download progress
@app.route('/output/progress', methods = ['POST'])
@logged_in
def output_progress():
    # JSON response
    data = request.get_json()

    # Get key
    key = data['key']

    # Get download progress
    progress = download_progress.get(key, {})

    return jsonify(progress=progress)


# Index page
@app.route('/', methods = ['GET'])
@logged_in
def index():
    return render_template('index.html', flags=FLAGS)

# Download post
@app.route('/download', methods = ['POST'])
@logged_in
def download():
    # Download url
    url = request.form['url']

    # Parse form flags
    flags = []
    output_name_template = "%(title)s_%(id)s.%(ext)s"
    for key in request.form:
        if key.startswith(CHECKBOX_PREFIX):
            # Boolean flags
            flag_name = key[len(CHECKBOX_PREFIX):]
            flag_value = None
        elif key.startswith(INPUT_PREFIX):
            # Input flags
            flag_name = key[len(INPUT_PREFIX):]
            flag_value = request.form[key]
            if flag_value == '':
                continue
        else:
            continue

        # Skip if flag is not in help file
        if flag_name not in FLAGS_BY_NAME:
            print(f"Unknown flag: {flag_name}", flush=True)
            continue
        
        # Output only allows filename template
        if flag_name == "--output":
            output_name_template = flags[-1]
            continue

        # Add flag
        flags.append(flag_name)
        if flag_value is not None:
            flags.append(f'"flag_value"')
    
    # Parse form files
    for key in request.files:
        flag_name = key[len(INPUT_PREFIX):]

        if key.startswith(CHECKBOX_PREFIX):
            continue

        if request.files[key].filename == '':
            # Default cookies file
            if flag_name == '--cookies':
                flags.append(flag_name)
                flags.append(DEFAULT_COOKIES_FILE)
            continue
        
        
        if FLAGS_BY_NAME[flag_name]['argument'] != 'FILE':
            continue

        # Set file name
        file = request.files[key]
        file_ending = file.filename.split('.')[-1]
        file_name = f'{generate_key()}.{file_ending}'
        file_path = os.path.join(UPLOADS_FOLDER, file_name)
        
        # Save file
        file.save(file_path)
        file.close()

        # Add flag
        flags.append(flag_name)
        flags.append(file_path)

    # Generate key
    key = generate_key()

    # Add output flag
    flags.append("--output")
    output_name_template = output_name_template.split('\\')[-1].split('/')[-1].strip() # sanitize
    flags.append(os.path.join(downloads_for_key(key), output_name_template))

    # Add --verbose flag
    if DEBUG:
        flags.append("--verbose")

    # Start download process
    process = multiprocessing.Process(target=download_process, args=(url, flags, key, download_progress))
    process.start()
    
    return redirect('/output/' + key)

# Login page
@app.route('/login', methods = ['GET'])
def login():
    return render_template('login.html')

# Login post
@app.route('/login', methods = ['POST'])
def login_post():
    username = request.form['username']
    password = request.form['password']

    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT login(%s, %s)", (username, password))

            result = cur.fetchone()

    if result[0]:
        session['logged_in'] = True
        return redirect('/')
    else:
        return redirect('/login')

# Logout post 
@app.route('/logout', methods = ['POST'])
def logout():
    session['logged_in'] = False
    return redirect('/login')


if __name__ == '__main__':
    FLAGS = json.load(open(HELP_FILE, 'r'))
    FLAGS_BY_NAME = {flag['flag']: flag for flag in FLAGS}

    app.run(debug=True, host="0.0.0.0", port=5000)