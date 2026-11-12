#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Мост достижений для Hydra Launcher
==================================

В чём беда
----------
Hydra ищет файлы достижений строго по определённым адресам и всегда
в папке с номером игры (appid). Если эмулятор сохранил прогресс куда-то
в другое место — или в папку с неправильным номером (например в «0»,
когда в steam_appid.txt написана ерунда вместо числа) — Hydra этих
достижений просто не видит, и в лаунчере пусто.

Вторая беда: у некоторых сборок эмулятор вообще не работает (например,
его съел антивирус). Игра тогда копит достижения только в своём
сохранении. Такой случай мост тоже умеет вытаскивать.

Что делает этот скрипт
----------------------
1. Знает 19 эмуляторов и около семидесяти мест, куда они складывают
   прогресс, и умеет читать все известные форматы файлов (INI всех мастей,
   JSON Goldberg, бинарный stats.bin от SmartSteamEmu, реестр GreenLuma).
2. Если формат незнакомый — просто пробует все разборщики по очереди
   на любом файле, похожем на достижения. Поэтому «добавить новый
   эмулятор» обычно не требуется: он подхватится сам.
3. Если номер игры потерялся — пытается понять, чья это папка: сверяет
   названия достижений со схемами игр (steam_settings\\achievements.json
   в папке игры).
4. Если у игры свой учёт достижений (например Godot-игра пишет их в
   save.json), мост читает и его, а карту «внутреннее имя → имя Steam»
   достаёт прямо из файлов игры.
5. Складывает результат в %APPDATA%\\GSE Saves\\<номер>\\achievements.json
   в формате Goldberg — это место Hydra читает всегда, для любого крака.

Чего скрипт НЕ делает
---------------------
Ничего не удаляет и не меняет в папках игр. Только читает и дописывает
свои файлы в отдельную папку. Игру сломать нельзя.

И не выдумывает достижения. Если эмулятор ничего не записал на диск —
переносить нечего, и скрипт честно об этом скажет. Живой пример: RUNE без
схемы достижений (файл achievements.ini на месте, а внутри Count=0, так
выглядит Hollow Knight из репака). Подробнее — в README, раздел
«Чего он не умеет» / «What it can't do».

Запуск
------
    python bridge.py            обычный прогон
    python bridge.py --dry-run  посмотреть, ничего не писать
    python bridge.py --watch    повторять раз в 30 секунд
    python bridge.py --help     все ключи

Подробности — в README.md рядом с этим файлом.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

try:
    import msvcrt          # нужен, чтобы два слежения не запустились разом
except ImportError:
    msvcrt = None

try:
    import winreg          # реестр — там живут достижения GreenLuma
except ImportError:
    winreg = None

# ────────────────────────────── настройки ──────────────────────────────

HERE = Path(__file__).resolve().parent

APPDATA = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
PUBLIC_DOCS = Path(os.environ.get("PUBLIC") or r"C:\Users\Public") / "Documents"
PROGRAM_DATA = Path(os.environ.get("ProgramData") or r"C:\ProgramData")
USER_DOCS = Path.home() / "Documents"
LOCAL_LOW = Path(os.environ.get("USERPROFILE") or Path.home()) / "AppData" / "LocalLow"

HYDRA_DIR = APPDATA / "hydralauncher"
HYDRA_LOG = HYDRA_DIR / "logs" / "achievements.txt"

# Место, которое Hydra читает для любого крака — сюда и кладём результат.
BRIDGE_DIR = APPDATA / "GSE Saves"

# Папки с играми по умолчанию. Свои пути пиши в roots.txt (по одной в строке) —
# он читается вместе с этим списком, а не вместо него.
DEFAULT_ROOTS = [r"C:\Games", r"D:\Games"]

STATE_FILE = HERE / "state.json"         # помним, какие файлы создали мы сами
CACHE_FILE = HERE / "steam-cache.json"    # кэш поиска номеров игр в Steam
REPORT_FILE = HERE / "report.txt"         # отчёт последнего прогона
LOCK_FILE = HERE / "bridge.lock"          # занято, пока работает слежение
MANUAL_FILE = HERE / "games.txt"          # ручные привязки «папка = номер игры»
ROOTS_FILE = HERE / "roots.txt"
OWN_MAP_FILE = HERE / "local-saves.json"  # ручные карты для игр со своим учётом
OWN_CACHE_FILE = HERE / "local-saves-cache.json"  # кэш разбора файлов игр

# Через сколько секунд кэш обхода (см. PathCache) выбрасывается и обход
# делается начисто. Страховка: даже если время изменения папки где-то
# соврёт, раз в две минуты всё равно находится всё.
FULL_EVERY_DEFAULT = 120

MAX_DEPTH = 5            # на сколько папок вглубь роемся в поисках игр
MAX_SNIFF_SIZE = 8 << 20   # файлы больше 8 МБ на разбор не берём
MAX_MAP_FILE_SIZE = 400 << 20  # но файлы игр для карты достижений — до 400 МБ

SKIP_DIRS = {
    "$recycle.bin", "system volume information", "windowsapps", "winsxs",
    "node_modules", "__pycache__", ".git", ".svn", "temp", "tmp",
    "_redist", "redist", "directx", "dotnet", "vcredist",
}

# Как эмуляторы называют свои конфиги (в них бывает номер игры)
EMU_INI_NAMES = {
    "steam_emu.ini", "sovereign.ini", "coldclientloader.ini",
    "smartsteamemu.ini", "codex.ini", "ali213.ini", "rld!.ini",
    "onlinefix.ini", "cream_api.ini", "steam_api.ini", "goldberg.ini",
    "steamconfig.ini", "valve.ini", "hlm.ini", "ds.ini", "gse.ini",
    "steam_settings.ini", "steamclient.ini", "tenoke.ini", "kaos.ini",
}

# Внутренности игровых движков: в этих папках steam_api.dll просто лежит
# копией, игрой они не являются
ENGINE_DIR_NAMES = {"x86_64", "x64", "x86", "win64", "win32", "plugins", "binaries"}

# Файлы, в которых эмуляторы держат достижения (список из Achievement Watcher,
# исходников Hydra и наблюдений на живых папках).
ACH_FILE_NAMES = {
    "achievements.json", "achievements.ini", "achiev.ini", "achieve.dat",
    "achievements.bin", "achievements.dat", "achievements.txt",
    "stats.ini", "stats.bin", "userstats.ini", "user_stats.ini",
    "user_stats.dat", "userstats", "usergamestats.bin", "achievement",
    "achievement.dat", "achievements", "user_achievements.json",
    "steam_achievements.json", "achievements.cfg", "progress.json",
}

# А это — «на глазок»: имя файла, который может быть про достижения
ACH_FILE_RE = re.compile(
    r"(?i)(achiev|^ach[_.]|_ach_|userstat|user_stat|steam_achievements|"
    r"creamapi\.achievements|progress\.(json|dat|ini|sav)$)"
)

# ────────────────────────── мелкие помощники ──────────────────────────


def read_text(path: Path) -> str:
    """Читает текстовый файл, не падая на странных символах и кодировках.

    Эмуляторы пишут кто в UTF-8, кто в cp1251, а кто и в UTF-16 — поэтому
    сначала смотрим на метку в начале файла, а потом пробуем по очереди.
    """
    raw = read_bytes(path)
    if not raw:
        return ""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1251")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def write_text_atomic(path: Path, text: str) -> None:
    """Пишет текстовый файл через временный и с резервной копией.

    Два смысла. Первый: файл появляется на диске целиком, одним движением
    (os.replace), а не по кусочкам — Hydra в этот момент не увидит
    половинку. Второй: прежнее содержимое остаётся рядом как «.bak»,
    так что вернуть всё назад можно руками, без нас.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
        except OSError:
            pass                      # копия — не повод не записать главное
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_bytes(path: Path, limit: int = MAX_SNIFF_SIZE) -> bytes:
    """Читает файл целиком, но не больше указанного размера."""
    try:
        with open(path, "rb") as handle:
            return handle.read(limit)
    except OSError:
        return b""


def read_json(path: Path):
    """Читает json. Если файла нет или он битый — вернёт None."""
    try:
        return json.loads(read_text(path))
    except (ValueError, OSError):
        return None


def to_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# В сборках в steam_appid.txt часто стоит не настоящий номер, а 480 —
# это Spacewar, тестовое приложение самой Valve. С таким номером эмулятор
# сваливает достижения в папку, о которой Hydra знать не знает.
DUMMY_APPIDS = {"480"}


def is_appid(name) -> bool:
    """Похоже ли это на номер игры в Steam: только цифры и это не «0»."""
    text = str(name)
    return (bool(re.fullmatch(r"\d{3,10}", text))
            and text != "0" and text not in DUMMY_APPIDS)


def clean_title(text) -> str:
    """Название без меток репака: «Игра - RePack.net» → «Игра»."""
    name = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", str(text))
    name = re.split(r"\s+[-–—]\s+", name)[0]
    return name.strip(" ._-–—")


def norm(text) -> str:
    """Приводит строку к виду для сравнения: без пробелов, знаков и регистра."""
    return re.sub(r"[^0-9a-zа-яё]+", "", str(text).lower())


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 достижение», «2 достижения», «5 достижений»."""
    tail2, tail1 = count % 100, count % 10
    if 11 <= tail2 <= 14:
        word = many
    elif tail1 == 1:
        word = one
    elif 2 <= tail1 <= 4:
        word = few
    else:
        word = many
    return f"{count} {word}"


def parse_ini(text: str) -> dict[str, dict[str, str]]:
    """Разбирает ini-файл в словарь: раздел → {ключ: значение}."""
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            # в конфигах любят писать «значение   # пояснение» — пояснение отрезаем
            value = value.split("#")[0].strip()
            sections.setdefault(current, {})[key.strip()] = value
    return sections


# ─────────────────── кэш обхода: что лежит в папках ───────────────────
#
# Один прогон проверяет одни и те же пути тысячи раз: только в поиске
# сохранений игры набирается под три тысячи проверок, и каждая — обращение
# к диску. Диск от этого просыпается и шумит, поэтому запоминаем:
#
#   * что лежит в папке — по времени её изменения. Появился или исчез
#     файл — время поменялось, и запись сама устарела;
#   * что внутри файла — по его размеру и времени. Правка файла папку
#     не трогает, поэтому содержимое проверяем отдельно.
#
# Кэш живёт в памяти процесса, на диск не пишется: устареть он не может
# по построению, а переживать перезапуск ему незачем. Слежение держит его
# между прогонами и время от времени (FULL_EVERY_DEFAULT) выбрасывает.


