"""
RAJ VIDEO MERGE
Batch video merger with group-size control, lossless-level quality,
and optional seamless transitions.

Requires ffmpeg.exe + ffprobe.exe to be placed in the same folder as
this script/exe (or available in PATH).
"""

import os
import sys
import json
import shutil
import subprocess
import threading
import tempfile
import customtkinter as ctk
from tkinter import filedialog, Listbox, MULTIPLE, END

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm")
TRANSITIONS = ["None", "Crossfade", "Fade to Black", "Slide Left", "Wipe"]
TRANSITION_DURATION = 1.0  # seconds, used only when a transition is selected
CRF = "15"                 # lossless-level quality (14-16 range)
PRESET = "slow"


def get_ffmpeg_path(name):
    """Look for ffmpeg/ffprobe next to the exe first, then in PATH."""
    base = os.path.dirname(os.path.abspath(sys.argv[0]))
    local = os.path.join(base, name + (".exe" if os.name == "nt" else ""))
    if os.path.exists(local):
        return local
    found = shutil.which(name)
    if found:
        return found
    return name  # let it fail loudly later with a clear error


FFMPEG = get_ffmpeg_path("ffmpeg")
FFPROBE = get_ffmpeg_path("ffprobe")


def probe(path):
    """Return (width, height, fps, has_audio) for a video file."""
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_streams", path
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    w = h = 0
    fps = 30.0
    has_audio = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not w:
            w = int(s.get("width", 0))
            h = int(s.get("height", 0))
            rate = s.get("avg_frame_rate", "30/1")
            try:
                num, den = rate.split("/")
                fps = float(num) / float(den) if float(den) != 0 else 30.0
            except Exception:
                fps = 30.0
        if s.get("codec_type") == "audio":
            has_audio = True
    return w, h, fps, has_audio


def normalize_clip(src, dst, target_w, target_h, target_fps, log):
    """Re-encode a clip to a common resolution/fps/codec so joins are seamless."""
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,fps={target_fps}"
    )
    cmd = [
        FFMPEG, "-y", "-i", src,
        "-vf", vf,
        "-c:v", "libx264", "-crf", CRF, "-preset", PRESET,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        dst
    ]
    log(f"Normalizing: {os.path.basename(src)}")
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def concat_no_transition(clips, output_path, log):
    """Seamless straight cut merge via concat demuxer (clips already normalized)."""
    list_file = output_path + "_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    cmd = [
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", output_path
    ]
    log(f"Merging (no transition) -> {os.path.basename(output_path)}")
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    os.remove(list_file)


