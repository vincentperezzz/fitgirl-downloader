import os
import sys
import argparse
import requests
import time

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn, TextColumn

from fitgirl_parser import FitGirlParser
from idm_exporter import IDMExporter

console = Console()

def print_banner():
    console.print(Panel.fit(
        "[bold cyan]FitGirl to IDM Downloader[/bold cyan]\n"
        "[dim]Fast, Automated Link Extractor & Batch Download Manager for IDM[/dim]",
        border_style="cyan"
    ))

def download_files_directly(links, target_dir):
    """
    Direct built-in multi-part downloader using requests with rich progress bar.
    Bypasses IDM concurrency rate-limits completely.
    """
    os.makedirs(target_dir, exist_ok=True)
    console.print(f"\n[bold green]Starting Direct Download to:[/bold green] [bold cyan]{target_dir}[/bold cyan]")
    console.print(f"[dim]Total parts to download: {len(links)}[/dim]\n")

    with Progress(
        TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        DownloadColumn(),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
        console=console
    ) as progress:
        overall_task = progress.add_task("[yellow]Overall Progress", total=len(links), filename="Overall Progress")

        for idx, item in enumerate(links, 1):
            url = item.get('direct') or item.get('original')
            filename = item.get('filename') or f"part{idx:03d}.rar"
            file_path = os.path.join(target_dir, filename)

            # Skip if already fully downloaded
            if os.path.exists(file_path):
                existing_size = os.path.getsize(file_path)
                if existing_size >= 400 * 1024 * 1024:  # roughly 400MB+
                    console.print(f"[green]✓ Part {idx}/{len(links)} already exists ({existing_size/(1024*1024):.1f} MB), skipping: {filename}[/green]")
                    progress.update(overall_task, advance=1)
                    continue

            task_id = progress.add_task(f"Downloading", total=None, filename=filename)
            referer_header = 'https://filekeeper.net/download' if ('dlproxy' in url or 'filekeeper' in url) else 'https://fitgirl-repacks.site/'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': referer_header
            }

            try:
                with requests.get(url, headers=headers, stream=True, timeout=20) as r:
                    r.raise_for_status()
                    total_length = r.headers.get('content-length')
                    if total_length:
                        progress.update(task_id, total=int(total_length))

                    with open(file_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024 * 256):
                            if chunk:
                                f.write(chunk)
                                progress.update(task_id, advance=len(chunk))
            except Exception as e:
                console.print(f"[red]X Error downloading {filename}: {e}[/red]")
            finally:
                progress.remove_task(task_id)
                progress.update(overall_task, advance=1)

    console.print("\n[bold green]✓ All parts downloaded successfully![/bold green]")