class PathCache:
    """Помнит содержимое папок и файлов, чтобы не бегать по диску лишний раз."""

    def __init__(self):
        self.dirs: dict[str, list] = {}    # папка → [время, подпапки, ярлыки, файлы, таблица имён]
        self.files: dict[str, list] = {}   # файл → [отпечаток, формат, достижения, пояснение]
        self.verdicts: dict = {}           # набор dll → вердикт «эмулятор работает или нет»
        self.checked: set[str] = set()     # папки, которые в этом прогоне уже проверяли
        self.reads = 0                     # сколько раз реально читали список файлов

    def begin_pass(self) -> None:
        """Новый прогон: папки проверяем заново, но каждую — по одному разу.

        Внутри прогона время папки не переспрашиваем: прогон и так снимает
        срез, а переспрашивать — это те же тысячи обращений к диску.
        """
        self.checked.clear()

    def list_dir(self, path: Path):
        """Что лежит в папке: [время, подпапки, ярлыки, файлы, таблица имён].

        None — если это не папка или в неё не пускают. Порядок имён — тот же,
        что выдаёт диск: на него опирается обход, чтобы не менять отчёт.
        """
        key = os.path.normcase(str(path))
        if key in self.checked:
            return self.dirs.get(key)
        self.checked.add(key)

        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            self.dirs.pop(key, None)
            return None

        remembered = self.dirs.get(key)
        if remembered is not None and remembered[0] == mtime:
            return remembered

        dirs: list[str] = []
        links: list[str] = []
        files: list[str] = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs.append(entry.name)
                        elif entry.is_dir():
                            links.append(entry.name)   # ярлык или junction на папку
                        else:
                            files.append(entry.name)
                    except OSError:
                        continue
        except OSError:
            self.dirs.pop(key, None)
            return None

        self.reads += 1
        record = [mtime, dirs, links, files, None]
        self.dirs[key] = record
        return record

    @staticmethod
    def names(record: list) -> dict:
        """Таблица «имя в нижнем регистре → что это» для одной папки.

        Собирается один раз на прочитанный список: иначе на каждый вопрос
        «а это папка?» пришлось бы перебирать все имена подряд.
        """
        table = record[4]
        if table is None:
            table = {}
            for names, what in ((record[1], "папка"), (record[2], "ярлык"),
                                (record[3], "файл")):
                for name in names:
                    table.setdefault(os.path.normcase(name), what)
            record[4] = table
        return table

    def kind(self, path: Path) -> str:
        """Что это: «папка», «ярлык», «файл» или «нет» (пустая строка).

        Спрашиваем не диск, а список соседей: он всё равно уже прочитан.
        """
        parent = path.parent
        if parent == path:                  # корень диска: соседей у него нет
            try:
                return "папка" if path.is_dir() else ("файл" if path.exists() else "")
            except OSError:
                return ""
        record = self.list_dir(parent)
        if record is None:
            return ""
        return self.names(record).get(os.path.normcase(path.name), "")

    def is_dir(self, path: Path) -> bool:
        return self.kind(path) in ("папка", "ярлык")   # ярлык — тоже папка, как и раньше

    def is_file(self, path: Path) -> bool:
        return self.kind(path) == "файл"

    def exists(self, path: Path) -> bool:
        return self.kind(path) != ""

    def fingerprint(self, path: Path):
        """Размер и время файла — по ним видно, менялся ли он."""
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns

    def forget(self) -> None:
        """Забыть всё: следующий обход пойдёт начисто."""
        self.dirs.clear()
        self.files.clear()
        self.verdicts.clear()
        self.checked.clear()


# Текущий кэш. Прогон подменяет его своим (см. use_path_cache), а если
# ничего не подменено — работает пустой, и всё читается с диска как раньше.
CACHE = PathCache()


def use_path_cache(cache: PathCache) -> PathCache:
    """Сделать кэш текущим и вернуть прежний (чтобы потом вернуть обратно)."""
    global CACHE
    previous, CACHE = CACHE, cache
    return previous


def iter_dirs(root: Path, max_depth: int = MAX_DEPTH):
    """Обходит папку вглубь, пропуская мусорные каталоги.

    Порядок тот же, что у os.walk: папка, потом её подпапки по очереди.
    """
    if not CACHE.is_dir(root):
        return
    base_depth = len(root.parts)
    stack = [root]
    while stack:
        here = stack.pop()
        record = CACHE.list_dir(here)
        if record is None:
            continue
        files = record[3]
        depth = len(here.parts) - base_depth
        if depth >= max_depth:
            dirs = []
        else:
            dirs = [d for d in record[1]
                    if d.lower() not in SKIP_DIRS and not d.startswith("$")]
        yield here, dirs, files
        for name in reversed(dirs):
            stack.append(here / name)


def find_files(root: Path, names: set[str], max_depth: int = MAX_DEPTH):
    """Находит файлы с нужными именами где угодно внутри папки."""
    wanted = {n.lower() for n in names}
    for here, _dirnames, filenames in iter_dirs(root, max_depth):
        for filename in filenames:
            if filename.lower() in wanted:
                yield here / filename


# ───────────────────────── разбор форматов ────────────────────────────
#
# Все разборщики возвращают одно и то же: словарь
#     { «техническое имя достижения»: время открытия в секундах }
# Неоткрытые достижения в словарь не попадают вообще.
# Время может быть 0, если эмулятор его не записал.

Achievements = dict[str, int]

TRUEISH = {"1", "true", "yes", "on", "да", "unlocked", "earned", "completed"}
FALSEISH = {"0", "false", "no", "off", "нет", ""}

# ключи, которыми эмуляторы помечают «открыто»
ACHIEVED_KEYS = {
    "achieved", "haveachieved", "havehaveachieved", "unlocked", "earned",
    "completed", "state", "isachieved", "unlock", "achievedflag",
}
# ключи, в которых лежит время открытия
TIME_KEYS = {
    "unlocktime", "haveachievedtime", "havehaveachievedtime", "timeunlocked",
    "unlocktimeunix", "time", "earnedtime", "unlocktimestamp", "timestamp",
    "datetime", "when",
}

# разделы, которые достижениями не являются
NOT_ACHIEVEMENTS = {
    "steamachievements", "achievements", "achievement", "steam", "game",
    "settings", "profile", "crack", "dlc", "interfaces", "interface",
    "gameresources", "achievementsunlocktimes", "stats", "userstats",
    "gameinfo", "steamemu", "general", "user", "playername", "config",
    "properties", "version", "credentials", "networking",
}

# имя достижения должно быть хоть на что-то похоже
# (нижнее подчёркивание в начале разрешено: игры часто зовут достижения
#  вроде _ACHIEVEMENT_BUTTON_1__NAME)
NAME_RE = re.compile(r"^_?[A-Za-z0-9][A-Za-z0-9_.\-]{1,63}$")

# картинки, звуки и прочая начинка игр: там достижений точно нет
MEDIA_SUFFIXES = {
    ".wav", ".ogg", ".mp3", ".flac", ".png", ".jpg", ".jpeg", ".bmp", ".gif",
    ".webp", ".dds", ".tga", ".svg", ".ico", ".ttf", ".otf", ".woff", ".woff2",
    ".mp4", ".webm", ".bk2", ".bik", ".usm", ".vid", ".wem", ".bnk", ".bank",
    ".uasset", ".uexp", ".pak", ".sig", ".exe", ".dll", ".pdb", ".res",
}


def looks_like_name(name: str) -> bool:
    """Не всякая строка — имя достижения. Отсеиваем мусор."""
    if not NAME_RE.match(name):
        return False
    if norm(name) in NOT_ACHIEVEMENTS:
        return False
    # в конфигах эмуляторов полно таких ключей
    if norm(name) in {"appid", "steamappid", "username", "language", "savepath",
                      "path", "name", "count", "steam64", "steamid"}:
        return False
    return True


def truthy(value) -> bool:
    return str(value).strip().lower() in TRUEISH


def hex_to_uint_le(value: str):
    """RLD! и 3DM пишут числа в hex, причём младшим байтом вперёд."""
    text = str(value).strip()
    if not text or not re.fullmatch(r"[0-9a-fA-F]+", text):
        return None
    if len(text) % 2:
        return None
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return None
    raw = raw + b"\x00" * (4 - len(raw))
    return struct.unpack("<I", raw[:4])[0]


def plausible_time(value) -> int:
    """Время открытия: отсеиваем «1» и прочие не-даты."""
    number = to_int(value)
    return number if 1_400_000_000 < number < 4_000_000_000 else 0


# ── JSON: Goldberg / GSE / EMPRESS / OnlineFix новых версий / ALI ──

def parse_json_achievements(data) -> Achievements:
    """Формат `{"ИМЯ": {"earned": true, "earned_time": 1234567890}}`.

    Бывает и списком: `[{"name": "...", "earned": true}]`.
    """
    found: Achievements = {}

    def take(name, item) -> None:
        if not name or not looks_like_name(str(name)):
            return
        when = 0
        if isinstance(item, dict):
            for key, value in item.items():
                if norm(key) in TIME_KEYS:
                    when = plausible_time(value)
                    break
        found[str(name)] = when

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("id") or item.get("apiname")
            if name and (truthy(item.get("earned")) or truthy(item.get("achieved"))
                         or truthy(item.get("completed")) or truthy(item.get("unlocked"))):
                take(name, item)
    elif isinstance(data, dict):
        for name, item in data.items():
            if isinstance(item, dict):
                mark = False
                for key, value in item.items():
                    if norm(key) in ACHIEVED_KEYS and truthy(value):
                        mark = True
                        break
                if mark:
                    take(name, item)
            elif truthy(item):
                take(name, item)
    return found


# ── INI во всех видах: RUNE, CODEX, ALI213, RLD!, 3DM, Hoodlum... ──

def section(sections: dict, *names: str) -> dict | None:
    """Находит раздел ini без учёта регистра."""
    wanted = {norm(n) for n in names}
    for key, values in sections.items():
        if norm(key) in wanted:
            return values
    return None


