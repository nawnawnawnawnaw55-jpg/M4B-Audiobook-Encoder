# M4B Audiobook Encoder

A desktop application built with Python and PyQt6 that merges multiple audio files into a single .m4b audiobook. 

Instead of processing files one by one, this tool acts as a multithreaded frontend for FFmpeg. It encodes your files into AAC format in parallel across all available CPU cores, then merges them together along with your cover art and metadata.

### Features

* Multithreaded encoding to significantly speed up processing times
* Drag and drop support for entire folders or individual files
* Live audio preview to test how different AAC bitrates will sound before you commit to a full encode
* Add custom cover art and metadata (author, narrator, year, genre)
* Chapter marker management. Auto generate them from file names or write your own
* Supports mp3, m4a, aac, flac, wav, wma, and ogg inputs

### Prerequisites

You need two main things installed on your computer for this to work: Python and FFmpeg.

1. **Python 3.8 or newer**
2. **FFmpeg**: This must be installed and accessible in your system's PATH, otherwise the script will fail to encode or preview audio.

### Installing FFmpeg

If you do not already have FFmpeg installed, here is how to get it:

* **Windows**: Download a release build from gyan.dev. Extract the zip file, and add the `bin` folder inside it to your Windows Environment Variables under Path.
* **macOS**: Use Homebrew and run `brew install ffmpeg` in your terminal.
* **Linux**: Use your package manager, for example `sudo apt install ffmpeg`.

To verify it is installed correctly, open a new terminal or command prompt and type `ffmpeg`. If you get a list of commands and version information, you are good to go.

### Setup and Installation

1. Clone this repository to your local machine.
2. Open a terminal in the project folder.
3. Install the required Python dependencies by running:
   `pip install PyQt6`

### Usage

Run the script from your terminal:
`python audiobook_merger.py`

Once the interface opens:
1. Drag and drop a folder containing your audio files into the app.
2. Rearrange the files in the list if they are out of order.
3. Type in the audiobook title and any additional metadata you want.
4. Select your preferred chapter settings and edit the names if needed.
5. Choose your encoding quality. You can click a track and use the Preview button to test the audio quality.
6. Add cover art.
7. Click the encode button and wait for the multithreaded process to finish. The final .m4b file will be saved in the same directory as your source files.
