import os
import sys
import time
import subprocess
import glob
import argparse
import re
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

console = Console()

class ArchiveExtractor:
    def __init__(self):
        self.tool_type, self.tool_path = self.find_extraction_tool()

    @staticmethod
    def find_extraction_tool():
        """
        Locates 7-Zip or WinRAR on the Windows system.
        Prefers 7z.exe because it provides clean CLI progress output.
        """
        seven_zip_paths = [
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe"
        ]
        for p in seven_zip_paths:
            if os.path.exists(p):
                return '7z', p

        winrar_paths = [
            r"C:\Program Files\WinRAR\WinRAR.exe",
            r"C:\Program Files\WinRAR\UnRAR.exe",
            r"C:\Program Files (x86)\WinRAR\WinRAR.exe"
        ]
        for p in winrar_paths:
            if os.path.exists(p):
                return 'winrar', p

        return None, None

    @staticmethod
    def find_all_archive_starters(folder_path):
        """
        Finds the initial part for every distinct archive set in folder.
        Handles both multi-part archives (.part001.rar, .part1.rar) and single archives (.rar, .zip, .7z).
        """
        if not os.path.exists(folder_path):
            return []

        all_archives = []
        for ext in ["*.rar", "*.zip", "*.7z"]:
            all_archives.extend(glob.glob(os.path.join(folder_path, ext)))

        # Group by archive base name (strip .partN.ext)
        groups = {}
        for fpath in all_archives:
            fname = os.path.basename(fpath)
            # Match patterns like: name.part001.rar or name.part1.rar or name.part01.rar
            m = re.match(r'^(.*?)(?:\.part(\d+))?\.(\w+)$', fname, re.IGNORECASE)
            if m:
                base = m.group(1).lower()
                part_num_str = m.group(2)
                part_int = int(part_num_str) if part_num_str else 1
                if base not in groups or part_int < groups[base][0]:
                    groups[base] = (part_int, fpath)
            else:
                groups[fname.lower()] = (1, fpath)

        starters = [item[1] for item in groups.values()]
        return sorted(starters)

    @staticmethod
    def find_first_part(folder_path):
        """
        Finds the first archive file for backwards compatibility.
        """
        starters = ArchiveExtractor.find_all_archive_starters(folder_path)
        return starters[0] if starters else None

    @staticmethod
    def get_all_rar_parts(folder_path):
        """
        Returns all .rar / .part*.rar files in the specified folder.
        """
        rar_files = []
        for ext in ["*.rar", "*.r00", "*.r01"]:
            rar_files.extend(glob.glob(os.path.join(folder_path, ext)))
        # Deduplicate and sort
        return sorted(list(set(rar_files)))

    @staticmethod
    def normalize_idm_filenames(folder_path):
        """
        Detects files that IDM renamed by appending duplicate suffixes like _2, _3
        (e.g. fg-u4.part01_2.rar), and safely normalizes them back to clean original names
        (e.g. fg-u4.part01.rar). Crucial for multi-part 7-Zip/WinRAR extraction & EF2 verification.
        """
        if not os.path.exists(folder_path):
            return 0
        renamed_count = 0
        pattern = re.compile(r'^(.*?)(?:_\d+)(\.(?:rar|bin|r\d+|7z|zip))$', re.IGNORECASE)
        try:
            for entry in os.scandir(folder_path):
                if entry.is_file():
                    m = pattern.match(entry.name)
                    if m:
                        clean_name = m.group(1) + m.group(2)
                        clean_path = os.path.join(folder_path, clean_name)
                        if not os.path.exists(clean_path):
                            try:
                                os.rename(entry.path, clean_path)
                                renamed_count += 1
                            except Exception:
                                pass
        except Exception:
            pass
        return renamed_count

    @staticmethod
    def is_file_ready(filepath):
        """
        Checks if a file has finished downloading and is not locked by IDM or another process.
        Tests readable access without requiring write/append access (which fails when IDM
        or Explorer holds shared read handles on finished downloads).
        """
        if not os.path.exists(filepath):
            # Check if an IDM duplicate-suffixed version exists
            base, ext = os.path.splitext(filepath)
            for i in range(1, 10):
                alt = f"{base}_{i}{ext}"
                if os.path.exists(alt):
                    filepath = alt
                    break
            else:
                return False

        # If there are temporary IDM download files (.tmp / .log / .temp)
        if os.path.exists(filepath + ".tmp") or os.path.exists(filepath + ".log") or os.path.exists(filepath + ".temp"):
            return False
        try:
            if os.path.getsize(filepath) == 0:
                return False
            # Verify file is readable and non-corrupt header
            with open(filepath, 'rb') as f:
                f.read(64)
            return True
        except (IOError, PermissionError):
            return False

    @staticmethod
    def delete_rar_files(folder_path):
        """
        Permanently deletes all .rar parts in the specified folder to free disk space.
        """
        rar_files = ArchiveExtractor.get_all_rar_parts(folder_path)
        if not rar_files:
            console.print("[dim]No .rar files found to delete.[/dim]")
            return 0, 0

        total_bytes = sum(os.path.getsize(f) for f in rar_files if os.path.exists(f))
        total_gb = total_bytes / (1024 ** 3)

        console.print(f"\n[bold red]Deleting {len(rar_files)} RAR archive parts permanently...[/bold red]")
        deleted_count = 0
        for f in rar_files:
            try:
                os.remove(f)
                deleted_count += 1
            except Exception as e:
                console.print(f"[yellow]Could not delete {os.path.basename(f)}: {e}[/yellow]")

        console.print(f"[bold green]✓ Successfully deleted {deleted_count}/{len(rar_files)} RAR parts![/bold green]")
        console.print(f"[bold green]✓ Freed {total_gb:.2f} GB of disk space.[/bold green]\n")
        return deleted_count, total_gb

    def wait_for_downloads_and_extract(self, expected_files_or_count, download_dir, extract_to=None, delete_rars_after=False):
        """
        Monitors download directory until all expected files are completed,
        then automatically extracts the multi-part archive and optionally cleans up RARs.
        """
        if not self.tool_path:
            console.print("[yellow]⚠ Neither 7-Zip nor WinRAR found. Automatic extraction skipped.[/yellow]")
            return False

        if extract_to is None:
            extract_to = download_dir

        is_list = isinstance(expected_files_or_count, list)
        total_files = len(expected_files_or_count) if is_list else int(expected_files_or_count)

        console.print(f"\n[bold cyan]Monitoring IDM Downloads...[/bold cyan]")
        console.print(f"[dim]Waiting for all {total_files} parts to finish downloading in:[/dim]")
        console.print(f"[bold yellow]{download_dir}[/bold yellow]\n")

        start_time = time.time()
        initial_completed = None
        prev_bytes = 0
        prev_time = start_time
        speed_history = []

        with Progress(
            TextColumn("[bold green]Downloaded Parts:"),
            BarColumn(),
            "[progress.percentage]{task.completed}/{task.total} ({task.percentage:>3.1f}%)",
            "• Speed: [cyan]{task.fields[speed]}[/cyan]",
            "• ETA: [bold yellow]{task.fields[eta]}[/bold yellow]",
            "• Elapsed:",
            TimeElapsedColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Waiting for IDM completion", total=total_files, speed="-- MB/s", eta="--:--")

            while not progress.finished:
                completed = 0
                current_bytes = 0
                if is_list:
                    for item in expected_files_or_count:
                        if isinstance(item, dict):
                            fn = item.get('filename')
                        else:
                            fn = str(item)
                        fp = os.path.join(download_dir, fn)
                        if os.path.exists(fp):
                            try:
                                current_bytes += os.path.getsize(fp)
                            except Exception:
                                pass
                            if self.is_file_ready(fp):
                                completed += 1
                else:
                    rar_files = self.get_all_rar_parts(download_dir)
                    for fp in rar_files:
                        try:
                            current_bytes += os.path.getsize(fp)
                        except Exception:
                            pass
                        if self.is_file_ready(fp):
                            completed += 1

                now = time.time()
                if initial_completed is None:
                    initial_completed = completed
                    prev_bytes = current_bytes

                dt = now - prev_time
                if dt >= 1.5:
                    delta_b = max(0, current_bytes - prev_bytes)
                    inst_speed = delta_b / dt
                    if inst_speed > 0 or len(speed_history) > 0:
                        speed_history.append(inst_speed)
                        if len(speed_history) > 10:
                            speed_history.pop(0)
                    prev_bytes = current_bytes
                    prev_time = now

                avg_speed = (sum(speed_history) / len(speed_history)) if speed_history else 0
                if avg_speed >= 1024 ** 2:
                    speed_str = f"{avg_speed / (1024 ** 2):.1f} MB/s"
                elif avg_speed >= 1024:
                    speed_str = f"{avg_speed / 1024:.0f} KB/s"
                else:
                    speed_str = "-- MB/s"

                elapsed = now - start_time
                remaining_parts = max(0, total_files - completed)
                eta_str = "--:--"
                if avg_speed > 1024 * 100:
                    est_part_bytes = (current_bytes / completed) if completed > 0 else (500 * 1024 * 1024)
                    rem_bytes = remaining_parts * est_part_bytes
                    eta_sec = rem_bytes / avg_speed
                    m, s = divmod(int(eta_sec), 60)
                    h, m = divmod(m, 60)
                    eta_str = f"{h}h {m:02d}m {s:02d}s" if h > 0 else f"{m:02d}m {s:02d}s"
                elif completed > initial_completed and elapsed > 8:
                    parts_per_sec = (completed - initial_completed) / elapsed
                    if parts_per_sec > 0:
                        eta_sec = remaining_parts / parts_per_sec
                        m, s = divmod(int(eta_sec), 60)
                        h, m = divmod(m, 60)
                        eta_str = f"{h}h {m:02d}m {s:02d}s" if h > 0 else f"{m:02d}m {s:02d}s"

                progress.update(task, completed=completed, speed=speed_str, eta=eta_str)

                if completed >= total_files:
                    break

                time.sleep(2)

        console.print(f"\n[bold green]✓ All {total_files} parts downloaded successfully![/bold green]\n")

        # Trigger extraction
        return self.extract_archive(download_dir, extract_to=extract_to, delete_rars_after=delete_rars_after)

    def extract_archive(self, download_dir, extract_to=None, delete_rars_after=False, progress_callback=None):
        """
        Extracts all multi-part archive sets found in the folder (main game + optional packs),
        and only permanently deletes .rar parts after ALL sets have succeeded.
        """
        if not self.tool_path:
            console.print("[yellow]⚠ Neither 7-Zip nor WinRAR found on your PC.[/yellow]")
            return False

        if extract_to is None:
            extract_to = download_dir

        starters = self.find_all_archive_starters(download_dir)
        if not starters:
            console.print(f"[red]X No archive files found in:[/red] {download_dir}")
            return False

        os.makedirs(extract_to, exist_ok=True)
        console.print(f"[bold cyan]Found {len(starters)} Archive Set(s) to extract in:[/bold cyan] [bold yellow]{extract_to}[/bold yellow]\n")

        all_succeeded = True
        extra_flags = {}
        if sys.platform == "win32":
            extra_flags["creationflags"] = subprocess.CREATE_NO_WINDOW

        for idx, starter in enumerate(starters, 1):
            console.print(f"[{idx}/{len(starters)}] [bold cyan]Extracting Archive:[/bold cyan] [bold white]{os.path.basename(starter)}[/bold white]")

            if self.tool_type == '7z':
                cmd = [self.tool_path, "x", starter, f"-o{extract_to}", "-y", "-bsp1"]
            else:
                cmd = [self.tool_path, "x", "-o+", starter, extract_to + "\\"]

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    **extra_flags
                )
                buffer = ""
                while True:
                    char = process.stdout.read(1)
                    if not char and process.poll() is not None:
                        break
                    if char in ('\r', '\n'):
                        line_str = buffer.strip()
                        buffer = ""
                        if line_str and ('%' in line_str or 'Extracting' in line_str or 'Everything is Ok' in line_str):
                            print(f"\r{line_str[:80]}", end='', flush=True)
                            if progress_callback:
                                try:
                                    progress_callback(f"[{idx}/{len(starters)}] {line_str}")
                                except Exception:
                                    pass
                    else:
                        buffer += char

                process.wait()
                print()
                if process.returncode != 0:
                    console.print(f"\n[red]X Extraction of {os.path.basename(starter)} failed with code {process.returncode}[/red]")
                    all_succeeded = False
                    break
            except Exception as e:
                console.print(f"[red]X Error extracting {os.path.basename(starter)}: {e}[/red]")
                all_succeeded = False
                break

        if all_succeeded:
            console.print(f"\n[bold green]✓ All {len(starters)} archive sets extracted successfully![/bold green]")
            console.print(f"[bold white]Extracted Game Setup Files ready in:[/bold white] [bold cyan]{extract_to}[/bold cyan]\n")

            if delete_rars_after:
                self.delete_rar_files(download_dir)
            return True
        else:
            console.print(f"\n[bold red]Extraction incomplete. Keeping all archive files for safety.[/bold red]")
            return False