def parse_ini_achievements(text: str) -> tuple[Achievements, str]:
    """Разбирает ini-файл достижений.

    Возвращает (достижения, название формата). Пустое название формата
    означает «не понял, что это за файл». Если формат понятен, но ничего
    не открыто — вернётся пустой словарь и название формата.
    """
    sections = parse_ini(text)
    if not sections:
        return {}, ""

    # 1. Hoodlum / DARKSiDERS:  [Achievements] + [AchievementsUnlockTimes]
    got = section(sections, "Achievements")
    times = section(sections, "AchievementsUnlockTimes")
    if got is not None and times is not None:
        found: Achievements = {}
        for name, value in got.items():
            if truthy(value) and looks_like_name(name):
                found[name] = plausible_time(times.get(name, 0))
        return found, "Hoodlum/DARKSiDERS"

    # 2. 3DM:  [State] ИМЯ=0101  +  [Time] ИМЯ=hex
    state = section(sections, "State")
    if state is not None:
        times = section(sections, "Time") or {}
        found = {}
        for name, value in state.items():
            if not looks_like_name(name):
                continue
            raw = str(value).strip().lower()
            if raw in ("0101", "01", "1") or (hex_to_uint_le(raw) or 0) != 0:
                found[name] = plausible_time(hex_to_uint_le(times.get(name, "0")) or 0)
        return found, "3DM"

    # 3. CreamAPI:  [achievements] ИМЯ=1 / unlocktime=...
    cream = section(sections, "achievements", "achievements_list")
    if cream and any(norm(k) in ACHIEVED_KEYS or norm(k) == "unlocktime" for k in cream):
        found = {}
        when = 0
        for key, value in cream.items():
            if norm(key) in TIME_KEYS:
                # CreamAPI пишет время без последних нулей — досчитываем
                number = to_int(value)
                when = number * 1000 if len(str(number)) == 7 else plausible_time(number)
        for name, value in cream.items():
            if norm(name) in ACHIEVED_KEYS or norm(name) in TIME_KEYS:
                continue
            if truthy(value) and looks_like_name(name):
                found[name] = when
        return found, "CreamAPI"

    # 4. «Просто список»:  [Achievements] ИМЯ=время_открытия (SOVEREIGN и похожие)
    flat = section(sections, "achievements", "unlocked", "earned")
    if flat is not None:
        found = {}
        for name, value in flat.items():
            if not looks_like_name(name):
                continue
            when = plausible_time(value)
            if when or truthy(value):
                found[name] = when
        if found or flat:
            return found, "список имён (SOVEREIGN и похожие)"

    # 5. По разделу на достижение: RUNE, CODEX, ALI213, RLD!, RLE, SKIDROW...
    found = {}
    understood = False
    hex_mode = False
    for name, values in sections.items():
        if norm(name) in NOT_ACHIEVEMENTS or not looks_like_name(name):
            continue
        achieved = False
        when = 0
        for key, value in values.items():
            nkey = norm(key)
            if nkey in ACHIEVED_KEYS or nkey == "state":
                understood = True
                if nkey == "state" and hex_to_uint_le(value) is not None:
                    hex_mode = True
                    if hex_to_uint_le(value) != 0:
                        achieved = True
                elif truthy(value):
                    achieved = True
            elif nkey in TIME_KEYS:
                when = plausible_time(value)
        # «1@1234567890» — так писал старый SKIDROW
        if not achieved:
            for value in values.values():
                if "@" in str(value):
                    head, _, tail = str(value).partition("@")
                    if truthy(head):
                        achieved = True
                        when = plausible_time(tail) or when
                    elif truthy(tail):
                        achieved = True
                        when = plausible_time(head) or when
        if achieved:
            found[name] = when
    if found:
        return found, "RLD!/hex" if hex_mode else "INI по разделам"

    # 6. RUNE и CODEX кладут рядом список вида
    #        [SteamAchievements]
    #        00000=COMPLETE_PROLOGUE
    #        Count=1
    #    Само состояние открытия живёт в разделах выше, поэтому тут данных
    #    нет — но файл нам понятен, ругаться на него не надо.
    if section(sections, "SteamAchievements") is not None:
        return {}, "RUNE/CODEX (список достижений, пусто)"

    if understood:
        return {}, "INI по разделам (пока ничего не открыто)"

    return {}, ""


# ── SmartSteamEmu: бинарный stats.bin ──

def parse_sse_bin(data: bytes) -> Achievements:
    """Формат SmartSteamEmu (SSE).

    Заголовок: 4 байта — сколько записей дальше. Дальше записи по 24 байта:
    первые 4 байта — CRC32 от имени достижения (задом наперёд), байты 8..12 —
    время открытия, байты 20..24 — состояние (0/1 достижение, больше — счётчик).
    """
    if len(data) < 28 or (len(data) - 4) % 24:
        return {}
    count = struct.unpack("<I", data[:4])[0]
    if not 0 < count < 20000 or len(data) - 4 != count * 24:
        return {}
    found: Achievements = {}
    for index in range(count):
        record = data[4 + index * 24: 4 + (index + 1) * 24]
        value = struct.unpack("<i", record[20:24])[0]
        if value not in (0, 1):
            continue                      # это счётчик, а не достижение
        crc = record[0:4][::-1].hex()     # имя achievement'а — это его CRC32
        if value == 1:
            when = struct.unpack("<i", record[8:12])[0]
            found[f"crc:{crc}"] = plausible_time(when)
    return found


# ── GreenLuma: достижения в реестре ──

def read_greenluma() -> dict[str, Achievements]:
    """GreenLuma Reborn и GreenLuma 2020 держат достижения в реестре."""
    result: dict[str, Achievements] = {}
    if winreg is None:
        return result
    for path in (r"SOFTWARE\GLR\AppID", r"SOFTWARE\GL2020\AppID"):
        for view in (0, getattr(winreg, "KEY_WOW64_32KEY", 0),
                     getattr(winreg, "KEY_WOW64_64KEY", 0)):
            try:
                root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                                      winreg.KEY_READ | view)
            except OSError:
                continue
            try:
                index = 0
                while True:
                    try:
                        appid = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    if not is_appid(appid):
                        continue
                    found = _greenluma_values(root, appid)
                    if found:
                        result[appid] = found
            finally:
                root.Close()
    return result


def _greenluma_values(root, appid: str) -> Achievements:
    """Читает раздел достижений одной игры из реестра GreenLuma."""
    try:
        key = winreg.OpenKey(root, appid, 0, winreg.KEY_READ)
    except OSError:
        return {}
    values: dict[str, str] = {}
    try:
        index = 0
        while True:
            try:
                name, value, _kind = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            values[name] = str(value)
    finally:
        key.Close()

    found: Achievements = {}
    for name, value in values.items():
        if name.endswith("_Time") or norm(name) in ACHIEVED_KEYS:
            continue
        if looks_like_name(name) and truthy(value):
            found[name] = plausible_time(values.get(name + "_Time", 0))
    return found


# ── Универсальный «нюхач»: пробует всё на любом файле ──

def sniff_file(path: Path) -> tuple[str, Achievements, str]:
    """Пробует прочитать файл всеми известными способами.

    Возвращает (название формата, достижения, пояснение). Пусто — не вышло.
    Именно это и позволяет поддерживать новые эмуляторы без правок кода.

    Разбор запоминается по размеру и времени файла: если файл не менялся,
    второй раз его читать незачем — а во время игры он меняется на каждой
    открытой ачивке, и тогда перечитываем.
    """
    key = os.path.normcase(str(path))
    mark = CACHE.fingerprint(path)
    if mark is None:
        CACHE.files.pop(key, None)
        return "", {}, ""
    remembered = CACHE.files.get(key)
    if remembered is not None and remembered[0] == mark:
        return remembered[1], dict(remembered[2]), remembered[3]

    shape, found, note = sniff_file_uncached(path, mark[0])
    CACHE.files[key] = [mark, shape, dict(found), note]
    return shape, found, note


def sniff_file_uncached(path: Path, size: int) -> tuple[str, Achievements, str]:
    """Собственно разбор — без памяти, всегда читает файл заново."""
    if size == 0:
        return "", {}, "файл пустой"
    if size > MAX_SNIFF_SIZE:
        return "", {}, "файл слишком большой для разбора"

    data = read_bytes(path)
    if not data:
        return "", {}, ""

    # 0. Картинки, звуки и прочая начинка игр — там достижений не бывает
    if path.suffix.lower() in MEDIA_SUFFIXES:
        return "", {}, ""

    # 1. JSON (Goldberg, GSE, EMPRESS, новые OnlineFix и прочие)
    stripped = data.lstrip()[:1]
    if stripped in (b"{", b"["):
        try:
            parsed = json.loads(data.decode("utf-8-sig", "replace"))
        except ValueError:
            parsed = None
        if parsed is not None:
            found = parse_json_achievements(parsed)
            if found:
                return "JSON (Goldberg/EMPRESS/OnlineFix)", found, ""
            return "", {}, "это json, но достижений внутри нет"

    # 2. Бинарник SmartSteamEmu
    if path.suffix.lower() in (".bin", ".dat") or path.name.lower() == "stats.bin":
        found = parse_sse_bin(data)
        if found:
            return "SmartSteamEmu (stats.bin)", found, ""

    # 3. Любой INI
    found, shape = parse_ini_achievements(data.decode("utf-8-sig", "replace"))
    if found:
        return f"INI ({shape})", found, ""
    if shape:
        return "", {}, ""          # формат понятен, просто ничего не открыто

    # Ругаемся только на файлы с «говорящим» именем: конфиг эмулятора
    # (steam_emu.ini и подобные) — это нормально, что достижений в нём нет.
    low = path.name.lower()
    if low in ACH_FILE_NAMES or ACH_FILE_RE.search(low):
        return "", {}, "формат не опознан"
    return "", {}, ""


# ───────────────── где эмуляторы держат достижения ─────────────────────

# Шаблоны путей. Подстановки (их раскрывает expand_template ниже):
#   {APPDATA} {LOCALAPPDATA} {PROGRAMDATA} {PUBLICDOC} {USERDOCS} {LOCALLOW}
#   {GAME} — папка игры, {APPID} — номер игры
# Звёздочка — любая одна папка на этом месте.
LOCATION_TEMPLATES: list[tuple[str, str]] = [
    ("Goldberg", "{APPDATA}/Goldberg SteamEmu Saves/{APPID}"),
    ("GSE (Goldberg fork)", "{APPDATA}/GSE Saves/{APPID}"),
    ("Goldberg (в папке игры)", "{GAME}/steam_settings/{APPID}"),
    ("Goldberg (в папке игры)", "{GAME}/steam_settings"),
    ("Goldberg (рядом с exe)", "{GAME}/Goldberg SteamEmu Saves/{APPID}"),
    ("EMPRESS", "{APPDATA}/EMPRESS/{APPID}"),
    ("EMPRESS", "{APPDATA}/EMPRESS/{APPID}/remote/{APPID}"),
    ("EMPRESS (документы)", "{PUBLICDOC}/EMPRESS/{APPID}"),
    ("EMPRESS (в папке игры)", "{GAME}/remote/{APPID}"),
    ("CODEX", "{PUBLICDOC}/Steam/CODEX/{APPID}"),
    ("CODEX (appdata)", "{APPDATA}/Steam/CODEX/{APPID}"),
    ("RUNE", "{PUBLICDOC}/Steam/RUNE/{APPID}"),
    ("RUNE (appdata)", "{APPDATA}/Steam/RUNE/{APPID}"),
    ("OnlineFix", "{PUBLICDOC}/OnlineFix/{APPID}/Stats"),
    ("OnlineFix", "{PUBLICDOC}/OnlineFix/{APPID}"),
    ("OnlineFix (в папке игры)", "{GAME}/Stats"),
    ("SKIDROW", "{LOCALAPPDATA}/SKIDROW/{APPID}"),
    ("SKIDROW (appdata)", "{APPDATA}/SKIDROW/{APPID}"),
    ("SKIDROW (документы)", "{USERDOCS}/SkidRow/{APPID}"),
    ("SKIDROW (SteamEmu)", "{USERDOCS}/{APPID}/SteamEmu/UserStats"),
    ("Hoodlum/DARKSiDERS", "{USERDOCS}/*/{APPID}/SteamEmu/UserStats"),
    ("Hoodlum/DARKSiDERS (в игре)", "{GAME}/SteamEmu/UserStats"),
    ("Hoodlum/DARKSiDERS (в игре)", "{GAME}/SteamEmu"),
    ("ALI213", "{USERDOCS}/VALVE/{APPID}"),
    ("ALI213 (профиль)", "{USERDOCS}/VALVE/{APPID}/*/Stats"),
    ("ALI213 (в папке игры)", "{GAME}/Profile/Stats"),
    ("ALI213 (в папке игры)", "{GAME}/Profile/*/Stats"),
    ("SmartSteamEmu", "{APPDATA}/SmartSteamEmu/{APPID}"),
    ("SmartSteamEmu (local)", "{LOCALAPPDATA}/SmartSteamEmu/{APPID}"),
    ("SmartSteamEmu (в игре)", "{GAME}/SmartSteamEmu"),
    ("CreamAPI", "{APPDATA}/CreamAPI/{APPID}"),
    ("CreamAPI", "{APPDATA}/CreamAPI/{APPID}/stats"),
    ("CreamAPI (local)", "{LOCALAPPDATA}/CreamAPI/{APPID}"),
    ("CreamAPI (в папке игры)", "{GAME}/stats"),
    ("RLD!", "{PROGRAMDATA}/RLD!/{APPID}"),
    ("RLD!", "{PROGRAMDATA}/Steam/RLD!/{APPID}"),
    ("RLD! (stats)", "{PROGRAMDATA}/Steam/RLD!/{APPID}/stats"),
    ("Reloaded/3DM", "{PROGRAMDATA}/Steam/{APPID}"),
    ("Reloaded/3DM (stats)", "{PROGRAMDATA}/Steam/{APPID}/stats"),
    ("DODI", "{PROGRAMDATA}/Steam/dodi/{APPID}/stats"),
    ("Player", "{PROGRAMDATA}/Steam/Player/{APPID}/stats"),
    ("RLE", "{APPDATA}/RLE/{APPID}"),
    ("Razor1911", "{APPDATA}/.1911/{APPID}"),
    ("TENOKE", "{APPDATA}/TENOKE/{APPID}"),
    ("KaOs", "{APPDATA}/KaOs/{APPID}"),
    ("эмулятор в папке игры", "{GAME}"),
    ("эмулятор в папке игры", "{GAME}/{APPID}"),
    ("эмулятор в папке игры", "{GAME}/remote/{APPID}"),
    ("эмулятор в папке игры", "{GAME}/save"),
    ("эмулятор в папке игры", "{GAME}/userdata"),
]