def merge_with_transition(clips, durations, output_path, transition, log):
    """Merge normalized clips with an xfade/acrossfade transition between each pair."""
    n = len(clips)
    xfade_map = {
        "Crossfade": "fade",
        "Fade to Black": "fadeblack",
        "Slide Left": "slideleft",
        "Wipe": "wiperight",
    }
    xfade_type = xfade_map.get(transition, "fade")

    inputs = []
    for c in clips:
        inputs += ["-i", c]

    filter_parts = []
    prev_v = "0:v"
    prev_a = "0:a"
    running_offset = durations[0] - TRANSITION_DURATION

    for i in range(1, n):
        out_v = f"v{i}"
        out_a = f"a{i}"
        filter_parts.append(
            f"[{prev_v}][{i}:v]xfade=transition={xfade_type}:duration={TRANSITION_DURATION}:offset={running_offset:.3f}[{out_v}]"
        )
        filter_parts.append(
            f"[{prev_a}][{i}:a]acrossfade=d={TRANSITION_DURATION}[{out_a}]"
        )
        prev_v = out_v
        prev_a = out_a
        if i < n - 1:
            running_offset += durations[i] - TRANSITION_DURATION

    filter_complex = ";".join(filter_parts)
    cmd = [
        FFMPEG, "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{prev_v}]", "-map", f"[{prev_a}]",
        "-c:v", "libx264", "-crf", CRF, "-preset", PRESET,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path
    ]
    log(f"Merging with {transition} -> {os.path.basename(output_path)}")
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def get_duration(path):
    cmd = [
        FFPROBE, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RAJ VIDEO MERGE")
        self.geometry("780x640")

        self.input_folder = ""
        self.output_folder = ""
        self.merge_list = []  # ordered full paths

        row1 = ctk.CTkFrame(self)
        row1.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkButton(row1, text="Select Input Folder", command=self.select_input).pack(side="left")
        self.input_label = ctk.CTkLabel(row1, text="No folder selected")
        self.input_label.pack(side="left", padx=10)

        ctk.CTkLabel(self, text="Videos in folder (ctrl/shift click to multi-select, then Add):").pack(anchor="w", padx=12)
        self.avail_list = Listbox(self, selectmode=MULTIPLE, height=8, bg="#2b2b2b", fg="white")
        self.avail_list.pack(fill="both", expand=True, padx=12, pady=4)

        add_row = ctk.CTkFrame(self)
        add_row.pack(fill="x", padx=12)
        ctk.CTkButton(add_row, text="Add Selected ->", command=self.add_selected).pack(side="left")
        ctk.CTkButton(add_row, text="Move Up", command=self.move_up).pack(side="left", padx=6)
        ctk.CTkButton(add_row, text="Move Down", command=self.move_down).pack(side="left")
        ctk.CTkButton(add_row, text="Remove", command=self.remove_selected).pack(side="left", padx=6)

        ctk.CTkLabel(self, text="Merge order:").pack(anchor="w", padx=12, pady=(8, 0))
        self.order_list = Listbox(self, height=8, bg="#1f1f1f", fg="white")
        self.order_list.pack(fill="both", expand=True, padx=12, pady=4)

        row2 = ctk.CTkFrame(self)
        row2.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkButton(row2, text="Select Output Folder", command=self.select_output).pack(side="left")
        self.output_label = ctk.CTkLabel(row2, text="No folder selected")
        self.output_label.pack(side="left", padx=10)

        row3 = ctk.CTkFrame(self)
        row3.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(row3, text="Merge group size:").pack(side="left")
        self.group_size = ctk.CTkEntry(row3, width=60)
        self.group_size.insert(0, "2")
        self.group_size.pack(side="left", padx=8)

        ctk.CTkLabel(row3, text="Transition:").pack(side="left", padx=(20, 0))
        self.transition_var = ctk.StringVar(value="None")
        ctk.CTkOptionMenu(row3, values=TRANSITIONS, variable=self.transition_var).pack(side="left", padx=8)

        ctk.CTkButton(self, text="START MERGE", fg_color="#1f8b4c", command=self.start_merge_thread).pack(pady=10)

        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=12, pady=(0, 6))

        self.log_box = ctk.CTkTextbox(self, height=120)
        self.log_box.pack(fill="both", padx=12, pady=(0, 12))

    def log(self, msg):
        self.log_box.insert(END, msg + "\n")
        self.log_box.see(END)
        self.update_idletasks()

    def select_input(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.input_folder = folder
        self.input_label.configure(text=folder)
        self.avail_list.delete(0, END)
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(VIDEO_EXTS):
                self.avail_list.insert(END, f)

    def add_selected(self):
        for i in self.avail_list.curselection():
            name = self.avail_list.get(i)
            full = os.path.join(self.input_folder, name)
            if full not in self.merge_list:
                self.merge_list.append(full)
                self.order_list.insert(END, name)

    def move_up(self):
        sel = self.order_list.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        self.merge_list[i - 1], self.merge_list[i] = self.merge_list[i], self.merge_list[i - 1]
        self.refresh_order_list(select=i - 1)

    def move_down(self):
        sel = self.order_list.curselection()
        if not sel or sel[0] == len(self.merge_list) - 1:
            return
        i = sel[0]
        self.merge_list[i + 1], self.merge_list[i] = self.merge_list[i], self.merge_list[i + 1]
        self.refresh_order_list(select=i + 1)

    def remove_selected(self):
        sel = list(self.order_list.curselection())
        for i in reversed(sel):
            del self.merge_list[i]
        self.refresh_order_list()

    def refresh_order_list(self, select=None):
        self.order_list.delete(0, END)
        for f in self.merge_list:
            self.order_list.insert(END, os.path.basename(f))
        if select is not None:
            self.order_list.selection_set(select)

    def select_output(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_folder = folder
            self.output_label.configure(text=folder)

    def start_merge_thread(self):
        threading.Thread(target=self.run_merge, daemon=True).start()

    def run_merge(self):
        try:
            if not self.merge_list:
                self.log("ERROR: Add videos to the merge order list first.")
                return
            if not self.output_folder:
                self.log("ERROR: Select an output folder first.")
                return
            try:
                group_size = int(self.group_size.get())
                assert group_size >= 2
            except Exception:
                self.log("ERROR: Group size must be a whole number >= 2.")
                return

            transition = self.transition_var.get()
            groups = [self.merge_list[i:i + group_size] for i in range(0, len(self.merge_list), group_size)]
            total = len(groups)
            self.progress.set(0)

            with tempfile.TemporaryDirectory() as tmp:
                for idx, group in enumerate(groups, start=1):
                    self.log(f"--- Group {idx}/{total} ({len(group)} clip(s)) ---")

                    if len(group) == 1:
                        out_name = os.path.basename(group[0])
                        out_path = os.path.join(self.output_folder, out_name)
                        shutil.copy2(group[0], out_path)
                        self.log(f"Leftover clip saved as-is -> {out_name}")
                        self.progress.set(idx / total)
                        continue

                    w, h, fps, _ = probe(group[0])

                    normalized = []
                    durations = []
                    for j, clip in enumerate(group):
                        norm_path = os.path.join(tmp, f"g{idx}_c{j}.mp4")
                        normalize_clip(clip, norm_path, w, h, fps, self.log)
                        normalized.append(norm_path)
                        durations.append(get_duration(norm_path))

                    out_name = os.path.basename(group[0])
                    out_path = os.path.join(self.output_folder, out_name)

                    if transition == "None":
                        concat_no_transition(normalized, out_path, self.log)
                    else:
                        merge_with_transition(normalized, durations, out_path, transition, self.log)

                    self.log(f"Done -> {out_name}")
                    self.progress.set(idx / total)

            self.log("=== ALL GROUPS MERGED SUCCESSFULLY ===")
        except subprocess.CalledProcessError as e:
            self.log("FFMPEG ERROR:")
            self.log(e.stderr[-1500:] if e.stderr else str(e))
        except Exception as e:
            self.log(f"ERROR: {e}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
