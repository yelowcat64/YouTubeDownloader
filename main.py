from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import urllib.request
import zipfile
import os
import sys
import ssl
import certifi
from pathlib import Path
from tkinter import LEFT, RIGHT, StringVar, Tk, filedialog, messagebox
from tkinter import ttk
import tkinter as tk

def setup_ssl():
    """Naprawia problem z certyfikatami SSL w zamrożonym exe"""
    if getattr(sys, 'frozen', False):
        # Wymuszamy certyfikaty z certifi
        os.environ['SSL_CERT_FILE'] = certifi.where()
        os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()  # na wszelki wypadek

setup_ssl()  # <- Wywołanie na samym początku!

def get_app_path() -> Path:
    """Zwraca folder, w którym znajduje się plik .exe (działa zarówno w .py jak i w PyInstaller)"""
    if getattr(sys, 'frozen', False):
        # Uruchomione jako EXE
        return Path(sys.executable).parent
    else:
        # Normalny Python
        return Path(__file__).resolve().parent


# === ŚCIEŻKI ===
APP_DIR = get_app_path()
TOOLS_DIR = APP_DIR / "tools"

VERSIONS_FILE = TOOLS_DIR / "versions.json"
YT_DLP = TOOLS_DIR / "yt-dlp.exe"
FFMPEG = TOOLS_DIR / "ffmpeg.exe"

HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "YouTube-Downloader-Windows"}

FILENAME_TOKENS = {
    "Title": "%(title)s",
    "Channel": "%(channel)s",
    "Uploader": "%(uploader)s",
    "Upload Date": "%(upload_date)s",
    "Video ID": "%(id)s",
    "Playlist Index": "%(playlist_index)03d",
    "Chapter Number": "%(chapter_number)02d",
    "Chapter Title": "%(chapter_title)s"
}

PERCENT_PATTERN = re.compile(r"(?:\[download\]\s*)?(\d{1,3}(?:\.\d+)?)%")

def to_windows_path(value):
    """Convert path to be Windows friendly even if file dialog returns forward slashes."""
    return str(value).replace("/", "\\")

def fetch_github_json(url):
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=25, context=context) as response:
            return json.load(response)

    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise RuntimeError("Tools are ready.")
        raise
    except Exception as e:
        raise RuntimeError(f"Network error: {e}") from e


def download_file(url, path, report):
    context = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(url, headers=HEADERS)
    
    with urllib.request.urlopen(req, timeout=90, context=context) as response, open(path, "wb") as target:
        total, received = int(response.headers.get("Content-Length", 0)), 0
        while block := response.read(1024 * 512):
            target.write(block)
            received += len(block)
            if total:
                report(f"Downloading tool: {received * 100 // total}%")


class ToolManager:
    def __init__(self, report): self.report = report

    def ensure_tools(self, force=False):
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        try: versions = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): versions = {}
        release = fetch_github_json("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest")
        if force or not YT_DLP.exists() or versions.get("yt_dlp") != release["tag_name"]:
            self.report(f"Updating yt-dlp ({release['tag_name']})...")
            asset = next(x for x in release["assets"] if x["name"] == "yt-dlp.exe")
            temporary = YT_DLP.with_suffix(".new.exe"); download_file(asset["browser_download_url"], temporary, self.report); temporary.replace(YT_DLP)
            versions["yt_dlp"] = release["tag_name"]
        release = fetch_github_json("https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest")
        if force or not FFMPEG.exists() or versions.get("ffmpeg") != release["tag_name"]:
            self.report(f"Updating FFmpeg ({release['tag_name']})...")
            asset = next(x for x in release["assets"] if x["name"].endswith("win64-gpl.zip"))
            archive = TOOLS_DIR / "ffmpeg.zip"; download_file(asset["browser_download_url"], archive, self.report)
            with zipfile.ZipFile(archive) as zipped:
                member = next(x for x in zipped.namelist() if x.lower().endswith("/bin/ffmpeg.exe"))
                with zipped.open(member) as source, open(FFMPEG, "wb") as target: shutil.copyfileobj(source, target)
            archive.unlink(missing_ok=True); versions["ffmpeg"] = release["tag_name"]
        VERSIONS_FILE.write_text(json.dumps(versions, indent=2), encoding="utf-8")