# Где вообще живут папки эмуляторов: их мы обшариваем целиком, даже если
# внутри имя папки не похоже на номер игры (например «0»).
EMU_BASE_DIRS: list[tuple[str, str]] = [
    ("Goldberg", "{APPDATA}/Goldberg SteamEmu Saves"),
    ("GSE (Goldberg fork)", "{APPDATA}/GSE Saves"),
    ("EMPRESS", "{APPDATA}/EMPRESS"),
    ("CODEX", "{APPDATA}/Steam/CODEX"),
    ("RUNE", "{PUBLICDOC}/Steam/RUNE"),
    ("CODEX (документы)", "{PUBLICDOC}/Steam/CODEX"),
    ("EMPRESS (документы)", "{PUBLICDOC}/EMPRESS"),
    ("OnlineFix", "{PUBLICDOC}/OnlineFix"),
    ("SKIDROW", "{LOCALAPPDATA}/SKIDROW"),
    ("SKIDROW (appdata)", "{APPDATA}/SKIDROW"),
    ("SmartSteamEmu", "{APPDATA}/SmartSteamEmu"),
    ("CreamAPI", "{APPDATA}/CreamAPI"),
    ("RLE", "{APPDATA}/RLE"),
    ("Razor1911", "{APPDATA}/.1911"),
    ("RLD!", "{PROGRAMDATA}/RLD!"),
    ("Reloaded/3DM", "{PROGRAMDATA}/Steam"),
    ("TENOKE", "{APPDATA}/TENOKE"),
    ("KaOs", "{APPDATA}/KaOs"),
]

# Что искать внутри папки: сначала точные имена, потом «на глазок»
DEEP_LEVELS = 2


def candidate_files(base: Path, deep: int = DEEP_LEVELS):
    """Файлы, похожие на достижения, внутри папки (на пару уровней вглубь).

    Кроме файлов с «говорящими» именами смотрим и на конфиги эмуляторов:
    некоторые (например SOVEREIGN.ini) держат достижения прямо внутри себя.
    """
    if not CACHE.is_dir(base):
        return
    for here, _dirnames, filenames in iter_dirs(base, deep):
        for filename in filenames:
            low = filename.lower()
            if Path(low).suffix in MEDIA_SUFFIXES:
                continue
            if low in ACH_FILE_NAMES or low in EMU_INI_NAMES or ACH_FILE_RE.search(low):
                yield here / filename


def expand_template(tpl: str, appid: str, game_dir: Path | None) -> list[Path]:
    """Разворачивает шаблон пути в реально существующие папки.

    Звёздочку разворачиваем руками, а не через glob: в названиях папок часто
    встречаются квадратные скобки (метки репаков вроде «[by ...]»), а glob
    считает их спецсимволом и ничего не находит.
    """
    if "{APPID}" in tpl and not appid:
        return []
    text = (tpl
            .replace("{APPDATA}", str(APPDATA))
            .replace("{LOCALAPPDATA}", str(LOCALAPPDATA))
            .replace("{PROGRAMDATA}", str(PROGRAM_DATA))
            .replace("{PUBLICDOC}", str(PUBLIC_DOCS))
            .replace("{USERDOCS}", str(USER_DOCS))
            .replace("{LOCALLOW}", str(LOCAL_LOW))
            .replace("{GAME}", str(game_dir or ""))
            .replace("{APPID}", appid or ""))

    parts = Path(text).parts
    if not parts:
        return []
    current = [Path(parts[0])]
    for part in parts[1:]:
        following: list[Path] = []
        for base in current:
            if part == "*":
                record = CACHE.list_dir(base)
                if record is None:
                    continue
                for name in record[1] + record[2]:    # подпапки и ярлыки на них
                    following.append(base / name)
            else:
                candidate = base / part
                if CACHE.exists(candidate):
                    following.append(candidate)
        current = following
        if not current:
            return []
    return [p for p in current if CACHE.exists(p)]


# ─────────── что за эмулятор стоит в папке игры (мост сам смотрит) ───────────

EMU_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("Goldberg / GSE", ("steam_settings", "gse.ini", "goldberg.ini",
                        "steam_api64.dll.goldberg")),
    ("SmartSteamEmu", ("smartsteamemu.ini", "smartsteamemu.dll",
                       "smartsteamloader", "coldclientloader.ini")),
    ("SOVEREIGN", ("sovereign.ini", "sovereign.dll")),
    ("RUNE", ("steam_emu.ini.rne", "steam_api64.rne", "rune.ini")),
    ("CODEX", ("steam_emu.ini.cdx", "steam_api64.cdx", "codex.ini")),
    ("OnlineFix", ("onlinefix.ini", "onlinefix64.dll", "onlinefix.dll")),
    ("CreamAPI", ("cream_api.ini", "creamapi.dll", "steam_api64.cdx")),
    ("ALI213", ("ali213.ini", "ali213.dll", "valve.ini", "steamconfig.ini")),
    ("Hoodlum / DARKSiDERS", ("hlm.ini", "ds.ini", "hoodlum.ini")),
    ("SteamConfig (Catherine)", ("steam_api.ini", "steamprofile")),
    ("TENOKE", ("tenoke.ini", "tenoke.dll")),
    ("Goldberg (Golden) / SteamStub", ("steam_api64.org", "steam_api.org")),
]


def identify_emulator(game_dir: Path) -> tuple[list[str], str]:
    """Смотрит на файлы в папке игры и говорит, какой там эмулятор и что читать.

    Возвращает (список названий, откуда номер игры).
    """
    names: list[str] = []
    if not game_dir.is_dir():
        return names, ""
    try:
        entries = {p.name.lower() for p in game_dir.iterdir()}
    except OSError:
        return names, ""

    for label, markers in EMU_MARKERS:
        if any(marker in entries for marker in markers):
            if label not in names:
                names.append(label)

    # Конфиги эмулятора могут лежать и в подпапке
    if not names:
        for here, _dirnames, filenames in iter_dirs(game_dir, 3):
            low = {f.lower() for f in filenames}
            for label, markers in EMU_MARKERS:
                if any(marker in low for marker in markers) and label not in names:
                    names.append(label)

    # Заодно попробуем понять, какой номер игры у этой сборки
    appid, note = find_appid_near(game_dir)
    return names, (f"{note} → {appid}" if appid else "")


# Слова, по которым узнаётся живой эмулятор внутри steam_api.dll
EMU_DLL_MARKERS = (
    b"goldberg", b"mr_goldberg", b"smartsteamemu", b"coldclient",
    b"sovereign", b"onlinefix", b"creamapi", b"tenoke", b"empress",
    b"codex", b"rune", b"steamclient_loader", b"kaos", b"rl_loader",
)
# А это — подпись честной библиотеки от самой Valve
VALVE_DLL_MARKERS = (b"valve corp", b"valve corporation", b"steam client api")


def check_steam_dlls(game_dir: Path, emu_dir: Path | None = None) -> str:
    """Проверяет, живой ли эмулятор лежит рядом с игрой.

    Частая беда: антивирус съедает подменную steam_api.dll, и остаётся
    оригинальная от Valve. Тогда Steam-API в игре не поднимается вообще,
    достижения нигде не копятся, и никакой мост их не найдёт — кроме
    собственного сохранения самой игры.

    Осторожно с выводами: RUNE и другие эмуляторы не пишут свой файл
    с нуля, а берут родную Valve-библиотеку и правят её. Внутри такого
    файла остаются строки Valve, и подпись перестаёт сходиться. Поэтому
    «Valve внутри» само по себе ничего не значит: смотрим, есть ли рядом
    хоть какие-то собственные файлы эмулятора.
    """
    for base in (game_dir, emu_dir):
        if base is None or not CACHE.is_dir(base):
            continue
        for here, _dirnames, filenames in iter_dirs(base, 3):
            low = {f.lower() for f in filenames}
            for _label, markers in EMU_MARKERS:
                if any(marker in low for marker in markers):
                    return ""       # эмулятор на месте, ругаться не на что

    dlls: list[Path] = []
    for here, _dirnames, filenames in iter_dirs(game_dir, 3):
        for filename in filenames:
            low = filename.lower()
            if low.endswith(".dll") and low.startswith(("steam_api", "steamclient")):
                dlls.append(here / filename)
    if not dlls:
        return ""

    # Библиотеки весят мегабайты, а меняются только при переустановке
    # эмулятора — значит, и вердикт можно помнить по их отпечаткам.
    marks = tuple((os.path.normcase(str(dll)), CACHE.fingerprint(dll)) for dll in dlls)
    remembered = CACHE.verdicts.get(marks)
    if remembered is not None:
        return remembered

    emulator = valve = False
    for dll in dlls:
        data = read_bytes(dll, 6 << 20).lower()
        if any(marker in data for marker in EMU_DLL_MARKERS):
            emulator = True
        if any(marker in data for marker in VALVE_DLL_MARKERS):
            valve = True
    verdict = ""
    if not emulator and valve:
        verdict = ("эмулятор не работает: рядом лежит оригинальная steam_api от Valve "
                   "(похоже, его удалил антивирус)")
    CACHE.verdicts[marks] = verdict
    return verdict