def find_recent_repack_folders(base_dir=r"D:\Downloads"):
    """
    Scans base_dir for folders containing .ef2 manifests or .rar files.
    """
    if not os.path.exists(base_dir):
        return []
    candidates = []
    try:
        for entry in os.scandir(base_dir):
            if entry.is_dir():
                ef2s = glob.glob(os.path.join(entry.path, "*.ef2"))
                rars = glob.glob(os.path.join(entry.path, "*.rar"))
                if ef2s or rars:
                    candidates.append((entry.path, len(rars), entry.stat().st_mtime))
    except Exception:
        pass
    # Sort by most recently modified
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates

def interactive_extractor(preset_action=None):
    console.print(Panel.fit(
        "[bold cyan]FitGirl Archive Extractor & RAR Cleanup[/bold cyan]\n"
        "[dim]Auto-extract multi-part RARs & permanently delete archives to save disk space[/dim]",
        border_style="cyan"
    ))

    extractor = ArchiveExtractor()
    if not extractor.tool_path:
        console.print("[bold red]Neither 7-Zip nor WinRAR found. Please install 7-Zip to extract RARs.[/bold red]")
        sys.exit(1)
    else:
        console.print(f"[green]✓ Found {extractor.tool_type.upper()} at:[/green] {extractor.tool_path}\n")

    recent_folders = find_recent_repack_folders()
    selected_dir = None

    if recent_folders:
        console.print("[bold white]Found download folders with RAR files:[/bold white]")
        for idx, (fpath, count, _) in enumerate(recent_folders[:5], 1):
            console.print(f" [{idx}] [bold yellow]{os.path.basename(fpath)}[/bold yellow] [dim]({count} RAR parts)[/dim]")
        console.print(f" [{len(recent_folders[:5]) + 1}] Enter custom path manually")

        choice = Prompt.ask("\nSelect folder number", choices=[str(i) for i in range(1, len(recent_folders[:5]) + 2)], default="1")
        c_idx = int(choice) - 1
        if c_idx < len(recent_folders[:5]):
            selected_dir = recent_folders[c_idx][0]

    if not selected_dir:
        selected_dir = Prompt.ask("[bold yellow]Enter full path of folder containing RAR parts[/bold yellow]")

    if not os.path.exists(selected_dir):
        console.print(f"[red]Directory not found: {selected_dir}[/red]")
        sys.exit(1)

    rars = extractor.get_all_rar_parts(selected_dir)
    console.print(f"\n[green]Selected folder:[/green] [bold cyan]{selected_dir}[/bold cyan]")
    console.print(f"[dim]Found {len(rars)} RAR parts currently.[/dim]\n")

    if preset_action:
        action = preset_action
    else:
        console.print("[bold white]Choose Action:[/bold white]")
        console.print(" [1] [bold green]Extract RARs into folder and DELETE RARs permanently[/bold green] (Recommended cleanup)")
        console.print(" [2] [bold cyan]Wait for IDM to finish downloading all parts[/bold cyan], then extract & delete RARs")
        console.print(" [3] [bold yellow]Extract RARs only[/bold yellow] (Keep original RARs)")
        console.print(" [4] [bold red]Delete RAR parts only[/bold red] (Cleanup without extracting)")
        action = Prompt.ask("\nSelect action", choices=["1", "2", "3", "4"], default="1")

    if action == "1":
        extractor.extract_archive(selected_dir, extract_to=selected_dir, delete_rars_after=True)
    elif action == "2":
        total_str = Prompt.ask("[bold yellow]Enter total number of parts expected (e.g. 186)[/bold yellow]", default=str(len(rars) if len(rars) > 100 else 186))
        extractor.wait_for_downloads_and_extract(int(total_str), selected_dir, extract_to=selected_dir, delete_rars_after=True)
    elif action == "3":
        extractor.extract_archive(selected_dir, extract_to=selected_dir, delete_rars_after=False)
    elif action == "4":
        confirm = Confirm.ask(f"[bold red]Are you sure you want to PERMANENTLY delete all {len(rars)} RAR parts in this folder?[/bold red]", default=False)
        if confirm:
            extractor.delete_rar_files(selected_dir)

