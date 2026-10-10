import os
import sys
import time
import subprocess
import winreg

class IDMExporter:
    def __init__(self, idm_path=None):
        self.idm_path = idm_path or self.find_idm()

    @staticmethod
    def find_idm():
        """
        Locates IDMan.exe path on the Windows system.
        """
        common_paths = [
            r"C:\Program Files (x86)\Internet Download Manager\IDMan.exe",
            r"C:\Program Files\Internet Download Manager\IDMan.exe",
            os.path.expandvars(r"%ProgramFiles(x86)%\Internet Download Manager\IDMan.exe"),
            os.path.expandvars(r"%ProgramFiles%\Internet Download Manager\IDMan.exe")
        ]

        for p in common_paths:
            if os.path.exists(p):
                return p

        registry_keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\DownloadManager"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Internet Download Manager"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Internet Download Manager")
        ]

        for hkey, subkey in registry_keys:
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    path, _ = winreg.QueryValueEx(key, "ExePath")
                    if path and os.path.exists(path):
                        return path
            except Exception:
                pass

        return None

    def send_to_idm(self, links, download_dir=None, start_queue=True, relay=None):
        """
        Queues every link in IDM through its command line, no import step.

        IDM's own User-Agent gets 403 from FileKeeper / dlproxy, so those links are served through
        `relay` (see idm_proxy.LocalRelay), which fetches them with Chrome headers. Other hosters go
        to IDM directly. Returns (ok, message, queued_count).
        """
        from idm_proxy import needs_relay

        if not self.idm_path or not os.path.exists(self.idm_path):
            return False, "IDMan.exe not found on this system.", 0
        if download_dir:
            os.makedirs(download_dir, exist_ok=True)

        creationflags = 0x00000008 if sys.platform == "win32" else 0   # DETACHED_PROCESS
        queued = relayed = 0
        for i, item in enumerate(links):
            url = item.get('direct') or item.get('original')
            if relay is not None and needs_relay(item):
                url = relay.url_for(item)
                relayed += 1
            cmd = [self.idm_path, "/n", "/d", url, "/a"]      # /n silent, /a add to queue only
            if download_dir:
                cmd.extend(["/p", download_dir])
            filename = item.get('filename')
            if filename and filename != 'paste_link':
                cmd.extend(["/f", filename])
            try:
                subprocess.Popen(cmd, creationflags=creationflags)
                queued += 1
            except OSError:
                continue
            time.sleep(1.5 if i == 0 else 0.08)    # first call may have to start IDM itself

        if queued and start_queue:
            time.sleep(0.5)
            try:
                subprocess.Popen([self.idm_path, "/s"], creationflags=creationflags)
            except OSError:
                pass

        if not queued:
            return False, "IDM did not accept any links.", 0
        msg = f"Queued {queued} parts in IDM."
        if relayed:
            msg += f" {relayed} are relayed through this app, so keep it open until they finish."
        return True, msg, queued

    @staticmethod
    def export_ef2(links, output_file_path, download_dir=None, referer="https://fitgirl-repacks.site/"):
        """
        Exports download links into IDM .ef2 batch file format.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_file_path)), exist_ok=True)
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        
        lines = []
        for item in links:
            url = item.get('direct') or item.get('original')
            filename = item.get('filename', '')

            lines.append("<")
            lines.append(url)
            if 'dlproxy' in url or 'filekeeper' in url:
                lines.append("referer: https://filekeeper.net/download")
            elif referer:
                lines.append(f"referer: {referer}")
            lines.append(f"User-Agent: {user_agent}")
            if download_dir:
                lines.append(f"path: {download_dir}")
            if filename and filename != 'paste_link':
                lines.append(f"file name: {filename}")
            lines.append(">")

        content = "\r\n".join(lines) + "\r\n"
        with open(output_file_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)

        return output_file_path

    @staticmethod
    def export_txt(links, output_file_path):
        """
        Exports a simple line-by-line TXT file of URLs for IDM import.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_file_path)), exist_ok=True)
        urls = [item.get('direct') or item.get('original') for item in links]
        content = "\r\n".join(urls) + "\r\n"
        with open(output_file_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return output_file_path

    def export_bat(self, links, output_file_path, download_dir=None):
        """
        Generates an executable .bat script that runs IDMan commands cleanly.
        """
        idm_exe = self.idm_path or r"C:\Program Files (x86)\Internet Download Manager\IDMan.exe"
        os.makedirs(os.path.dirname(os.path.abspath(output_file_path)), exist_ok=True)

        lines = ["@echo off", "echo Adding FitGirl downloads to IDM queue...", ""]
        for item in links:
            url = item.get('direct') or item.get('original')
            filename = item.get('filename', '')
            
            cmd = f'"{idm_exe}" /d "{url}"'
            if download_dir:
                cmd += f' /p "{download_dir}"'
            if filename and filename != 'paste_link':
                cmd += f' /f "{filename}"'
            cmd += ' /a'
            lines.append(cmd)

        lines.append("")
        lines.append('echo Starting IDM Queue...')
        lines.append(f'"{idm_exe}" /s')
        lines.append("echo Done!")
        lines.append("pause")

        with open(output_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return output_file_path