# ──────────────────── номер игры и папки игр ────────────────────


class GameInfo:
    """Игра, найденная на диске: папка, схема достижений и номер."""

    __slots__ = ("path", "ach_names", "appid", "appid_note", "emu_names",
                 "has_schema", "emu_hint", "emu_dir")

    def __init__(self, path: Path, ach_names: set[str], has_schema: bool = True,
                 emu_dir: Path | None = None):
        self.path = path
        self.ach_names = ach_names      # технические имена достижений в нижнем регистре
        self.appid = ""
        self.appid_note = ""
        self.emu_names: list[str] = []
        self.has_schema = has_schema
        self.emu_hint = ""              # предупреждение про нерабочий эмулятор
        # У Unity-игр эмулятор часто лежит не в корне, а глубоко внутри:
        # Hollow Knight\..._Data\Plugins\x86_64. Тогда «папка игры» — корень,
        # а «папка эмулятора» — та, что глубже.
        self.emu_dir = emu_dir

    @property
    def title(self) -> str:
        return self.path.name


def scan_games(roots: list[Path]) -> list[GameInfo]:
    """Находит папки игр и, если повезёт, схемы их достижений.

    Схема (steam_settings\\achievements.json) — не обязательна: она нужна
    только чтобы понимать, чьи достижения нашлись в папке без номера,
    и чтобы читать бинарный формат SmartSteamEmu.
    """
    found: dict[str, GameInfo] = {}

    def register(game_dir: Path, schema_names: set[str] | None,
                 emu_dir: Path | None = None) -> None:
        key = str(game_dir).lower()
        if key not in found:
            found[key] = GameInfo(game_dir, schema_names or set(),
                                  has_schema=schema_names is not None,
                                  emu_dir=emu_dir)
        elif schema_names is not None and not found[key].ach_names:
            found[key].ach_names = schema_names
            found[key].has_schema = True

    def game_root_from_engine_dir(here: Path) -> Path | None:
        """Из внутренностей движка достаёт корень игры.

        Бывает так: в корне игры ни одного файла эмулятора нет, всё спрятано
        в «..._Data/Plugins/x86_64». Поднимаемся вверх, пока не найдём папку
        игры — ту, где лежит «..._Data» или сам .exe.
        """
        root = here
        for _ in range(4):
            parent = root.parent
            if parent == root:
                break
            root = parent
            name = root.name.lower()
            if name.endswith("_data") or name in ENGINE_DIR_NAMES:
                continue
            try:
                has_exe = any(p.suffix.lower() == ".exe" for p in root.iterdir()
                              if p.is_file())
                has_data = any(p.is_dir() and p.name.lower().endswith("_data")
                               for p in root.iterdir())
            except OSError:
                return None
            if has_exe or has_data:
                return root
            return None
        return None

    for root in roots:
        for here, _dirnames, filenames in iter_dirs(root):
            lowers = {f.lower() for f in filenames}
            if not any(name in lowers for name in ("steam_settings", "steam_emu.ini",
                                                   "sovereign.ini", "smartsteamemu.ini",
                                                   "steam_api64.dll", "steam_api.dll")):
                continue
            schema_names: set[str] | None = None
            for candidate in (here / "steam_settings" / "achievements.json",
                              here / "steam_settings" / "steam_appid.txt"):
                if candidate.name != "achievements.json":
                    continue
                parsed = read_json(candidate)
                if isinstance(parsed, list):
                    names = {norm(item.get("name"))
                             for item in parsed
                             if isinstance(item, dict) and item.get("name")}
                    schema_names = names or None
            # «x86_64», «Plugins» и подобное — это внутренности движка, а не
            # папка игры: там лежат только копии steam_api.dll. Но игру всё
            # равно надо взять на заметку — иначе она выпадет из отчёта.
            if here.name.lower() in ENGINE_DIR_NAMES:
                game_dir = game_root_from_engine_dir(here)
                if game_dir is not None:
                    register(game_dir, schema_names, emu_dir=here)
                continue
            register(here, schema_names)

    return list(found.values())


def load_manual_map() -> dict[str, str]:
    """Читает games.txt: строки вида «Папка игры = 1234567»."""
    result: dict[str, str] = {}
    for line in read_text(MANUAL_FILE).splitlines():
        line = line.split("#")[0].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if is_appid(value):
            result[norm(key)] = value
    return result


def load_hydra_log_map() -> dict[str, str]:
    """Пары «папка → номер игры» из журнала самой Hydra.

    Она пишет туда строки вида:
        Generated achievements.json for 1234567 at D:\\...\\Game\\steam_settings
    Это честный источник: номер поставила сама Hydra.
    """
    result: dict[str, str] = {}
    text = read_text(HYDRA_LOG)
    if not text:
        return result
    for match in re.finditer(r"for (\d{3,10}) at ([A-Za-z]:\\[^\r\n]+)", text):
        appid, folder = match.group(1), match.group(2).strip()
        # сравнение по «хвосту» пути: игра могла быть перенесена в другую папку
        tail = "\\".join(Path(folder).parts[-3:]).lower()
        result.setdefault(tail, appid)
    return result


def steam_lookup_appid(title: str, cache: dict) -> str:
    """Ищет номер игры в магазине Steam по названию (нужен интернет)."""
    key = norm(title)
    if key in cache:
        return cache[key]

    appid = ""
    query = urllib.parse.urlencode({"term": title, "cc": "ru", "l": "ru"})
    try:
        with urllib.request.urlopen(
            "https://store.steampowered.com/api/storesearch/?" + query, timeout=8
        ) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        items = data.get("items") or []
        for item in items:
            # верим только точному совпадению названия
            if norm(item.get("name")) == key:
                appid = str(item.get("id") or "")
                break
    except Exception:
        appid = ""
    cache[key] = appid
    return appid


def find_appid_near(folder: Path, levels: int = 3) -> tuple[str, str]:
    """Ищет номер игры в конфигах эмулятора рядом с папкой игры.

    Возвращает (номер, откуда взяли).
    """
    for here, _dirnames, filenames in iter_dirs(folder, levels):
        for filename in filenames:
            if filename.lower() not in EMU_INI_NAMES:
                continue
            path = here / filename
            try:
                if path.stat().st_size > 300_000:
                    continue
            except OSError:
                continue
            text = read_text(path)
            # 1) обычная строка AppId=1234567 (у разных эмуляторов по-разному)
            for match in re.finditer(
                    r"(?im)^\s*(?:steam)?app_?id\s*=\s*[\"']?(\d{3,10})[\"']?\s*(?:#|;|$)",
                    text):
                if is_appid(match.group(1)):
                    return match.group(1), f"{filename}: AppId"
            # 2) комментарий вида «Game data is stored at ...\RUNE\<номер>»
            for match in re.finditer(r"(?i)\b(?:RUNE|CODEX|Steam|EMPRESS)\\(\d{3,10})\b", text):
                if is_appid(match.group(1)):
                    return match.group(1), f"{filename}: путь с номером"
            # 3) номер в имени файла: steam_appid.txt лежит рядом
    for here, _dirnames, filenames in iter_dirs(folder, 2):
        for filename in filenames:
            low = filename.lower()
            # бывает и «steam_appid.txt.txt» — сборщики любят удвоить расширение
            if low.startswith("steam_appid") or low == "appid.txt":
                text = read_text(here / filename).strip()
                if is_appid(text):
                    return text, f"{filename}"
    return "", ""


def read_steam_appid_txt(path: Path) -> str:
    """steam_appid.txt должен содержать число. Если там ерунда — вернём пусто."""
    text = read_text(path).strip()
    return text if is_appid(text) else ""


def resolve_appid(game: GameInfo, manual: dict[str, str],
                  hydra_log: dict[str, str], cache: dict, online: bool) -> None:
    """Выясняет номер игры и запоминает, откуда он взялся."""
    # 1) нормальный steam_appid.txt
    appid = read_steam_appid_txt(game.path / "steam_settings" / "steam_appid.txt")
    if appid:
        game.appid, game.appid_note = appid, "steam_appid.txt"
        return

    # 2) конфиг эмулятора рядом с игрой
    appid, note = find_appid_near(game.path)
    if appid:
        game.appid, game.appid_note = appid, note
        return

    # 3) ручная привязка из games.txt (название можно писать без меток репака)
    for key in (norm(game.path.name), norm(game.path.parent.name),
                norm(clean_title(game.path.name)),
                norm(clean_title(game.path.parent.name))):
        if key and key in manual:
            game.appid, game.appid_note = manual[key], "games.txt"
            return

    # 4) журнал Hydra: сравниваем хвост пути
    tail = "\\".join(game.path.parts[-2:]).lower()
    for logged_tail, logged_appid in hydra_log.items():
        if logged_tail.endswith(tail):
            game.appid, game.appid_note = logged_appid, "журнал Hydra"
            return

    # 5) магазин Steam (только если разрешено ключом --online)
    if online:
        for title in (game.path.name, clean_title(game.path.name),
                      clean_title(game.path.parent.name)):
            if not title:
                continue
            appid = steam_lookup_appid(title, cache)
            if appid:
                game.appid, game.appid_note = appid, "поиск в Steam"
                return

    game.appid_note = "номер не найден"


def split_by_schema(names: Achievements, games: list[GameInfo]):
    """Раскладывает достижения из папки без номера по играм.

    Папку вроде «GSE Saves\\0» могли наполнить сразу несколько игр (когда
    у сборки потерялся номер). Поэтому сверяем каждое название со схемами
    достижений игр и отдаём игре только её собственные.

    Возвращает (список пар «игра → её достижения», сколько названий не опознали).
    """
    leftover = dict(names)
    matched: list[tuple[GameInfo, Achievements]] = []
    for game in games:
        if not (game.appid and game.ach_names):
            continue
        mine = {name: when for name, when in leftover.items()
                if norm(name) in game.ach_names}
        if not mine:
            continue
        for name in mine:
            leftover.pop(name, None)
        matched.append((game, mine))
    return matched, len(leftover), leftover


# ─────────── реестр: часть эмуляторов и игр держит прогресс прямо там ───────────
#
# Достижения живут не только в файлах. GreenLuma (см. выше) — известный
# случай, но есть и другие. Поэтому проходим по HKCU\Software на небольшую
# глубину, собираем значения «имя = открыто/время» и выясняем, чьи они,
# сверкой со схемами достижений игр — тем же способом, что и для папки «0».
# Реестр только читается, ничего не меняется.

REGISTRY_ROOTS = [r"SOFTWARE"]
REGISTRY_SKIP = {"microsoft", "classes", "policies", "wow6432node", "registeredapplications",
                 "adobe", "google", "mozilla", "python", "pythonsoftwarefoundation",
                 "khronos", "valve", "steam", "glr", "gl2020", "clients",
                 "intel", "nvidia", "amd", "realtek"}
