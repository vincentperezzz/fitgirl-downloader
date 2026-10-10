# FitGirl to IDM Batch Downloader

A fast, automated link parser and Internet Download Manager (IDM) batch importer designed specifically for FitGirl Repacks.

## Features

- ⚡ **Automated Link Extraction**: Parses FitGirl pages (such as `https://fitgirl-repacks.site/marvels-spider-man-2/`) and automatically extracts all part files and mirrors.
- 🎯 **Host Categorization**: Groups links by mirror hosters (DataNodes, FuckingFast, FileKeeper, Pixeldrain, MultiUpload, MediaFire, etc.).
- 🚀 **Direct IDM Auto-Queueing**: Automatically feeds all URLs into `IDMan.exe` queue with one click.
- 📄 **Multiple Export Formats**:
  - Native IDM Export (`.ef2`) format with pre-filled filenames and referers.
  - Plain Text URL list (`.txt`) for IDM import.
  - Windows Batch script (`.bat`) generator.
- 💻 **CLI & Graphical UI**: Choice between an interactive terminal CLI (`fitgirl_idm.py`) and a desktop GUI (`gui.py`).
- 📦 **Automated Multi-Part RAR Extraction**: Auto-detects 7-Zip (`7z.exe`) and WinRAR, monitors IDM downloads until 100% complete, and extracts all parts at once into the game setup folder.
- 🧹 **Automatic Disk Space Cleanup**: Automatically and permanently deletes the `.rar` split archive parts after successful extraction, freeing up to 100+ GB of disk space!

## Standalone Windows Executable (.EXE)

A standalone single-file Windows executable is built in:
```
dist\FitGirlDownloader.exe
```
- No Python installation needed.
- Double-click to launch the GUI directly without opening any command prompts.
- To rebuild the executable at any time, run `build_exe.bat`.

## Quick Start

### Windows Launcher (`run.bat`)
Simply double-click `run.bat` to launch the GUI script, or run `dist\FitGirlDownloader.exe`.

### Standalone Extractor CLI
```bash
# Monitor folder and auto-extract when all parts finish downloading:
python extractor.py --watch

# Extract existing RAR parts and permanently delete the RARs:
python extractor.py --extract-and-delete
```

## How IDM Integration Works

1. **Direct Queueing**: The tool locates your installed `IDMan.exe` and queues downloads cleanly.
2. **IDM EF2 Import**: Generates an `.ef2` file containing direct links, destination folders, and required `referer: https://filekeeper.net/download` headers. In IDM, go to **Tasks -> Import -> From IDM export file** and select the `.ef2` file.
3. **Background Monitor & Auto-Extract**: Keeps running in terminal while IDM downloads. Once all parts are in `D:\Downloads\<Game Title>\`, it automatically calls 7-Zip to extract `setup.exe` and cleans up the `.rar` files.
