import sys
import os
import glob
import subprocess
import ctypes
import re
import tempfile
import uuid
import shutil
import concurrent.futures
import time
import threading

if os.name == 'nt':
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)
    myappid = 'naw.audiobookmerger.1.0'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QFileDialog, QFrame, QMessageBox, QListWidget,
                             QComboBox, QListWidgetItem, QDialog, QProgressBar,
                             QStyledItemDelegate, QStyle, QScrollArea)
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont, QIcon, QPolygonF, QPen, QCursor
from PyQt6.QtCore import Qt, QPointF, QThread, pyqtSignal, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput


class HoverDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen_color = QColor("#F97300") if option.state & QStyle.StateFlag.State_Selected else QColor("#888888")
            pen = QPen(pen_color, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            rect = option.rect
            x = rect.right() - 25
            y = rect.center().y()
            painter.drawLine(x - 5, y - 2, x, y - 7)
            painter.drawLine(x, y - 7, x + 5, y - 2)
            painter.drawLine(x - 5, y + 2, x, y + 7)
            painter.drawLine(x, y + 7, x + 5, y + 2)
            painter.restore()


class ChapterEditorDialog(QDialog):
    def __init__(self, parent, file_items, custom_names_dict):
        super().__init__(parent)
        self.setWindowTitle("Edit Custom Chapter Names")
        self.resize(600, 450)
        self.setModal(True)
        self.setStyleSheet(parent.styleSheet())

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Type the exact names you want for your audiobook chapters.\nEmpty boxes will default back to the file name."))
        layout.addSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #3E3E3E; border-radius: 4px; background-color: #121212; }")

        content = QWidget()
        self.form_layout = QVBoxLayout(content)
        self.form_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.inputs = {}
        for item in file_items:
            filepath = item.data(Qt.ItemDataRole.UserRole)
            default_name = os.path.splitext(os.path.basename(filepath))[0]
            current_name = custom_names_dict.get(filepath, default_name)

            row = QHBoxLayout()
            lbl = QLabel(default_name[:25] + "..." if len(default_name) > 25 else default_name)
            lbl.setFixedWidth(200)
            lbl.setStyleSheet("color: #888888; font-size: 12px;")

            edit = QLineEdit(current_name)
            row.addWidget(lbl)
            row.addWidget(edit)
            self.form_layout.addLayout(row)
            self.inputs[filepath] = edit

        scroll.setWidget(content)
        layout.addWidget(scroll)

        btn_layout = QHBoxLayout()
        btn_apply = QPushButton("Save Chapter Names")
        btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_apply.clicked.connect(self.accept)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("secondaryBtn")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_apply)
        layout.addLayout(btn_layout)

    def get_names(self):
        return {filepath: edit.text().strip() for filepath, edit in self.inputs.items()}


class ExpandedListDialog(QDialog):
    def __init__(self, parent, main_list_widget):
        super().__init__(parent)
        self.setWindowTitle("Manage Audiobook Files")
        self.resize(600, 450)
        self.setModal(True)
        self.setStyleSheet(parent.styleSheet())

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Check the files you want to include in the final audiobook.\nUnchecked files will be ignored."))

        self.overlay_list = QListWidget()
        for i in range(main_list_widget.count()):
            orig_item = main_list_widget.item(i)
            new_item = QListWidgetItem(orig_item.text())
            new_item.setFlags(new_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            new_item.setCheckState(orig_item.checkState())
            new_item.setData(Qt.ItemDataRole.UserRole, orig_item.data(Qt.ItemDataRole.UserRole))
            self.overlay_list.addItem(new_item)

        layout.addWidget(self.overlay_list)

        btn_layout = QHBoxLayout()
        btn_apply = QPushButton("Apply Changes")
        btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_apply.clicked.connect(self.accept)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("secondaryBtn")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_apply)
        layout.addLayout(btn_layout)

    def get_updated_states(self):
        return [self.overlay_list.item(i).checkState() for i in range(self.overlay_list.count())]


class EncoderWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, target_folder, files_to_merge, output_file, cover_path, quality, meta_data, chapter_mode, custom_chapters):
        super().__init__()
        self.target_folder = target_folder
        self.files_to_merge = files_to_merge
        self.output_file = output_file
        self.cover_path = cover_path
        self.quality = quality
        self.meta_data = meta_data
        self.chapter_mode = chapter_mode
        self.custom_chapters = custom_chapters

    def run(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        dur_pattern = re.compile(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}\.\d+)")

        total_files = len(self.files_to_merge)
        meta_text = ";FFMETADATA1\n"
        if self.meta_data.get('title'):    meta_text += f"title={self.meta_data['title']}\n"
        if self.meta_data.get('author'):   meta_text += f"artist={self.meta_data['author']}\n"
        if self.meta_data.get('narrator'): meta_text += f"composer={self.meta_data['narrator']}\n"
        if self.meta_data.get('genre'):    meta_text += f"genre={self.meta_data['genre']}\n"
        if self.meta_data.get('year'):     meta_text += f"date={self.meta_data['year']}\n"

        current_time_ms = 0
        file_durations = []
        
        # 1. Scan Durations (Sequential so times line up perfectly)
        for i, f in enumerate(self.files_to_merge):
            self.progress.emit(int((i / total_files) * 10), f"Scanning metadata ({i+1}/{total_files})...")
            
            info_cmd = ["ffmpeg", "-i", f]
            duration_ms = 0
            duration_sec = 0.0
            try:
                res = subprocess.run(info_cmd, stderr=subprocess.PIPE, text=True, creationflags=flags)
                match = dur_pattern.search(res.stderr)
                if match:
                    h, m, s = float(match.group(1)), float(match.group(2)), float(match.group(3))
                    duration_sec = h * 3600 + m * 60 + s
                    duration_ms = int(duration_sec * 1000)
            except Exception:
                pass

            file_durations.append(duration_sec)

            if self.chapter_mode != "none" and duration_ms > 0:
                if self.chapter_mode == "custom":
                    track_title = self.custom_chapters.get(f, "")
                    if not track_title:
                        track_title = os.path.splitext(os.path.basename(f))[0]
                else:
                    track_title = os.path.splitext(os.path.basename(f))[0]

                meta_text += f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={current_time_ms}\nEND={current_time_ms + duration_ms}\ntitle={track_title}\n"
            current_time_ms += duration_ms

        # 2. Setup Temp Directory
        temp_dir = tempfile.mkdtemp(prefix="m4b_encode_")
        
        bitrate = "320k"
        if "256k" in self.quality: bitrate = "256k"
        if "192k" in self.quality: bitrate = "192k"
        if "128k" in self.quality: bitrate = "128k"

        # Pre-calculate audio volume limits
        num_threads = os.cpu_count() or 4
        total_audio_sec = sum(file_durations)

        # 3. Parallel Encoding Phase
        encoded_files = [None] * total_files
        completed_files = 0
        
        total_completed_audio_sec = 0.0
        progress_lock = threading.Lock()

        def encode_file(idx, f_path):
            out_file = os.path.join(temp_dir, f"part_{idx:04d}.m4a")
            # Strip video/art tracks from sub-chunks to prevent concat corruption
            cmd = [
                "ffmpeg", "-y", "-i", f_path,
                "-vn", "-c:a", "aac", "-b:a", bitrate,
                out_file
            ]
            
            # Use Popen to read progress live from the thread
            process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, creationflags=flags)
            time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")
            
            error_log = []
            last_sec = 0.0
            local_accumulated = 0.0
            last_update_time = time.time()
            
            for line in process.stderr:
                error_log.append(line)
                if len(error_log) > 20:
                    error_log.pop(0)
                
                match = time_pattern.search(line)
                if match:
                    h, m, s = float(match.group(1)), float(match.group(2)), float(match.group(3))
                    current_sec = h * 3600 + m * 60 + s
                    diff = current_sec - last_sec
                    if diff > 0:
                        local_accumulated += diff
                        last_sec = current_sec
                        
                    # Throttle lock acquisition to once per second per thread (Eliminates the CPU bottleneck)
                    if time.time() - last_update_time > 1.0:
                        if local_accumulated > 0:
                            with progress_lock:
                                nonlocal total_completed_audio_sec
                                total_completed_audio_sec += local_accumulated
                            local_accumulated = 0.0
                        last_update_time = time.time()
            
            # Flush any remaining accumulated time when the file finishes
            if local_accumulated > 0:
                with progress_lock:
                    total_completed_audio_sec += local_accumulated
            
            process.wait()
            if process.returncode != 0:
                err_msg = "".join(error_log)
                raise Exception(f"Failed to encode {os.path.basename(f_path)}:\n{err_msg}")
            return idx, out_file

        try:
            start_time = time.time()
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                future_to_idx = {executor.submit(encode_file, i, f): i for i, f in enumerate(self.files_to_merge)}
                pending = set(future_to_idx.keys())
                
                while pending:
                    # Wait up to 1 second so the UI updates constantly
                    done, pending = concurrent.futures.wait(pending, timeout=1.0)
                    
                    for future in done:
                        idx, out_file = future.result()
                        encoded_files[idx] = out_file
                        completed_files += 1
                        
                    elapsed = time.time() - start_time
                    
                    with progress_lock:
                        current_audio_done = total_completed_audio_sec
                        
                    # Smooth progress bar from 10% to 90% based on exact live audio processed
                    if total_audio_sec > 0:
                        percent_audio = min(current_audio_done / total_audio_sec, 1.0)
                        percent = 10 + int(percent_audio * 80)
                    else:
                        percent = 10 + int((completed_files / total_files) * 80)
                    
                    # Wait exactly 10 seconds to sample the speed, preventing wild ETA swings
                    if elapsed >= 10.0 and current_audio_done > 0:
                        actual_speed = current_audio_done / elapsed
                        remaining_audio = total_audio_sec - current_audio_done
                        eta_seconds = remaining_audio / actual_speed if actual_speed > 0 else 0
                        speed_str = f"{actual_speed:.1f}x"
                        
                        rm, rs = divmod(int(eta_seconds), 60)
                        rh, rm = divmod(rm, 60)
                        time_str = f"Speed: {speed_str} | ETA: {rh:02d}:{rm:02d}:{rs:02d}"
                    else:
                        rm, rs = divmod(int(elapsed), 60)
                        rh, rm = divmod(rm, 60)
                        time_str = f"Elapsed: {rh:02d}:{rm:02d}:{rs:02d} | Calculating ETA..."

                    self.progress.emit(percent, f"Encoded {completed_files}/{total_files} files | {time_str}")
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.finished.emit(False, str(e))
            return

        # 4. Fast Merge Phase
        self.progress.emit(90, "Merging files & embedding metadata...")
        
        concat_path = os.path.join(temp_dir, "inputs.txt")
        with open(concat_path, "w", encoding="utf-8") as cf:
            for ef in encoded_files:
                # We just write the basename because we will run ffmpeg INSIDE the temp dir
                cf.write(f"file '{os.path.basename(ef)}'\n")

        meta_file_path = os.path.join(temp_dir, "metadata.txt")
        with open(meta_file_path, "w", encoding="utf-8") as mf:
            mf.write(meta_text)

        abs_cover = os.path.abspath(self.cover_path)
        abs_output = os.path.abspath(self.output_file)

        # -c copy grabs the pre-encoded AAC files and stitches them instantly
        merge_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", "inputs.txt",
            "-i", abs_cover,
            "-i", "metadata.txt",
            "-map", "0:a", "-map", "1:v", "-map_metadata", "2",
            "-c:v", "copy", "-c:a", "copy",
            "-disposition:v", "attached_pic",
            abs_output
        ]

        merge_res = subprocess.run(merge_cmd, cwd=temp_dir, capture_output=True, text=True, creationflags=flags)
        
        # 5. Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)

        if merge_res.returncode == 0:
            self.progress.emit(100, "Encoding Complete!")
            self.finished.emit(True, "")
        else:
            self.finished.emit(False, merge_res.stderr)