def main():
    parser = argparse.ArgumentParser(description="Extract FitGirl Repack download links directly into Internet Download Manager (IDM).")
    parser.add_argument("url", nargs="?", help="FitGirl page URL or paste URL")
    parser.add_argument("--host", help="Preferred hoster (e.g., FileKeeper, DataNodes, Pixeldrain)")
    parser.add_argument("--dir", help="Output download directory for IDM")
    parser.add_argument("--export", choices=["idm", "ef2", "txt", "bat", "direct"], help="Export format or direct download")
    parser.add_argument("--auto-queue", action="store_true", help="Automatically send all links to IDM queue")
    parser.add_argument("--start-queue", action="store_true", help="Start IDM queue download after adding")

    args = parser.parse_args()

    print_banner()

    idm_exporter = IDMExporter()
    if idm_exporter.idm_path:
        console.print(f"[bold green][+] Found IDM at:[/bold green] {idm_exporter.idm_path}\n")
    else:
        console.print("[yellow][!] IDMan.exe not automatically detected in standard path. You can still export .ef2 / .txt / .bat files![/yellow]\n")

    fitgirl_url = args.url
    if not fitgirl_url:
        fitgirl_url = Prompt.ask("[bold yellow]Enter FitGirl Game Page URL or Paste URL[/bold yellow]")

    if not fitgirl_url.strip():
        console.print("[red]No URL provided. Exiting.[/red]")
        sys.exit(1)

    with console.status("[bold green]Scraping & Resolving FitGirl links (bypassing countdowns)...[/bold green]"):
        parser_engine = FitGirlParser()
        result = parser_engine.parse_url(fitgirl_url)

    if not result['success']:
        console.print(f"[red]X Error:[/red] {result['error']}")
        sys.exit(1)

    console.print(f"\n[bold green]Game Title:[/bold green] [bold white]{result['title']}[/bold white]")
    console.print(f"[bold blue]Source URL:[/bold blue] {result['source_url']}")
    
    categorized = result['categorized_links']
    if not categorized:
        console.print("[red]No download links found on this page.[/red]")
        sys.exit(0)

    # Display links table
    table = Table(title="Available File Hosters & Mirrors", border_style="cyan")
    table.add_column("Option", justify="center", style="cyan", no_wrap=True)
    table.add_column("Hoster / Mirror", style="bold green")
    table.add_column("Parts Count", justify="right", style="magenta")
    table.add_column("Sample Filename / Status", style="dim white")

    host_keys = list(categorized.keys())
    for idx, host in enumerate(host_keys, start=1):
        links = categorized[host]
        sample = links[0]['filename'] if links else ""
        status_tag = ""
        if 'fuckingfast' in host.lower():
            status_tag = " [⚠ Rate Limited by Host - HTTP 429]"
        elif any(h in host.lower() for h in ['filekeeper', 'datanodes', 'pixeldrain', 'mediafire']):
            status_tag = " [✓ Recommended / IDM Ready]"
        
        table.add_row(str(idx), f"{host}{status_tag}", str(len(links)), sample)

    console.print(table)

    # Select hoster
    selected_host = None
    if args.host:
        for k in host_keys:
            if args.host.lower() in k.lower():
                selected_host = k
                break
    
    if not selected_host:
        if len(host_keys) == 1:
            selected_host = host_keys[0]
        else:
            choice = Prompt.ask(
                "\n[bold yellow]Select Hoster Option Number (or 'all')[/bold yellow]",
                choices=[str(i) for i in range(1, len(host_keys) + 1)] + ['all'],
                default="1"
            )
            if choice == 'all':
                selected_host = 'all'
            else:
                selected_host = host_keys[int(choice) - 1]

    if selected_host == 'all':
        target_links = []
        for k in host_keys:
            target_links.extend(categorized[k])
    else:
        target_links = categorized[selected_host]

    console.print(f"\n[bold green]Selected {len(target_links)} links from:[/bold green] [bold yellow]{selected_host}[/bold yellow]")

    # Default download dir on D:\Downloads\<Game Title>
    base_download_dir = r"D:\Downloads"
    default_download_dir = args.dir or os.path.join(base_download_dir, result['title'])

    # Determine action
    if args.export == 'idm' or args.auto_queue:
        action = '1'
    elif args.export == 'ef2':
        action = '2'
    elif args.export == 'txt':
        action = '3'
    elif args.export == 'bat':
        action = '4'
    elif args.export == 'direct':
        action = '5'
    else:
        console.print("\n[bold white]Choose Action:[/bold white]")
        console.print(" [1] [bold green]Send directly to IDM Queue[/bold green] (Auto-queue in IDMan.exe)")
        console.print(" [2] [bold cyan]Export IDM .ef2 Batch File[/bold cyan] (File -> Import -> From IDM export file)")
        console.print(" [3] [bold yellow]Export .txt Link List[/bold yellow] (File -> Import -> From text file)")
        console.print(" [4] [bold magenta]Generate .bat Launcher Script[/bold magenta]")
        console.print(" [5] [bold green]⚡ Download Directly Now with Python[/bold green] (Full speed, bypasses IDM 403 limits)")

        action = Prompt.ask("\nSelect action number", choices=["1", "2", "3", "4", "5"], default="1")

    output_dir = os.path.join(os.getcwd(), "exports")
    os.makedirs(output_dir, exist_ok=True)

    if action == '1':
        target_path = default_download_dir
        
        if args.auto_queue:
            start_now = args.start_queue
        else:
            start_now = args.start_queue or Confirm.ask("[bold cyan]Start IDM Download Queue immediately?[/bold cyan]", default=True)

        with console.status("[bold green]Sending links to IDM...[/bold green]"):
            success, msg = idm_exporter.send_to_idm(target_links, download_dir=target_path, start_queue=start_now)

        if success:
            console.print(f"\n[bold green][+] Success![/bold green] {msg}")
            console.print(f"[dim]Files queued for IDM. Save folder: {target_path}[/dim]")
        else:
            console.print(f"\n[bold red][X] Failed to send to IDM:[/bold red] {msg}")
            console.print("[yellow]Fallback: Exporting IDM .ef2 file instead...[/yellow]")
            ef2_path = os.path.join(output_dir, f"{result['title']}.ef2")
            idm_exporter.export_ef2(target_links, ef2_path, download_dir=target_path)
            console.print(f"[bold green][+] Saved .ef2 file to:[/bold green] {ef2_path}")

    elif action == '2':
        ef2_path = os.path.join(output_dir, f"{result['title']}.ef2")
        idm_exporter.export_ef2(target_links, ef2_path, download_dir=default_download_dir)
        console.print(f"\n[bold green][+] Exported IDM .ef2 File:[/bold green] {ef2_path}")
        console.print("[dim]Open IDM -> Go to Tasks -> Import -> From IDM export file -> Select this file.[/dim]")

    elif action == '3':
        txt_path = os.path.join(output_dir, f"{result['title']}.txt")
        idm_exporter.export_txt(target_links, txt_path)
        console.print(f"\n[bold green][+] Exported TXT URL List:[/bold green] {txt_path}")
        console.print("[dim]Open IDM -> Go to Tasks -> Import -> From text file -> Select this file.[/dim]")

    elif action == '4':
        bat_path = os.path.join(output_dir, f"{result['title']}_idm.bat")
        idm_exporter.export_bat(target_links, bat_path, download_dir=default_download_dir)
        console.print(f"\n[bold green][+] Exported Batch Launcher:[/bold green] {bat_path}")
        console.print("[dim]Double-click the .bat file anytime to queue and start downloads in IDM.[/dim]")

    elif action == '5':
        download_files_directly(target_links, default_download_dir)

    # Auto-extraction monitor
    from extractor import ArchiveExtractor
    extractor = ArchiveExtractor()
    if extractor.tool_path:
        should_watch = args.auto_queue or Confirm.ask(
            f"\n[bold yellow]Wait for IDM to finish all {len(target_links)} parts and auto-extract them with {extractor.tool_type.upper()}?[/bold yellow]",
            default=True
        )
        if should_watch:
            delete_rars = Confirm.ask(
                "[bold yellow]Permanently delete all .rar files after extraction to save disk space?[/bold yellow]",
                default=True
            )
            extractor.wait_for_downloads_and_extract(
                target_links,
                default_download_dir,
                extract_to=default_download_dir,
                delete_rars_after=delete_rars
            )

    console.print("\n[bold cyan]Done! Happy gaming![/bold cyan]\n")

if __name__ == "__main__":
    main()