REGISTRY_MAX_DEPTH = 3      # HKCU\Software\Издатель\Игра — дальше не лезем
REGISTRY_MAX_KEYS = 3000    # страховка, чтобы прогон не подвис на чужом ПК


def scan_registry_achievements(limit: int = REGISTRY_MAX_KEYS) -> dict[str, Achievements]:
    """Собирает из HKCU\\Software значения, похожие на достижения."""
    result: dict[str, Achievements] = {}
    if winreg is None:
        return result

    pending = [(root, 0) for root in REGISTRY_ROOTS]
    checked = 0
    while pending and checked < limit:
        path, depth = pending.pop()
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ)
        except OSError:
            continue

        values: dict[str, str] = {}
        subkeys: list[str] = []
        try:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                values[name] = str(value)
            index = 0
            while True:
                try:
                    subkeys.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
        finally:
            key.Close()
        checked += 1

        found: Achievements = {}
        for name, value in values.items():
            if not looks_like_name(name) or not truthy(value):
                continue
            # рядом часто лежит ИМЯ_Time со временем открытия
            found[name] = plausible_time(values.get(name + "_Time", 0))
        if found:
            result[path] = found

        if depth + 1 < REGISTRY_MAX_DEPTH:
            for sub in subkeys:
                if sub.lower() in REGISTRY_SKIP or sub.startswith("{"):
                    continue
                pending.append((path + "\\" + sub, depth + 1))
    return result


# ───── свои сохранения игр: когда эмулятор вообще не работает ─────
#
# Бывает, что эмулятор из сборки съеден антивирусом: Steam-API в игре
# не поднимается вообще, и достижения копятся только в её собственном
# сохранении (%APPDATA%\Godot\app_userdata\<Игра>\save.json у Godot-игр).
# Hydra про такое не знает вообще. Мост читает это сохранение, а карту
# «внутреннее имя → имя в Steam» достаёт из файлов самой игры.


def load_own_map() -> dict:
    """Ручные карты из local-saves.json (на случай, если авто не справится).

    Вид файла:
        {
          "1234567": {
            "save": "MyGame",
            "map": {"_ACHIEVEMENT_FIRST_STEPS__NAME": "ach_first_steps"}
          }
        }
    """
    data = read_json(OWN_MAP_FILE)
    return data if isinstance(data, dict) else {}


def own_save_achievements(data) -> dict[str, bool]:
    """Вытаскивает из сохранения игры отметки «достижение открыто».

    Умеет два вида:
      * Godot-игра: {"sections": {"achievements": {"ИМЯ": {"completed": "true"}}}}
      * попроще:    {"achievements": {"ИМЯ": true}}
    """
    result: dict[str, bool] = {}
    if not isinstance(data, dict):
        return result

    candidates = []
    for key, value in data.items():
        if norm(key) in ("achievements", "achievement", "achievementsprogress"):
            candidates.append(value)
        if isinstance(value, dict):
            for inner_key, inner_value in value.items():
                if norm(inner_key) in ("achievements", "achievement"):
                    candidates.append(inner_value)
            # Godot: sections.achievements
            if norm(key) == "sections":
                for inner_key, inner_value in value.items():
                    if norm(inner_key) == "achievements":
                        candidates.append(inner_value)

    for block in candidates:
        if not isinstance(block, dict):
            continue
        for name, mark in block.items():
            if not looks_like_name(name):
                continue
            if isinstance(mark, dict):
                state = False
                for key, value in mark.items():
                    if norm(key) in ACHIEVED_KEYS and truthy(value):
                        state = True
                        break
                    if isinstance(value, bool) and value:
                        state = True
                result[name] = state
            elif isinstance(mark, bool):
                result[name] = mark
            elif truthy(mark):
                result[name] = True
    return result


def derive_api_map(binary: Path, keys: list[str], cache: dict) -> tuple[dict[str, str], str]:
    """Достаёт из файлов игры карту «внутреннее имя → имя достижения в Steam».

    В Godot-играх каждое достижение — это ресурс, где рядом лежат
    внутренний ключ, описание и настоящее имя для Steam:
        _ACHIEVEMENT_BUTTON_1__NAME
        _ACHIEVEMENT_BUTTON_1__DESC
        ach_1_button_clicks
    Ищем строку сразу после __DESC — это и есть имя для Steam.

    Возвращает (карта, пояснение).
    """
    try:
        stat = binary.stat()
    except OSError:
        return {}, "файл игры не читается"
    key = f"{binary}|{stat.st_size}|{int(stat.st_mtime)}"
    cached = cache.get(key)
    if isinstance(cached, dict):
        return dict(cached), "из кэша"

    if stat.st_size > MAX_MAP_FILE_SIZE:
        return {}, "файл игры слишком большой"

    data = read_bytes(binary, MAX_MAP_FILE_SIZE)
    if not data:
        return {}, "файл игры не читается"

    mapping: dict[str, str] = {}
    for name in keys:
        needle = name.encode()
        api = ""
        for match in re.finditer(re.escape(needle), data):
            tail = data[match.end(): match.end() + 400]
            for token in re.finditer(rb"[A-Za-z][A-Za-z0-9_]{2,63}", tail):
                candidate = token.group().decode()
                if candidate == name:
                    continue
                if candidate.endswith(("__NAME", "__DESC", "_icon", "_unachieved")):
                    continue
                if candidate in keys:
                    continue
                if looks_like_name(candidate):
                    api = candidate
                    break
            if api:
                break
        if api:
            mapping[name] = api

    if not mapping:
        return {}, "не нашёл в файлах игры имена достижений Steam"
    if len(set(mapping.values())) != len(mapping):
        return {}, "имена достижений в файле игры перепутались"
    if len(mapping) != len(keys):
        missing = [k for k in keys if k not in mapping]
        return {}, f"не для всех достижений нашлись имена (нет: {len(missing)})"

    cache[key] = mapping
    return mapping, "из файлов игры"


OWN_SAVE_BASES: list[str] = [
    "{APPDATA}/Godot/app_userdata/*",
    "{APPDATA}/*",
    "{LOCALAPPDATA}/*",
    "{LOCALLOW}/*/*",
    "{USERDOCS}/My Games/*",
]

OWN_SAVE_NAMES = {"save.json", "savegame.json", "achievements.json", "progress.json",
                  "savegame.dat", "profile.json", "user.json", "settings.json",
                  "save_data.json", "data.json", "userdata.json", "achievements.dat"}


def own_save_bases() -> dict[str, list[Path]]:
    """Папки, где игры держат свои сохранения, разложенные по названиям.

    Собираем один раз на прогон: этих папок немного, а игр много, и раньше
    они разворачивались заново под каждую игру.
    """
    index: dict[str, list[Path]] = {}
    for tpl in OWN_SAVE_BASES:
        for base in expand_template(tpl, "", None):
            if not CACHE.is_dir(base):
                continue
            index.setdefault(norm(base.name), []).append(base)
    return index


def find_own_save(index: dict[str, list[Path]], titles: list[str]) -> Path | None:
    """Ищет сохранение игры с её собственным учётом достижений."""
    wanted = {norm(t) for t in titles if t and norm(t)}
    if not wanted:
        return None

    for name, bases in index.items():
        if name not in wanted:
            continue
        for base in bases:
            best: Path | None = None
            best_when = 0.0
            for here, _dirnames, filenames in iter_dirs(base, 2):
                for filename in filenames:
                    if filename.lower() not in OWN_SAVE_NAMES:
                        continue
                    path = here / filename
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    if stat.st_size > 4 << 20:
                        continue
                    if best is None or stat.st_mtime > best_when:
                        best, best_when = path, stat.st_mtime
            if best is not None:
                return best
    return None


def game_binary(game_dir: Path) -> Path | None:
    """Самый «содержательный» файл игры — из него достаём имена достижений."""
    best: Path | None = None
    best_size = 0
    for suffix in (".pck", ".exe"):
        for here, _dirnames, filenames in iter_dirs(game_dir, 2):
            for filename in filenames:
                if not filename.lower().endswith(suffix):
                    continue
                path = here / filename
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > best_size:
                    best, best_size = path, size
        if best is not None:
            return best
    return None


def collect_own_saves(games: list[GameInfo], own_map: dict, cache: dict) -> list["Source"]:
    """Ищет игры, которые сами ведут учёт достижений (эмулятор не работает)."""
    sources: list[Source] = []
    index = own_save_bases()
    for game in games:
        titles = [game.path.name, game.path.name.split("-")[0].strip()]
        seen: set[str] = set()
        for here, _dirnames, filenames in iter_dirs(game.path, 2):
            for filename in filenames:
                if filename.lower().endswith(".exe"):
                    titles.append(Path(filename).stem)
        titles = [t for t in titles if t and norm(t) not in seen and not seen.add(norm(t))]

        manual = own_map.get(game.appid) if game.appid else None
        if isinstance(manual, dict) and manual.get("save"):
            titles.insert(0, str(manual["save"]))

        save_file = find_own_save(index, titles)
        if save_file is None:
            continue
        data = read_json(save_file)
        progress = own_save_achievements(data)
        if not progress:
            continue

        keys = sorted(progress)
        mapping: dict[str, str] = {}
        note = ""
        if isinstance(manual, dict) and isinstance(manual.get("map"), dict):
            mapping = {str(k): str(v) for k, v in manual["map"].items()}
            note = "карта из local-saves.json"
        else:
            binary = game_binary(game.path)
            if binary is None:
                note = "не нашёл файлов игры, чтобы понять имена достижений"
            else:
                mapping, note = derive_api_map(binary, keys, cache)

        if not mapping:
            sources.append(Source(
                "своё сохранение игры", save_file, game.appid, {},
                f"игра «{game.path.name}»: {note or 'карта не построилась'}"
                f" — подсказка: допиши карту в local-saves.json",
            ))
            continue

        when = int(save_file.stat().st_mtime)
        found: Achievements = {}
        for internal, done in progress.items():
            api = mapping.get(internal)
            if done and api:
                found[api] = when
        if not found:
            continue
        done_count = sum(1 for v in progress.values() if v)
        sources.append(Source(
            "своё сохранение игры", save_file, game.appid, found,
            f"игра «{game.path.name}»: открыто {done_count} из {len(progress)}, "
            f"карта имён {note}. Точного времени открытия игра не хранит — "
            f"поставил дату сохранения",
        ))
    return sources


# ─────────── игры на Adobe AIR: свой учёт в %APPDATA%\<id>\Local Store ───────────
#
# У сборок на Adobe AIR (Flash-игры, порты старых флешек) эмулятора часто
# нет вовсе, но игра всё равно ведёт свой учёт достижений и складывает его
# в Local Store рядом с собой. Формат файлов простой: сырой deflate, а внутри
# записи «длина записи (2 байта) + текст». Ключи с префиксом a_ — это ровно
# имена достижений Steam без самого префикса (a_btb_win → btb_win).
#
# Как связать игру с её папкой: в сборке лежит META-INF/AIR/application.xml,
# там написан id приложения — он же имя папки в %APPDATA%.