class ModernAudiobookMerger(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("M4B Audiobook Encoder")
        self.resize(950, 700)
        self.setAcceptDrops(True)
        self.set_dynamic_icon()

        self.target_folder = os.path.expanduser("~")
        self.audio_files = []
        self.cover_path = ""
        self.generated_cover = False
        self.custom_chapter_names = {}
        self.last_clicked_item = None
        self.current_preview_file = None

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)
        self.player.playbackStateChanged.connect(self.on_playback_state_changed)

        self.setStyleSheet("""
            QMainWindow, QDialog { background-color: #121212; }
            QLabel { color: #B3B3B3; font-family: 'Segoe UI', sans-serif; margin-bottom: 0px; }
            QLineEdit, QComboBox, QListWidget {
                background-color: #282828; color: #FFFFFF;
                border: 1px solid #3E3E3E; border-radius: 4px; padding: 8px;
                font-size: 14px; font-family: 'Segoe UI', sans-serif;
            }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus { border: 1px solid #F97300; }

            QListView::indicator { border: 2px solid #888888; border-radius: 3px; background-color: #181818; width: 14px; height: 14px; }
            QListView::indicator:checked { background-color: #F97300; border: 2px solid #F97300; }
            QListWidget::item { padding: 4px; }
            QListWidget::item:selected { background-color: #3E3E3E; }

            QPushButton {
                background-color: #F97300; color: #FFFFFF; border: none;
                border-radius: 20px; padding: 10px 20px;
                font-size: 14px; font-weight: bold; font-family: 'Segoe UI', sans-serif;
            }
            QPushButton:hover { background-color: #FF8C3A; }
            QPushButton:disabled { background-color: #3E3E3E; color: #888888; }
            QPushButton#secondaryBtn { background-color: transparent; border: 1px solid #B3B3B3; color: #FFFFFF; }
            QPushButton#secondaryBtn:hover { border: 1px solid #FFFFFF; }

            QPushButton#miniBtn { padding: 6px 12px; font-size: 12px; border-radius: 4px; }
            QPushButton#toggleBtn { text-align: left; background-color: transparent; border: none; color: #F97300; padding: 0px; font-size: 12px;}
            QPushButton#toggleBtn:hover { color: #FF8C3A; text-decoration: underline; }

            QFrame#coverFrame { background-color: #181818; border-radius: 8px; }
            QProgressBar { border: 1px solid #3E3E3E; border-radius: 4px; background-color: #282828; color: #FFFFFF; text-align: center; font-weight: bold; }
            QProgressBar::chunk { background-color: #F97300; border-radius: 3px; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(30)

        # --- Left Panel ---
        left_panel = QVBoxLayout()
        left_panel.setAlignment(Qt.AlignmentFlag.AlignTop)
        left_panel.setSpacing(0)

        title_label = QLabel("AUDIOBOOK TITLE")
        title_label.setStyleSheet("font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        left_panel.addWidget(title_label)
        left_panel.addSpacing(4)
        self.title_input = QLineEdit()
        self.title_input.textChanged.connect(self.update_generated_cover)
        left_panel.addWidget(self.title_input)
        left_panel.addSpacing(8)

        self.btn_toggle_meta = QPushButton("▼ Additional Information")
        self.btn_toggle_meta.setObjectName("toggleBtn")
        self.btn_toggle_meta.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_meta.clicked.connect(self.toggle_metadata)
        left_panel.addWidget(self.btn_toggle_meta)

        self.meta_widget = QWidget()
        meta_layout = QVBoxLayout(self.meta_widget)
        meta_layout.setContentsMargins(0, 10, 0, 0)

        row1 = QHBoxLayout()
        self.author_input = QLineEdit()
        self.author_input.setPlaceholderText("Author...")
        self.narrator_input = QLineEdit()
        self.narrator_input.setPlaceholderText("Narrator...")
        row1.addWidget(self.author_input)
        row1.addWidget(self.narrator_input)

        row2 = QHBoxLayout()
        self.genre_input = QLineEdit()
        self.genre_input.setPlaceholderText("Genre...")
        self.year_input = QLineEdit()
        self.year_input.setPlaceholderText("Year...")
        row2.addWidget(self.genre_input)
        row2.addWidget(self.year_input)

        meta_layout.addLayout(row1)
        meta_layout.addLayout(row2)
        self.meta_widget.hide()
        left_panel.addWidget(self.meta_widget)
        left_panel.addSpacing(25)

        quality_label = QLabel("AAC ENCODING QUALITY")
        quality_label.setStyleSheet("font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        left_panel.addWidget(quality_label)
        left_panel.addSpacing(4)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems([
            "Maximum Quality AAC (320k - Recommended)",
            "High Quality AAC (256k)",
            "Standard AAC (192k)",
            "Space Saver AAC (128k)"
        ])
        left_panel.addWidget(self.quality_combo)
        left_panel.addSpacing(25)

        files_header = QHBoxLayout()
        files_header.setContentsMargins(0, 0, 0, 0)
        files_label = QLabel("SUPPORTED AUDIO FILES IN FOLDER")
        files_label.setStyleSheet("font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        files_header.addWidget(files_label)
        files_header.addStretch()
        self.btn_expand = QPushButton("⛶ Expand")
        self.btn_expand.setObjectName("secondaryBtn")
        self.btn_expand.setStyleSheet("padding: 2px 8px; font-size: 11px; border-radius: 4px;")
        self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand.clicked.connect(self.open_expanded_list)
        self.btn_expand.hide()
        files_header.addWidget(self.btn_expand)
        left_panel.addLayout(files_header)
        left_panel.addSpacing(4)

        self.file_list = QListWidget()
        self.file_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.file_list.setMouseTracking(True)
        self.file_list.setItemDelegate(HoverDelegate(self.file_list))
        self.file_list.itemClicked.connect(self.on_item_clicked)
        left_panel.addWidget(self.file_list)
        left_panel.addSpacing(5)

        track_toolbar = QHBoxLayout()
        track_toolbar.setContentsMargins(0, 0, 0, 0)
        self.btn_preview = QPushButton("▶ Preview Track")
        self.btn_preview.setObjectName("secondaryBtn")
        self.btn_preview.setStyleSheet("padding: 4px 12px; border-radius: 4px; font-size: 12px;")
        self.btn_preview.clicked.connect(self.toggle_preview_track)
        track_toolbar.addWidget(self.btn_preview)
        track_toolbar.addStretch()
        left_panel.addLayout(track_toolbar)
        left_panel.addSpacing(20)

        folder_layout = QHBoxLayout()
        folder_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_import = QPushButton("Import Files")
        self.btn_import.setObjectName("secondaryBtn")
        self.btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_import.clicked.connect(self.browse_files)

        folder_layout.addWidget(self.btn_import)
        folder_layout.addSpacing(20)
        self.status_label = QLabel("Drag and drop files or folders.")
        self.status_label.setStyleSheet("color: #FFFFFF; font-size: 13px;")
        self.status_label.setWordWrap(True)
        folder_layout.addWidget(self.status_label, stretch=1)
        left_panel.addLayout(folder_layout)
        left_panel.addSpacing(20)

        self.btn_merge = QPushButton("ENCODE TO M4B")
        self.btn_merge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_merge.setEnabled(False)
        self.btn_merge.clicked.connect(self.execute_merge)
        left_panel.addWidget(self.btn_merge)

        left_panel.addSpacing(10)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        left_panel.addWidget(self.progress_bar)

        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet("color: #F97300; font-size: 11px; font-weight: bold;")
        self.eta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eta_label.hide()
        left_panel.addWidget(self.eta_label)

        # --- Right Panel (Cover Art & Chapters) ---
        right_panel = QVBoxLayout()
        right_panel.setSpacing(0)  # FIX 1: match left panel so label→dropdown gap is exactly 4px

        chapter_label = QLabel("CHAPTER MARKERS")
        chapter_label.setStyleSheet("font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        right_panel.addWidget(chapter_label)
        right_panel.addSpacing(4)

        self.chapter_combo = QComboBox()
        self.chapter_combo.addItems(["Auto-Generate from Files", "Custom Chapter Names", "No Chapters"])
        self.chapter_combo.currentTextChanged.connect(self.on_chapter_mode_change)
        right_panel.addWidget(self.chapter_combo)
        right_panel.addSpacing(4)

        self.btn_edit_chapters = QPushButton("✎ Edit Chapter Names")
        self.btn_edit_chapters.setObjectName("secondaryBtn")
        self.btn_edit_chapters.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_chapters.clicked.connect(self.open_chapter_editor)
        self.btn_edit_chapters.hide()
        right_panel.addWidget(self.btn_edit_chapters)

        # This stretch will separate the top items from the bottom items.
        right_panel.addStretch()

        self.cover_frame = QFrame()
        self.cover_frame.setObjectName("coverFrame")
        self.cover_frame.setFixedSize(350, 350)
        cover_layout = QVBoxLayout(self.cover_frame)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        self.cover_image_label = QLabel("No Cover Art")
        self.cover_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_image_label.setStyleSheet("font-size: 16px; color: #535353;")
        cover_layout.addWidget(self.cover_image_label)
        right_panel.addWidget(self.cover_frame)
        right_panel.addSpacing(15)

        self.btn_change_cover = QPushButton("Change Cover Art")
        self.btn_change_cover.setObjectName("secondaryBtn")
        self.btn_change_cover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_change_cover.clicked.connect(self.change_cover)
        right_panel.addWidget(self.btn_change_cover)

        main_layout.addLayout(left_panel, stretch=1)
        main_layout.addLayout(right_panel)

    def set_dynamic_icon(self):
        pixmap = QPixmap(256, 256)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#181818"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, 256, 256, 50, 50)
        painter.setBrush(QColor("#F97300"))
        painter.drawEllipse(28, 28, 200, 200)
        painter.setBrush(QColor("#181818"))
        polygon = QPolygonF([QPointF(105, 85), QPointF(105, 171), QPointF(175, 128)])
        painter.drawPolygon(polygon)
        painter.end()
        self.setWindowIcon(QIcon(pixmap))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.accept()
        else: event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                self.process_folder(path)
                break
            elif os.path.isfile(path):
                self.add_single_file(path)

    def browse_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", self.target_folder,
                                                "Audio Files (*.mp3 *.m4a *.aac *.flac *.wav *.wma *.ogg)")
        if files:
            for f in files:
                self.add_single_file(f)

    def toggle_metadata(self):
        if self.meta_widget.isHidden():
            self.meta_widget.show()
            self.btn_toggle_meta.setText("▲ Hide Additional Information")
        else:
            self.meta_widget.hide()
            self.btn_toggle_meta.setText("▼ Additional Information")

    def open_expanded_list(self):
        dialog = ExpandedListDialog(self, self.file_list)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_states = dialog.get_updated_states()
            for i, state in enumerate(new_states):
                self.file_list.item(i).setCheckState(state)

    def on_chapter_mode_change(self, text):
        if text == "Custom Chapter Names":
            self.btn_edit_chapters.show()
        else:
            self.btn_edit_chapters.hide()

    def open_chapter_editor(self):
        checked_items = []
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked_items.append(item)
        if not checked_items:
            QMessageBox.warning(self, "No Files", "Please select or import audio files first.")
            return
        dialog = ChapterEditorDialog(self, checked_items, self.custom_chapter_names)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.custom_chapter_names = dialog.get_names()

    # FIX 3: checkbox area → immediate toggle; track text area → still needs 2 clicks
    def on_item_clicked(self, item):
        pos = self.file_list.viewport().mapFromGlobal(QCursor.pos())
        index = self.file_list.indexFromItem(item)
        rect = self.file_list.visualRect(index)
        # Indicator is 14px wide (per stylesheet) + surrounding padding; 28px is a safe threshold
        in_checkbox_area = (pos.x() - rect.left()) < 28

        if in_checkbox_area:
            # Qt already toggled the check state on this click — don't interfere
            self.last_clicked_item = item
        elif self.last_clicked_item == item:
            current = item.checkState()
            item.setCheckState(Qt.CheckState.Unchecked if current == Qt.CheckState.Checked else Qt.CheckState.Checked)
        else:
            self.file_list.setCurrentItem(item)
            self.last_clicked_item = item

    def get_current_bitrate(self):
        qs = self.quality_combo.currentText()
        if "256k" in qs: return "256k"
        if "192k" in qs: return "192k"
        if "128k" in qs: return "128k"
        return "320k"

    # FIX 2: export preview as AAC using selected bitrate to evaluate output quality
    def toggle_preview_track(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.stop()
            return

        item = self.file_list.currentItem()
        if not item:
            QMessageBox.information(self, "Preview", "Please click a track in the list first.")
            return

        track_path = item.data(Qt.ItemDataRole.UserRole)
        if not os.path.exists(track_path):
            return

        self.btn_preview.setText("Rendering...")
        self.btn_preview.setEnabled(False)
        QApplication.processEvents()

        # Force Qt to release the old file lock, then clean it up
        if self.current_preview_file and os.path.exists(self.current_preview_file):
            self.player.setSource(QUrl())
            try:
                os.remove(self.current_preview_file)
            except Exception:
                pass

        temp_dir = tempfile.gettempdir()
        
        # Generate a unique filename so QMediaPlayer doesn't incorrectly cache the audio
        self.current_preview_file = os.path.join(temp_dir, f"m4b_preview_{uuid.uuid4().hex[:8]}.m4a")
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        
        bitrate = self.get_current_bitrate()
        
        # Added -vn to strip out embedded cover art which can crash the M4A preview render
        cmd = ["ffmpeg", "-y", "-i", track_path, "-t", "15",
               "-vn", "-c:a", "aac", "-b:a", bitrate, self.current_preview_file]

        success = False
        error_msg = ""
        try:
            # Added text=True so we can read the error message properly
            result = subprocess.run(cmd, creationflags=flags,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if (result.returncode == 0
                    and os.path.exists(self.current_preview_file)
                    and os.path.getsize(self.current_preview_file) > 0):
                self.player.stop()
                self.player.setSource(QUrl.fromLocalFile(self.current_preview_file))
                self.player.play()
                success = True
            else:
                error_msg = result.stderr if result.stderr else "Unknown FFmpeg error"
        except Exception as e:
            error_msg = str(e)

        if not success:
            if len(error_msg) > 400: error_msg = "... " + error_msg[-400:]
            QMessageBox.warning(self, "Preview Error", f"Failed to generate preview audio.\n\nDetails:\n{error_msg}")
            self.btn_preview.setText("▶ Preview Track")
            self.btn_preview.setStyleSheet("padding: 4px 12px; border-radius: 4px; font-size: 12px;")

        self.btn_preview.setEnabled(True)

    def on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_preview.setText("■ Stop Preview")
            self.btn_preview.setStyleSheet("padding: 4px 12px; border-radius: 4px; font-size: 12px; border-color: #F97300; color: #F97300;")
        else:
            self.btn_preview.setText("▶ Preview Track")
            self.btn_preview.setStyleSheet("padding: 4px 12px; border-radius: 4px; font-size: 12px;")

    def change_cover(self):
        if not self.file_list.count():
            QMessageBox.warning(self, "Hold Up", "Please add some audio files first.")
            return
        file_filter = "Images (*.png *.jpg *.jpeg)"
        image_path, _ = QFileDialog.getOpenFileName(self, "Select Cover Art", self.target_folder, file_filter)
        if image_path:
            self.cover_path = image_path
            self.generated_cover = False
            self.display_cover(self.cover_path)

    def add_single_file(self, file_path):
        supported = ('.mp3', '.m4a', '.aac', '.flac', '.wav', '.wma', '.ogg')
        if not file_path.lower().endswith(supported):
            return

        if not self.target_folder or self.target_folder == os.path.expanduser("~"):
            self.target_folder = os.path.dirname(file_path)
            self.title_input.setText(os.path.basename(os.path.normpath(self.target_folder)))

        item = QListWidgetItem(os.path.basename(file_path))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, file_path)
        self.file_list.addItem(item)

        self.btn_merge.setEnabled(True)
        self.status_label.setText(f"Loaded {self.file_list.count()} files. Drag items to reorder.")
        if self.file_list.count() > 5: self.btn_expand.show()

        if not self.cover_path and not self.generated_cover:
            self.cover_path = os.path.join(self.target_folder, "generated_cover.jpg")
            self.generated_cover = True
            self.update_generated_cover()

    def process_folder(self, folder):
        self.target_folder = folder
        self.title_input.setText(os.path.basename(os.path.normpath(folder)))

        supported_exts = ('*.mp3', '*.m4a', '*.aac', '*.flac', '*.wav', '*.wma', '*.ogg')
        audio_files = []
        for ext in supported_exts:
            audio_files.extend(glob.glob(os.path.join(folder, ext)))
        audio_files.sort()

        if not audio_files:
            QMessageBox.warning(self, "No Audio", "No supported audio files found in that folder.")
            return

        for file in audio_files:
            self.add_single_file(file)

        possible_covers = glob.glob(os.path.join(folder, "*.jpg")) + glob.glob(os.path.join(folder, "*.png"))
        if possible_covers:
            self.cover_path = possible_covers[0]
            self.generated_cover = False
            self.display_cover(self.cover_path)

    def update_generated_cover(self):
        if not self.generated_cover or not self.target_folder: return
        title = self.title_input.text().strip() or "Unknown Audiobook"

        pixmap = QPixmap(600, 600)
        pixmap.fill(QColor("#282828"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#F97300"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(0, 0, 600, 15)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Segoe UI", 36, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap), title)
        painter.end()

        pixmap.save(self.cover_path, "JPG")
        self.display_cover(self.cover_path)

    def display_cover(self, image_path):
        pixmap = QPixmap(image_path)
        scaled_pixmap = pixmap.scaled(350, 350, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
        self.cover_image_label.setPixmap(scaled_pixmap)
        self.cover_image_label.setText("")

    def toggle_ui(self, enabled):
        self.title_input.setEnabled(enabled)
        self.author_input.setEnabled(enabled)
        self.narrator_input.setEnabled(enabled)
        self.genre_input.setEnabled(enabled)
        self.year_input.setEnabled(enabled)
        self.quality_combo.setEnabled(enabled)
        self.chapter_combo.setEnabled(enabled)
        self.btn_edit_chapters.setEnabled(enabled)
        self.btn_expand.setEnabled(enabled)
        self.btn_import.setEnabled(enabled)
        self.btn_merge.setEnabled(enabled)
        self.btn_change_cover.setEnabled(enabled)
        self.file_list.setEnabled(enabled)
        self.btn_preview.setEnabled(enabled)

    def execute_merge(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.stop()

        output_name = self.title_input.text().replace(" ", "_") or "Merged_Audiobook"
        self.output_file = os.path.join(self.target_folder, f"{output_name}.m4b")

        selected_files = []
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_files.append(item.data(Qt.ItemDataRole.UserRole))

        if not selected_files:
            QMessageBox.warning(self, "No Files", "You unchecked every file. There is nothing to merge!")
            return

        self.toggle_ui(False)
        self.btn_merge.setText("ENCODING...")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.eta_label.setText("Preparing...")
        self.eta_label.show()

        meta_data = {
            'title':    self.title_input.text().strip(),
            'author':   self.author_input.text().strip(),
            'narrator': self.narrator_input.text().strip(),
            'genre':    self.genre_input.text().strip(),
            'year':     self.year_input.text().strip()
        }

        c_mode_text = self.chapter_combo.currentText()
        if "Auto" in c_mode_text:   c_mode = "auto"
        elif "Custom" in c_mode_text: c_mode = "custom"
        else:                         c_mode = "none"

        self.worker = EncoderWorker(self.target_folder, selected_files, self.output_file,
                                    self.cover_path, self.quality_combo.currentText(),
                                    meta_data, c_mode, self.custom_chapter_names)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.process_finished)
        self.worker.start()

    def update_progress(self, percent, text):
        self.progress_bar.setValue(percent)
        self.eta_label.setText(text)

    def process_finished(self, success, error_msg):
        self.toggle_ui(True)
        self.btn_merge.setText("ENCODE TO M4B")
        self.progress_bar.hide()
        self.eta_label.hide()

        if success:
            self.status_label.setText("Encoding Complete!")
            QMessageBox.information(self, "Complete", "Successfully encoded the audiobook into an M4B!")
        else:
            self.status_label.setText("FFmpeg Error. Check your files.")
            if len(error_msg) > 1200: error_msg = "... " + error_msg[-1200:]
            QMessageBox.critical(self, "Encode Failed", f"FFmpeg encountered a critical error:\n\n{error_msg}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ModernAudiobookMerger()
    window.show()
    sys.exit(app.exec())