def main():
    parser = argparse.ArgumentParser(description="Multi-part RAR extractor and automatic cleanup tool.")
    parser.add_argument("folder", nargs="?", help="Folder containing .rar parts")
    parser.add_argument("--watch", action="store_true", help="Wait for IDM to finish all parts before extracting")
    parser.add_argument("--total", type=int, help="Total number of parts expected when using --watch")
    parser.add_argument("--extract-and-delete", action="store_true", help="Extract into folder and permanently delete RAR parts")
    parser.add_argument("--delete-rars", action="store_true", help="Delete RAR parts after extraction")

    args = parser.parse_args()

    extractor = ArchiveExtractor()

    # If no folder specified, run interactive mode with preset if applicable
    if not args.folder:
        if args.extract_and_delete:
            interactive_extractor(preset_action="1")
        elif args.watch:
            interactive_extractor(preset_action="2")
        else:
            interactive_extractor()
        return

    target_dir = args.folder
    if args.watch:
        total = args.total or len(extractor.get_all_rar_parts(target_dir)) or 186
        delete_after = args.delete_rars or args.extract_and_delete
        extractor.wait_for_downloads_and_extract(total, target_dir, extract_to=target_dir, delete_rars_after=delete_after)
    else:
        delete_after = args.delete_rars or args.extract_and_delete
        extractor.extract_archive(target_dir, extract_to=target_dir, delete_rars_after=delete_after)

if __name__ == "__main__":
    main()