AIR_ACH_PREFIX = "a_"


def find_air_app_id(game_dir: Path) -> str:
    """Достаёт id приложения из META-INF/AIR/application.xml внутри сборки."""
    for here, _dirnames, filenames in iter_dirs(game_dir, 3):
        if here.name.lower() != "air" or here.parent.name.lower() != "meta-inf":
            continue
        for filename in filenames:
            if filename.lower() != "application.xml":
                continue
            text = read_text(here / filename)
            match = re.search(r"<id>\s*([^<\s]+)\s*</id>", text)
            if match:
                return match.group(1)
    return ""


def read_air_entries(path: Path) -> list[str]:
    """Разбирает файл из Local Store: сырой deflate + строки «длина + текст»."""
    data = read_bytes(path)
    if not data:
        return []
    body = b""
    for wbits in (-15, 15, 31):
        try:
            body = zlib.decompressobj(wbits).decompress(data)
        except zlib.error:
            body = b""
        if body:
            break
    if not body:
        body = data          # бывает, что файл лежит и без сжатия

    entries: list[str] = []
    index = 0
    while index + 2 <= len(body):
        size = struct.unpack(">H", body[index:index + 2])[0]
        index += 2
        if index + size > len(body):
            break
        try:
            entries.append(body[index:index + size].decode("utf-8"))
        except UnicodeDecodeError:
            return []        # это точно не «длина + текст», не наш формат
        index += size
    return entries


def collect_air_saves(games: list[GameInfo]) -> list["Source"]:
    """Ищет достижения в сохранениях игр, собранных на Adobe AIR."""
    sources: list["Source"] = []
    for game in games:
        if not game.appid:
            continue
        app_id = find_air_app_id(game.path)
        if not app_id:
            continue
        store = APPDATA / app_id / "Local Store"
        if not store.is_dir():
            continue

        found: Achievements = {}
        where: Path | None = None
        for here, _dirnames, filenames in iter_dirs(store, 2):
            for filename in filenames:
                if not filename.lower().endswith(".sav"):
                    continue
                path = here / filename
                keys = [entry[len(AIR_ACH_PREFIX):] for entry in read_air_entries(path)
                        if entry.startswith(AIR_ACH_PREFIX)]
                if not keys:
                    continue
                try:
                    when = int(path.stat().st_mtime)
                except OSError:
                    when = 0
                for name in keys:
                    if looks_like_name(name):
                        found[name] = when
                where = where or path

        if not found:
            continue
        sources.append(Source(
            "своё сохранение игры (Adobe AIR)", where, game.appid, found,
            f"игра «{game.path.name}»: учёт в «{app_id}\\Local Store», "
            f"{plural(len(found), 'достижение', 'достижения', 'достижений')}. "
            f"Времени открытия игра не хранит — поставил дату файла",
        ))
    return sources


# ─────────────────── читаемые места Hydra (для сверки) ───────────────────
# Список собран из исходников Hydra (find-achievement-files.ts).
# Если файл лежит ровно здесь — Hydra увидит его и без нас.

def hydra_readable_paths(appid: str) -> set[Path]:
    return {
        PUBLIC_DOCS / "Steam" / "CODEX" / appid / "achievements.ini",
        APPDATA / "Steam" / "CODEX" / appid / "achievements.ini",
        PUBLIC_DOCS / "Steam" / "RUNE" / appid / "achievements.ini",
        PUBLIC_DOCS / "OnlineFix" / appid / "Stats" / "Achievements.ini",
        PUBLIC_DOCS / "OnlineFix" / appid / "Achievements.ini",
        APPDATA / "Goldberg SteamEmu Saves" / appid / "achievements.json",
        APPDATA / "GSE Saves" / appid / "achievements.json",
        APPDATA / "EMPRESS" / "remote" / appid / "achievements.json",
        PUBLIC_DOCS / "EMPRESS" / "remote" / appid / "achievements.json",
        APPDATA / "RLE" / appid / "achievements.ini",
        APPDATA / ".1911" / appid / "achievement",
        APPDATA / "CreamAPI" / appid / "stats" / "CreamAPI.Achievements.cfg",
        USER_DOCS / "SKIDROW" / appid / "SteamEmu" / "UserStats" / "achiev.ini",
        PROGRAM_DATA / "RLD!" / appid / "achievements.ini",
        PROGRAM_DATA / "Steam" / "RLD!" / appid / "stats" / "achievements.ini",
        PROGRAM_DATA / "Steam" / "Player" / appid / "stats" / "achievements.ini",
        PROGRAM_DATA / "Steam" / "dodi" / appid / "stats" / "achievements.ini",
    }


# ───────────────────────────── источники ──────────────────────────────


class Source:
    """Найденные где-то достижения."""

    __slots__ = ("kind", "where", "appid", "achievements", "note",
                 "is_native", "fmt")

    def __init__(self, kind: str, where: Path | None, appid: str,
                 achievements: Achievements, note: str = "", fmt: str = ""):
        self.kind = kind
        self.where = where
        self.appid = appid
        self.achievements = achievements
        self.note = note
        self.fmt = fmt
        self.is_native = False   # True, если Hydra читает этот файл и сама


def collect_sources(roots: list[Path], games: list[GameInfo], ours: set[str],
                    own_map: dict, cache: dict) -> tuple[list[Source], list[str]]:
    """Проходит по всем известным местам и собирает, что нашлось.

    Свои собственные файлы (которые мы же и создали) за источники не считаем:
    иначе мост на каждом прогоне находил бы сам себя.
    """
    sources: list[Source] = []
    notes: list[str] = []
    ours_lower = {p.lower() for p in ours}
    seen: set[str] = set()

    def add(kind: str, path: Path, appid: str, note: str = "",
            only: set[str] | None = None) -> None:
        key = (str(path).lower(), appid)
        if key in seen or str(path).lower() in ours_lower:
            return
        seen.add(key)
        fmt, found, problem = sniff_file(path)
        if found and only is not None:
            found = {name: when for name, when in found.items() if name in only}
        if not found:
            if problem and problem != "это json, но достижений внутри нет":
                notes.append(f"{path} — {problem}")
            return
        sources.append(Source(kind, path, appid, found, note, fmt))

    # 1. Места, привязанные к номеру игры (в том числе внутри папок игр)
    for game in games:
        # у Unity-игр файлы эмулятора лежат в глубине папки игры, поэтому
        # шаблоны раскрываем и от корня, и от папки эмулятора
        for game_dir in (game.path, game.emu_dir):
            if game_dir is None:
                continue
            for label, tpl in LOCATION_TEMPLATES:
                for base in expand_template(tpl, game.appid, game_dir):
                    for path in candidate_files(base):
                        add(label, path, game.appid)

    # 2. Целиком папки эмуляторов: тут могут лежать сохранения с «потерянным»
    #    номером (например папка «0»), поэтому смотрим все подпапки
    for label, tpl in EMU_BASE_DIRS:
        for base in expand_template(tpl, "", None):
            if not base.is_dir():
                continue
            try:
                children = sorted(p for p in base.iterdir() if p.is_dir())
            except OSError:
                continue
            for folder in children:
                if folder.name.lower() in ("settings", "config", "userdata"):
                    continue
                for path in candidate_files(folder, 1):
                    fmt, found, _problem = sniff_file(path)
                    if not found:
                        continue
                    if is_appid(folder.name):
                        add(f"{label}\\{folder.name}", path, folder.name,
                            "номер из имени папки (верный)")
                        continue
                    # Номер потерялся (например папка «0»). Раскладываем
                    # достижения по играм, сверяя названия со схемами игр.
                    matched, rest, leftover = split_by_schema(found, games)
                    for game, mine in matched:
                        add(f"{label}\\{folder.name}", path, game.appid,
                            f"папка «{folder.name}» → игра «{game.title}»: "
                            f"{len(mine)} из {len(found)} названий совпали со схемой игры",
                            only=set(mine))
                    if not matched:
                        sources.append(Source(
                            f"{label}\\{folder.name}", path, "", found,
                            f"номер игры потерян (папка «{folder.name}»), "
                            f"и ни одна схема не подошла — чья папка, непонятно"))
                    elif rest:
                        sources.append(Source(
                            f"{label}\\{folder.name}", path, "", leftover,
                            f"в папке «{folder.name}» есть ещё "
                            f"{plural(rest, 'название', 'названия', 'названий')} "
                            f"от другой игры — не понял, чьи они"))

    # 3. Свои учёты достижений внутри игр (эмулятор может быть нерабочим)
    for source in collect_own_saves(games, own_map, cache):
        key = str(source.where).lower() if source.where else ""
        if key and key in ours_lower:
            continue
        sources.append(source)
        seen.add(key)

    # 4. GreenLuma: достижения в реестре
    for appid, found in read_greenluma().items():
        sources.append(Source("GreenLuma (реестр)", None, appid, found,
                              "реестр HKCU\\SOFTWARE\\GLR\\AppID", "реестр"))

    # 5. Adobe AIR: игры на Flash ведут свой учёт в Local Store
    for source in collect_air_saves(games):
        key = str(source.where).lower() if source.where else ""
        if key and key in ours_lower:
            continue
        sources.append(source)

    # 6. Прочий реестр: редкие эмуляторы и игры пишут достижения в HKCU\Software.
    #    Чьи они — выясняем сверкой со схемами игр, как и для папки «0».
    for key_path, found in scan_registry_achievements().items():
        matched, _rest, _leftover = split_by_schema(found, games)
        for game, mine in matched:
            if len(mine) < 2:
                # одно совпадение в реестре ничего не доказывает: у Unity-игр
                # там сотни настроек, и одна может случайно совпасть с именем
                continue
            sources.append(Source(
                "реестр Windows", None, game.appid, mine,
                f"HKCU\\{key_path} → игра «{game.title}»: "
                f"{len(mine)} из {len(found)} названий совпали со схемой игры",
                "реестр"))

    return sources, notes


# ──────────────────────── база Hydra (подсказка) ──────────────────────────


def hydra_known_appids() -> set[str]:
    """Какие номера игр вообще есть в базе Hydra. Это подсказка, не гарантия:
    часть записей в базе сжата, поэтому что-то может не найтись."""
    known: set[str] = set()
    db_dir = HYDRA_DIR / "hydra-db"
    if not db_dir.is_dir():
        return known
    for path in db_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in (".ldb", ".log"):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        known.update(m.decode() for m in re.findall(rb"steam:(\d{3,10})", data))
        known.update(m.decode() for m in re.findall(rb'"objectId":"(\d{3,10})"', data))
    return known


# ──────────────────────────── запись моста ────────────────────────────


def load_state() -> dict:
    state = read_json(STATE_FILE)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("ours", [])
    return state


def save_state(state: dict) -> None:
    write_text_atomic(
        STATE_FILE, json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    )


