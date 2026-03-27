import time
import sys
import os
from openai import OpenAI
import subprocess

ful = "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰"
emp = "▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱"
total_steps = len(emp)

def progress_bar(n):
    for i in range(total_steps + 1):
        barre = ful[:i] + emp[i:]
        percent = int((i / total_steps) * 100)
        print(f"\r[{barre}] {percent}%", end="", flush=True)
        time.sleep(n)

    print("\n\n[DONE]")


print("Loading...")
#if os.system("ping -n 1 8.8.8.8 >nul") != 0: sys.exit("No internet")
progress_bar(0.1)  
if os.name == "nt":
    import msvcrt

    def getch():
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return ch + ch2
        return ch

else:
    import tty, termios

    def getch():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                ch += sys.stdin.read(2)
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


UP    = {"\xe0H", "\x00H", "\x1b[A"}
DOWN  = {"\xe0P", "\x00P", "\x1b[B"}
RIGHT = {"\xe0M", "\x00M", "\x1b[C"}
LEFT  = {"\xe0K", "\x00K", "\x1b[D"}
ENTER = {"\r", "\n"}
BACK  = {"\x08", "\x7f"}


RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
BLUE   = "\033[94m"


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def pick_folder(start=None):
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    current = os.path.abspath(start or (desktop if os.path.isdir(desktop) else os.getcwd()))
    cursor  = 0

    while True:
        try:
            raw = sorted(os.scandir(current), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            raw = []

        entries = []
        parent = os.path.dirname(current)
        if parent != current:
            entries.append(("..", True))
        for e in raw:
            entries.append((e.name, e.is_dir()))

        cursor = max(0, min(cursor, len(entries) - 1))

        clear()
        print(f"{BOLD}{CYAN}  [ Folder Picker ]{RESET}")
        print(f"{DIM}  {current}{RESET}")
        print(f"{DIM}  ↑↓ move   →/Enter open   ←/Backspace go up   S select   Esc/Q quit{RESET}")
        print()

        VIEW = 22
        top  = max(0, min(cursor - VIEW // 2, max(0, len(entries) - VIEW)))

        for i, (name, is_dir) in enumerate(entries[top:top + VIEW], top):
            sel = i == cursor
            if name == "..":
                color, label = YELLOW, "..   [go up]"
                icon = "  "
            elif is_dir:
                color, label = BLUE, f"[{name}]"
                icon = "▸ " if sel else "  "
            else:
                color, label = DIM, name
                icon = "  "

            row = f"  {icon}{color}{label}{RESET}"
            if sel:
                row = f"{BOLD}{GREEN}>{row[1:]}"
            print(row)

        if len(entries) > VIEW:
            print(f"\n{DIM}  … {len(entries)} items ({VIEW} shown){RESET}")

        if entries:
            _name, _is_dir = entries[cursor]
            s_target = os.path.join(current, _name) if (_name != ".." and _is_dir) else current
        else:
            s_target = current
        print(f"\n{BOLD}  [S]{RESET} select  {BOLD}{GREEN}{s_target}{RESET}\n")

        key = getch()

        if key in UP:
            cursor = (cursor - 1) % max(1, len(entries))

        elif key in DOWN:
            cursor = (cursor + 1) % max(1, len(entries))

        elif key in RIGHT | ENTER:
            if entries:
                name, is_dir = entries[cursor]
                if name == "..":
                    current, cursor = parent, 0
                elif is_dir:
                    current, cursor = os.path.join(current, name), 0

        elif key in LEFT | BACK:
            if parent != current:
                current, cursor = parent, 0

        elif key.lower() == "s":
            return s_target

        elif key in {"\x1b", "q", "Q"}:
            return None



os.system("color 0a")
selected = pick_folder()
clear()
if selected is None:
    print("Cancelled.")
    exit()



while 1:


    os.system("color 0a")
    query = input(f"{selected}~$query>>")
    if query == "exit":exit()
    if query == "cd":
        selected = pick_folder()
        clear()
        if selected is None:
            print("Cancelled.")
            exit() 
        os.system("color 0a")
        query = input(f"{selected}~$query>>")
        if query == "exit":exit()

    files,sub = [],[]
    files = os.listdir(selected)
    for i in range(len(files)):
            if os.path.isdir(os.path.join(selected, files[i])):
                full_path = os.path.join(selected, files[i])
                sub = os.listdir(full_path)
                files[i] = files[i]+":"+",".join(sub)

    client = OpenAI(
        base_url="https://router.huggingface.co/v1",
        api_key="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",
        )

    completion = client.chat.completions.create(
        model="moonshotai/Kimi-K2-Instruct-0905",
        messages=[
                {
                    "role": "user",
                    "content": f"here are my files in the current folder : {files} .{query},give me the command for cmd only separated by $$ no additional text"
                    }
                ],
        )


    command = "\n".join((completion.choices[0].message.content).split("$$"))
    print(command)
    command = "@echo off\n"+ command + "\n"+"del query.bat"
    ans = input("Proceed? (Y/N)")
    if ans in ["Y","y","yes","Yes"]:
        with open(os.path.join(selected, "query.bat"), "w") as f:
            f.write(command)
        progress_bar(0.02)
        subprocess.run(os.path.join(selected, "query.bat"), shell=True, cwd=selected)
        time.sleep(0.3)