class TokenEditor(tk.Text):
    """Text field with visible pills instead of raw yt-dlp codes."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=1, wrap="word", undo=True, **kwargs)
        self.tokens = {}
        spacer = tk.Frame(self, width=1, height=22, bg="#0f172a")
        self.spacer_path = str(spacer)
        self.tokens[self.spacer_path] = ""
        self.window_create("1.0", window=spacer, align="top")
        self.mark_set("insert", "end-1c")
        self.bind("<BackSpace>", self.on_backspace, add=True)
        self.bind("<Delete>", self.on_delete, add=True)

    def add_pill(self, label, token):
        widget = tk.Label(self, text=f"  {label}  ", bg="#312e81", fg="#ddd6fe", font=("Segoe UI Semibold", 9), padx=3, pady=1)
        self.tokens[str(widget)] = token
        self.window_create("insert", window=widget, padx=2, align="top"); self.focus_set()

    def window_at(self, index):
        for kind, value, _ in self.dump(index, f"{index} +1c", window=True):
            if kind == "window": return value
        return None

    def on_backspace(self, _event):
        if self.compare("insert", "==", "1.0"): return None
        previous = self.index("insert -1c"); widget = self.window_at(previous)
        if widget:
            if widget == self.spacer_path: return "break"
            self.delete(previous, f"{previous} +1c"); self.tokens.pop(widget, None); return "break"
        return None

    def on_delete(self, _event):
        widget = self.window_at("insert")
        if widget:
            if widget == self.spacer_path: return "break"
            self.delete("insert", "insert +1c"); self.tokens.pop(widget, None); return "break"
        return None

    def get_template(self):
        result = []
        for kind, value, _ in self.dump("1.0", "end-1c", text=True, window=True):
            result.append(self.tokens.get(value, "") if kind == "window" else value)
        return "".join(result).strip()


class DownloaderApp:
    def __init__(self, root):
        self.root, self.process, self.events = root, None, queue.Queue()
        self.url, self.folder, self.download_type = StringVar(), StringVar(value=to_windows_path(Path.home() / "Downloads")), StringVar(value="Video")
        self.video_quality, self.audio_quality = StringVar(value="High Quality"), StringVar(value="High Quality")
        self.video_format, self.audio_format = StringVar(value="mp4"), StringVar(value="mp3")
        self.status, self.percent = StringVar(value="Ready. Paste video or playlist link."), StringVar(value="0%")
        self.command_preview = StringVar(value="Command will appear here after clicking Download.")
        self.build(); self.root.after(120, self.poll_events); self.root.after(350, self.check_tools)

    def build(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Custom.TEntry", fieldbackground="#0f172a", foreground="#f9fafb")
        style.configure("Custom.TButton", background="#0f172a", foreground="#f9fafb")
        style.map("Custom.TButton", background=[("active", "#312e81")])
        self.root.title("YouTube Downloader"); self.root.geometry("470x470"); self.root.minsize(860, 680); self.root.configure(bg="#111827")
        style = ttk.Style(); style.theme_use("clam")
        style.configure("TFrame", background="#111827"); style.configure("Card.TFrame", background="#1f2937")
        style.configure("TLabel", background="#111827", foreground="#e5e7eb", font=("Segoe UI", 10)); style.configure("Title.TLabel", font=("Segoe UI Semibold", 23), foreground="#f9fafb"); style.configure("Sub.TLabel", foreground="#9ca3af")
        style.configure("TEntry", fieldbackground="#0f172a", foreground="#f9fafb", insertcolor="#f9fafb", padding=9)
        style.configure("TCombobox", fieldbackground="#0f172a", background="#0f172a", foreground="#f9fafb", arrowcolor="#c4b5fd", padding=8)
        style.map("TCombobox", fieldbackground=[("readonly", "#0f172a")], selectbackground=[("readonly", "#0f172a")], selectforeground=[("readonly", "#f9fafb")])
        for option, value in (("*TCombobox*Listbox.background", "#1f2937"), ("*TCombobox*Listbox.foreground", "#f9fafb"), ("*TCombobox*Listbox.selectBackground", "#7c3aed"), ("*TCombobox*Listbox.selectForeground", "#ffffff")): self.root.option_add(option, value)
        style.configure("Accent.TButton", background="#7c3aed", foreground="white", font=("Segoe UI Semibold", 11), padding=(18, 10)); style.map("Accent.TButton", background=[("active", "#8b5cf6"), ("disabled", "#4b5563")])
        style.configure("TButton", padding=7); style.configure("Purple.Horizontal.TProgressbar", troughcolor="#0f172a", background="#8b5cf6", lightcolor="#a78bfa", darkcolor="#7c3aed", bordercolor="#0f172a", thickness=12)
        style.configure("Dark.Vertical.TScrollbar", background="#111827", troughcolor="#111827", bordercolor="#111827", arrowcolor="#9ca3af", width=12)
        style.map("Dark.Vertical.TScrollbar", background=[("active", "#111827")])
        scroll_host = ttk.Frame(self.root); scroll_host.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(scroll_host, bg="#111827", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(scroll_host, orient="vertical", command=self.canvas.yview, style="Dark.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill="y"); self.canvas.pack(side=LEFT, fill="both", expand=True)
        outer = ttk.Frame(self.canvas, padding=24)
        self.canvas_window = self.canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", self.update_scroll_region)
        self.canvas.bind("<Configure>", self.fit_scroll_content)
        self.root.bind_all("<MouseWheel>", self.scroll_with_mouse)
        card = ttk.Frame(outer, style="Card.TFrame", padding=20); card.pack(fill="x")
        self.field(card, "URL Address", self.url, "Paste link to video, playlist or channel")
        row = ttk.Frame(card, style="Card.TFrame"); row.pack(fill="x", pady=(14, 0)); ttk.Label(row, text="Destination folder", background="#1f2937").pack(anchor="w")
        ttk.Entry(row, textvariable=self.folder, style="Custom.TEntry").pack(side=LEFT, fill="x", expand=True, pady=(5, 0)); ttk.Button(row, text="Browse...", command=self.pick_folder, style="Custom.TButton").pack(side=RIGHT, padx=(8, 0), pady=(5, 0))
        ttk.Label(card, text="File name", background="#1f2937").pack(anchor="w", pady=(14, 0))
        self.name_editor = TokenEditor(card, bg="#0f172a", fg="#f9fafb", insertbackground="#f9fafb", relief="flat", padx=10, pady=10, font=("Segoe UI", 12), highlightthickness=2, highlightbackground="#ddd6fe", highlightcolor="#4a6984")
        self.name_editor.pack(fill="x", pady=(5, 0)); self.name_editor.add_pill("Title", FILENAME_TOKENS["Title"])
        pills = ttk.Frame(card, style="Card.TFrame"); pills.pack(fill="x", pady=(10, 0))
        for label, token in FILENAME_TOKENS.items():
            pill = tk.Label(pills, text=f"  {label}  ", cursor="hand2", bg="#312e81", fg="#ddd6fe", font=("Segoe UI Semibold", 9), padx=3, pady=3); pill.pack(side=LEFT, padx=(0, 6), pady=2)
            pill.bind("<Button-1>", lambda _e, title=label, code=token: self.name_editor.add_pill(title, code))
        options = ttk.Frame(outer, style="Card.TFrame", padding=20); options.pack(fill="x", pady=14)
        mode = ttk.Frame(options, style="Card.TFrame"); mode.pack(fill="x"); ttk.Label(mode, text="What do you want to download?", background="#1f2937").pack(side=LEFT, padx=(0, 12))
        kind_box = ttk.Combobox(mode, textvariable=self.download_type, values=["Audio", "Video", "Video without audio"], state="readonly", width=24); kind_box.pack(side=LEFT); kind_box.bind("<<ComboboxSelected>>", self.update_option_visibility)
        self.grid = ttk.Frame(options, style="Card.TFrame"); self.grid.pack(fill="x", pady=(16, 0))
        self.video_q = self.combo(self.grid, "Video Quality", self.video_quality, ["High Quality", "Medium Quality", "Low Quality"])
        self.video_f = self.combo(self.grid, "Video Format", self.video_format, ["mp4", "webm", "avi"])
        self.audio_q = self.combo(self.grid, "Audio Quality", self.audio_quality, ["High Quality", "Medium Quality", "Low Quality"])
        self.audio_f = self.combo(self.grid, "Audio Format", self.audio_format, ["mp3", "m4a", "wav"])
        bottom = ttk.Frame(outer); bottom.pack(fill="both", expand=True); status_row = ttk.Frame(bottom); status_row.pack(fill="x", pady=(2, 5))
        ttk.Label(status_row, textvariable=self.status, style="Sub.TLabel").pack(side=LEFT); ttk.Label(status_row, textvariable=self.percent, foreground="#c4b5fd", font=("Segoe UI Semibold", 10)).pack(side=RIGHT)
        self.progress = ttk.Progressbar(bottom, maximum=100, value=0, mode="determinate", style="Purple.Horizontal.TProgressbar"); self.progress.pack(fill="x")
        actions = ttk.Frame(bottom); actions.pack(fill="x", pady=(16, 0))
        self.download_button = ttk.Button(actions, text="Download", style="Accent.TButton", command=self.start_download); self.download_button.pack(side=RIGHT)
        self.update_option_visibility()

    def field(self, parent, label, variable, hint):
        ttk.Label(parent, text=label, background="#1f2937").pack(anchor="w"); ttk.Entry(parent, textvariable=variable).pack(fill="x", pady=(5, 1)); ttk.Label(parent, text=hint, background="#1f2937", foreground="#9ca3af").pack(anchor="w")

    def combo(self, parent, label, variable, values):
        box = ttk.Frame(parent, style="Card.TFrame"); ttk.Label(box, text=label, background="#1f2937").pack(anchor="w"); ttk.Combobox(box, textvariable=variable, values=values, state="readonly").pack(fill="x", pady=(5, 0)); return box

    def update_option_visibility(self, _event=None):
        for box in (self.video_q, self.video_f, self.audio_q, self.audio_f): box.grid_forget()
        visible = (self.audio_q, self.audio_f) if self.download_type.get() == "Audio" else (self.video_q, self.video_f) if self.download_type.get() == "Video without audio" else (self.video_q, self.audio_q, self.video_f)
        for column, box in enumerate(visible): self.grid.columnconfigure(column, weight=1); box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 10, 0))

    def pick_folder(self):
        if folder := filedialog.askdirectory(initialdir=self.folder.get() or str(Path.home())): self.folder.set(to_windows_path(folder))

    def update_scroll_region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def fit_scroll_content(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def scroll_with_mouse(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def check_tools(self, force=False):
        if self.process: return
        self.set_busy(True, "Checking tools..."); threading.Thread(target=self.tool_thread, args=(force,), daemon=True).start()

    def tool_thread(self, force):
        try: ToolManager(self.emit).ensure_tools(force); self.emit("__tools_done__")
        except Exception as error: self.emit("__error__" + str(error))

    def output_template(self):
        path = Path(self.folder.get().strip() or Path.home() / "Downloads") / f"{self.name_editor.get_template() or '%(title)s'}.%(ext)s"
        return to_windows_path(path)

    def build_command(self):
        command = [to_windows_path(YT_DLP), "--newline", "--no-warnings", "--force-overwrites", "--ffmpeg-location", to_windows_path(TOOLS_DIR), "-o", self.output_template()]
        audio_limit = {"High Quality": None, "Medium Quality": 192, "Low Quality": 128}[self.audio_quality.get()]
        if self.download_type.get() == "Audio":
            quality = {"High Quality": "0", "Medium Quality": "5", "Low Quality": "7"}[self.audio_quality.get()]
            command += ["-f", "bestaudio/best", "-x", "--audio-format", self.audio_format.get(), "--audio-quality", quality]
        else:
            limit = {"High Quality": 1080, "Medium Quality": 720, "Low Quality": 480}[self.video_quality.get()]
            video = "bestvideo*" if not limit else f"bestvideo*[height<={limit}]"
            if self.download_type.get() == "Video without audio": command += ["-f", f"{video}/best", "--remux-video", self.video_format.get()]
            else:
                audio = "bestaudio" if not audio_limit else f"bestaudio[abr<={audio_limit}]"; command += ["-f", f"{video}+{audio}/best", "--merge-output-format", self.video_format.get()]
        return command + [self.url.get().strip()]

    def start_download(self):
        if not self.url.get().strip(): messagebox.showwarning("Missing link", "Paste link to video, playlist or channel."); return
        if not YT_DLP.exists() or not FFMPEG.exists(): messagebox.showinfo("Preparing", "I will first download required tools."); self.check_tools(); return
        try: Path(self.folder.get()).mkdir(parents=True, exist_ok=True)
        except OSError as error: messagebox.showerror("Folder unavailable", str(error)); return
        command = self.build_command(); self.command_preview.set(subprocess.list2cmdline(command)); self.set_busy(True, "Starting download..."); threading.Thread(target=self.download_thread, args=(command,), daemon=True).start()

    def download_thread(self, command):
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            self.emit("__running__")
            for line in self.process.stdout: self.emit(line.strip())
            self.emit("__done__" + str(self.process.wait()))
        except Exception as error: self.emit("__error__" + str(error))

    def cancel(self):
        if self.process and self.process.poll() is None: self.process.terminate(); self.status.set("Stopping download...")

    def emit(self, text): self.events.put(text)

    def set_busy(self, busy, message):
        self.status.set(message); self.progress["value"] = 0; self.percent.set("0%")
        self.download_button.configure(state="disabled" if busy else "normal")

    def poll_events(self):
        try:
            while True:
                item = self.events.get_nowait()
                if item == "__tools_done__": self.process = None; self.set_busy(False, "Tools are ready.")
                elif item.startswith("__done__"):
                    code = int(item[8:]); self.process = None
                    self.set_busy(False, "Download completed." if code == 0 else f"Download finished with code {code}.")
                    if code == 0: self.progress["value"] = 100; self.percent.set("100%")
                elif item.startswith("__error__"):
                    self.process = None; self.set_busy(False, "An error occurred — see message."); messagebox.showerror("Error", item[9:])
                elif item:
                    if match := PERCENT_PATTERN.search(item):
                        value = min(100, float(match.group(1))); self.progress["value"] = value; self.percent.set(f"{value:.1f}%" if value % 1 else f"{int(value)}%")
                    self.status.set(item[-180:])
        except queue.Empty: pass
        self.root.after(120, self.poll_events)

def resource_path(relative_path: str) -> str:
    """Działa zarówno w .py jak i w PyInstallerze"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

if __name__ == "__main__":
    window = Tk(); window.iconbitmap(resource_path("icon.ico")); DownloaderApp(window); window.mainloop()