def write_bridge_file(appid: str, achievements: Achievements, ours: set[str],
                      dry_run: bool) -> tuple[Path, int, str]:
    """Дописывает достижения туда, откуда Hydra их точно прочитает.

    Hydra читает два места с форматом Goldberg: современное «GSE Saves»
    и старое «Goldberg SteamEmu Saves». Берём первое свободное: если в
    папке уже лежит чужой файл (свои сохранения игры) — не трогаем его,
    а пишем в соседнее место.

    Возвращает (файл, сколько нового добавили, пояснение).
    """
    ours_lower = {p.lower() for p in ours}
    target = None
    for base in (BRIDGE_DIR, APPDATA / "Goldberg SteamEmu Saves"):
        candidate = base / appid / "achievements.json"
        if candidate.exists() and str(candidate).lower() not in ours_lower:
            continue          # там уже чужой файл — обойдёмся без него
        target = candidate
        break
    if target is None:
        target = BRIDGE_DIR / appid / "achievements.json"
        return target, 0, "оба места заняты чужими файлами — не трогаю"

    raw = read_json(target)
    if not isinstance(raw, dict):
        raw = {}

    added = 0
    for name, when in achievements.items():
        old = raw.get(name)
        if isinstance(old, dict) and old.get("earned"):
            # уже записано раньше; время не перетираем, если оно осмысленное
            if old.get("earned_time") or not when:
                continue
        raw[name] = {"earned": True, "earned_time": when}
        added += 1

    if added and not dry_run:
        write_text_atomic(
            target,
            json.dumps(raw, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        )
        ours.add(str(target))
    return target, added, ""


# ────────────────────────────── отчёт ──────────────────────────────


def run_once(args) -> int:
    started = time.time()
    report: list[str] = []

    # Память обхода: в слежении она живёт между прогонами (main её и заводит),
    # при разовом запуске создаётся пустая — тогда всё читается с диска.
    paths = getattr(args, "_paths", None)
    if paths is None:
        paths = PathCache()
        args._paths = paths
    use_path_cache(paths)
    paths.begin_pass()

    def say(line: str = "") -> None:
        report.append(line)
        if not args.quiet or line.startswith(("МОСТ", "ОШИБКА", "ИТОГО")):
            print(line, flush=True)

    say("Мост достижений для Hydra")
    say("=" * 60)
    say(f"Обновлено: {time.strftime('%d.%m.%Y %H:%M')}")

    roots = [Path(p) for p in (args.roots or DEFAULT_ROOTS)]
    for line in read_text(ROOTS_FILE).splitlines():
        line = line.split("#")[0].strip().strip('"')
        if line and Path(line).is_dir() and Path(line) not in roots:
            roots.append(Path(line))
    roots = [p for p in roots if p.is_dir()]
    say("Папки с играми: " + (", ".join(str(p) for p in roots) or "нет ни одной"))

    state = load_state()
    ours = set(state.get("ours") or [])
    cache = read_json(CACHE_FILE) or {}
    own_cache = read_json(OWN_CACHE_FILE) or {}
    manual = load_manual_map()
    own_map = load_own_map()
    hydra_log = load_hydra_log_map()

    games = scan_games(roots)
    for game in games:
        resolve_appid(game, manual, hydra_log, cache, online=args.online)
        game.emu_names, _note = identify_emulator(game.path)
        game.emu_hint = check_steam_dlls(game.path, game.emu_dir)

    say(f"Игр в индексе: {len(games)} "
        f"(со схемой достижений: {sum(1 for g in games if g.has_schema)})")
    for game in sorted(games, key=lambda g: g.title.lower()):
        if not game.emu_names and not game.emu_hint:
            continue
        line = f"  {game.title} — эмулятор: {', '.join(game.emu_names) or 'не опознан'}"
        line += f" ({game.appid})" if game.appid else " (номер неизвестен)"
        say(line)
        if game.emu_hint:
            say(f"      ⚠ {game.emu_hint}")
    say()

    sources, problems = collect_sources(roots, games, ours, own_map, own_cache)
    known = hydra_known_appids()

    # что делать с каждым источником
    to_bridge: dict[str, Achievements] = {}
    visible: list[Source] = []
    unresolved: list[Source] = []

    for source in sources:
        if not source.appid:
            unresolved.append(source)
            continue
        # если файл лежит ровно там, где Hydra его ищет, — мостить нечего
        source.is_native = (source.where is not None
                            and source.where in hydra_readable_paths(source.appid))
        if source.is_native:
            visible.append(source)
            continue
        bucket = to_bridge.setdefault(source.appid, {})
        for name, when in source.achievements.items():
            if name not in bucket or (when and not bucket[name]):
                bucket[name] = when

    # ── что мостим ──
    title_by_appid = {g.appid: g.title for g in games if g.appid}
    say("МОСТ ПОСТРОЕН (Hydra сама этого не видит):")
    if not to_bridge:
        say("  нечего мостить — всё, что нашлось, Hydra читает и так")
    for appid in sorted(to_bridge):
        achievements = to_bridge[appid]
        target, added, problem = write_bridge_file(appid, achievements, ours, args.dry_run)
        # название игры: из индекса, а если её там нет — по папке источника
        title = title_by_appid.get(appid)
        if not title:
            title = next((s.where.parent.name for s in sources
                          if s.appid == appid and not s.is_native and s.where), "?")
        origins = sorted({s.kind for s in sources if s.appid == appid and not s.is_native})
        say(f"  {title} ({appid}): {plural(len(achievements), 'достижение', 'достижения', 'достижений')} "
            f"из {', '.join(origins)}")
        for source in sources:
            if source.appid == appid and not source.is_native and source.note:
                say(f"      · {source.note}")
        if problem:
            say(f"      ⚠ {problem} — {target}")
            continue
        hint = "" if appid in known else "   ⚠ в базе Hydra этого номера не нашлось"
        action = "записал бы" if args.dry_run else "записал"
        say(f"      {action} {added} новых в {target}{hint}")

    # ── что и так видно ──
    say()
    say("УЖЕ ВИДНО БЕЗ НАС (Hydra читает сама):")
    if not visible:
        say("  ничего")
    for source in visible:
        say(f"  {source.kind} ({source.appid}): "
            f"{plural(len(source.achievements), 'достижение', 'достижения', 'достижений')} — {source.where}")

    # ── что не получилось ──
    if unresolved:
        say()
        say("НЕ РАЗОБРАЛСЯ:")
        for source in unresolved:
            say(f"  {source.kind}: "
                f"{plural(len(source.achievements), 'достижение', 'достижения', 'достижений')} — {source.note}")
            for name in sorted(source.achievements)[:5]:
                say(f"      например: {name}")
            if source.where:
                say(f"      файл: {source.where}")

    # ── файлы, похожие на достижения, но нечитаемые ──
    if problems and not args.quiet:
        say()
        say("ФАЙЛЫ, КОТОРЫЕ НЕ РАЗОБРАЛ (первые 10):")
        for line in problems[:10]:
            say(f"  {line}")

    # ── игры без номера ──
    no_appid = [g for g in games if not g.appid]
    if no_appid:
        say()
        say("ИГРЫ, У КОТОРЫХ НЕ ВЫШЛО ОПРЕДЕЛИТЬ НОМЕР:")
        for game in no_appid:
            say(f"  {game.path} — {game.appid_note}")
        say("  подсказка: допиши строку «Название папки = номер игры» в games.txt")

    say()
    say(f"ИТОГО: источников {len(sources)}, "
        f"мостов {len(to_bridge)}, уже видно {len(visible)}, "
        f"не разобрано {len(unresolved)}, за {time.time() - started:.1f} с")
    if args.dry_run:
        say("Это был примерочный прогон (--dry-run): на диск ничего не записано.")

    if not args.dry_run:
        state["ours"] = sorted(ours)
        state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
        if args.online and cache:
            write_text_atomic(
                CACHE_FILE,
                json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            )
        if own_cache:
            write_text_atomic(
                OWN_CACHE_FILE,
                json.dumps(own_cache, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            )

    REPORT_FILE.write_text("\n".join(report) + "\n", encoding="utf-8")
    return 0


# ────────────────────────────── запуск ──────────────────────────────


def acquire_watcher_lock():
    """Пускает работать только одно слежение сразу.

    Второе только мешало бы первому, поэтому занимаем файл-замок.
    Возвращает открытый файл (держать, пока работаем) или None, если занято.
    """
    if msvcrt is None:
        return True
    handle = None
    try:
        # открываем без обрезки файла: обрезать занятый замок Windows не даст
        handle = open(LOCK_FILE, "a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"x")      # занять есть что
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except Exception:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Собирает достижения из всех эмуляторов в место, "
                    "которое читает Hydra Launcher.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Примеры:\n"
               "  python bridge.py               обычный прогон\n"
               "  python bridge.py --dry-run     только показать\n"
               "  python bridge.py --watch 10    следить, обновляя раз в 10 секунд\n",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="ничего не писать, только показать план")
    parser.add_argument("--watch", nargs="?", const=30, type=int, metavar="СЕК",
                        help="повторять прогон каждые N секунд (по умолчанию 30)")
    parser.add_argument("--roots", nargs="+", metavar="ПАПКА",
                        help="дополнительные папки с играми")
    parser.add_argument("--online", action="store_true",
                        help="разрешить искать номера игр в магазине Steam")
    parser.add_argument("--full", action="store_true",
                        help="при слежении каждый раз обходить всё начисто, "
                             "ничего не помня о папках")
    parser.add_argument("--full-every", type=int, dest="full_every", metavar="СЕК",
                        help="как часто при слежении обходить всё начисто "
                             f"(по умолчанию {FULL_EVERY_DEFAULT}; 0 — каждый раз)")
    parser.add_argument("--quiet", action="store_true",
                        help="короткий отчёт")
    args = parser.parse_args(argv)

    # Запуск без окна (pythonw.exe) — это как раз то, что нужно для автозагрузки.
    # Но у такого процесса нет вывода вообще, и обычный print() падает.
    # Поэтому подставляем пустышку на место вывода.
    for stream_name in ("stdout", "stderr"):
        if getattr(sys, stream_name, None) is None:
            setattr(sys, stream_name, open(os.devnull, "w", encoding="utf-8"))
    # чтобы русский текст не превращался в кракозябры при перенаправлении вывода
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not args.watch:
        return run_once(args)

    lock = acquire_watcher_lock()
    if lock is None:
        print("Слежение уже работает — второе не нужно. Пока!")
        return 0

    interval = max(5, args.watch)
    full_every = FULL_EVERY_DEFAULT if args.full_every is None else args.full_every
    if args.full:
        full_every = 0
    full_every = max(0, full_every)

    args._paths = PathCache()
    print(f"Слежу за достижениями, обновляю раз в {interval} секунд. "
          f"Закрыть — Ctrl+C.")
    if full_every:
        print(f"Что лежит в папках, помню и перечитываю раз в "
              f"{plural(full_every, 'секунду', 'секунды', 'секунд')} "
              f"— а если папка изменилась, то сразу.")
    print()
    last_full = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if full_every == 0 or now - last_full >= full_every:
                args._paths.forget()
                last_full = now
            run_once(args)
            print(f"\n(следующая проверка через {interval} секунд)\n")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nОстановился. Пока!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
