#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты на разборщики bridge.py.

Зачем они нужны
---------------
Мост читает файлы эмуляторов «на глазок»: одни форматы знает точно, другие
нюхает наугад. Ошибка в разборщике — тихая: файл просто не прочитается, и
человек снова увидит пустой лаунчер, не понимая почему. Поэтому каждый
формат проверяется на образце из папки `samples`.

Образцы написаны руками по тем форматам, которые мост обещает понимать.
Исключение одно: `rune-schema-missing.ini` повторяет настоящий файл из
установленной игры (Hollow Knight под RUNE) — про этот случай есть
отдельный раздел в README, «Чего он не умеет».

Запуск
------
    python -m unittest discover -s tests -t .     из папки проекта
    run-tests.bat                                 то же самое, двойным щелчком

За пределы временной папки тесты ничего не пишут, игры на диске не трогают.
"""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))       # чтобы нашёлся bridge.py из корня проекта

import bridge  # noqa: E402  (импорт после правки sys.path — так и задумано)

SAMPLES = HERE / "samples"

# Время открытия, которое стоит в образцах: 1700000000 — это 1 ноября 2023 года.
WHEN = 1700000000


def sample_text(name: str) -> str:
    """Читает образец так же, как это делает сам мост."""
    return bridge.read_text(SAMPLES / name)


def sample_json(name: str):
    return json.loads(sample_text(name))


def sse_bin(crc: str = "deadbeef", when: int = 1700000700, state: int = 1) -> bytes:
    """Собирает бинарник SmartSteamEmu: заголовок и записи по 24 байта.

    Имя достижения в этом формате не хранится — только его CRC32,
    причём записанный задом наперёд.
    """
    record = bytearray(24)
    record[0:4] = bytes.fromhex(crc)[::-1]
    struct.pack_into("<i", record, 8, when)
    struct.pack_into("<i", record, 20, state)
    return struct.pack("<I", 1) + bytes(record)


class IniPerSectionTest(unittest.TestCase):
    """INI, где каждому достижению отведён свой раздел (RUNE, CODEX, RLD!)."""

    def test_rune(self):
        found, shape = bridge.parse_ini_achievements(sample_text("rune-achievements.ini"))
        self.assertEqual(found, {"ACH_FIRST_STEPS": WHEN})
        self.assertEqual(shape, "INI по разделам")

    def test_codex_time_key_spelled_differently(self):
        found, _shape = bridge.parse_ini_achievements(sample_text("codex-achievements.ini"))
        self.assertEqual(found, {"ACH_SPEEDRUN": 1700000100})

    def test_rld_hex_state(self):
        found, shape = bridge.parse_ini_achievements(sample_text("rld-hex.ini"))
        self.assertEqual(found, {"ACH_RLD_ONE": 0})   # открыто, время не записано
        self.assertEqual(shape, "RLD!/hex")

    def test_old_skidrow_at_sign(self):
        found, _shape = bridge.parse_ini_achievements(sample_text("skidrow-old.ini"))
        self.assertEqual(found, {"ACH_SKID_ONE": 1700000400, "ACH_SKID_TWO": 1700000500})

    def test_schema_missing_is_empty_but_understood(self):
        """Тот самый Hollow Knight: файл есть, а достижений в нём нет.

        Тут важно не выдумать ничего лишнего и честно сказать, что формат
        понятен. Пустой словарь с непустым названием формата — это ответ
        «переносить нечего», а не «не разобрался».
        """
        found, shape = bridge.parse_ini_achievements(sample_text("rune-schema-missing.ini"))
        self.assertEqual(found, {})
        self.assertEqual(shape, "RUNE/CODEX (список достижений, пусто)")


class IniOtherShapesTest(unittest.TestCase):
    """Остальные виды INI: две таблицы, простой список, состояние hex-ом."""

    def test_hoodlum_two_tables(self):
        found, shape = bridge.parse_ini_achievements(sample_text("hoodlum-achievements.ini"))
        self.assertEqual(found, {"ACH_CAREFUL": 1700000200})
        self.assertEqual(shape, "Hoodlum/DARKSiDERS")

    def test_3dm_hex_state_and_time(self):
        found, shape = bridge.parse_ini_achievements(sample_text("3dm-state-time.ini"))
        self.assertEqual(found, {"ACH_3DM_ONE": WHEN})
        self.assertEqual(shape, "3DM")

    def test_creamapi(self):
        found, shape = bridge.parse_ini_achievements(sample_text("creamapi-achievements.ini"))
        self.assertEqual(found, {"ACH_CREAM_ONE": WHEN})
        self.assertEqual(shape, "CreamAPI")

    def test_flat_list(self):
        # В этом формате ноль — «не открыто»: в списке перечислены все
        # достижения игры, а не только взятые. Поэтому второго тут нет.
        found, shape = bridge.parse_ini_achievements(sample_text("flat-list-achievements.ini"))
        self.assertEqual(found, {"ACH_FLAT_ONE": 1700000300})
        self.assertEqual(shape, "список имён (SOVEREIGN и похожие)")

    def test_empty_text(self):
        self.assertEqual(bridge.parse_ini_achievements(""), ({}, ""))

    def test_text_without_achievements(self):
        self.assertEqual(bridge.parse_ini_achievements("[Settings]\nLanguage=russian\n"),
                         ({}, ""))


class JsonTest(unittest.TestCase):
    """JSON: Goldberg / GSE словарём и EMPRESS списком."""

    def test_goldberg_dict(self):
        found = bridge.parse_json_achievements(sample_json("goldberg-achievements.json"))
        self.assertEqual(found, {
            "ACH_JSON_ONE": 1700000500,
            "ACH_JSON_THREE": 0,        # открыто, время не записано
        })
        self.assertNotIn("ACH_JSON_TWO", found)      # earned=false — не открыто
        self.assertNotIn("Count", found)             # служебный ключ, не достижение
        self.assertNotIn("_comment", found)          # наша же подпись в образце

    def test_empress_list(self):
        found = bridge.parse_json_achievements(sample_json("empress-achievements.json"))
        self.assertEqual(found, {
            "ACH_LIST_ONE": 1700000600,
            "ACH_LIST_THREE": 0,        # unlocked="true", время не записано
        })
        self.assertNotIn("ACH_LIST_TWO", found)

    def test_junk_instead_of_json(self):
        self.assertEqual(bridge.parse_json_achievements([]), {})
        self.assertEqual(bridge.parse_json_achievements({}), {})
        self.assertEqual(bridge.parse_json_achievements("строка"), {})


class SmartSteamEmuBinTest(unittest.TestCase):
    """Бинарный stats.bin — единственный формат не текстом."""

    def test_unlocked(self):
        # имя в файле перевёрнуто, поэтому на выходе — исходные "deadbeef"
        self.assertEqual(bridge.parse_sse_bin(sse_bin()), {"crc:deadbeef": 1700000700})

    def test_locked(self):
        self.assertEqual(bridge.parse_sse_bin(sse_bin(state=0)), {})

    def test_counter_is_not_an_achievement(self):
        # значение больше единицы — это счётчик (шаги, убийства), а не достижение
        self.assertEqual(bridge.parse_sse_bin(sse_bin(state=42)), {})

    def test_two_records(self):
        first = sse_bin(crc="11111111", when=1700000700)
        second = sse_bin(crc="22222222", when=1700000800)
        data = struct.pack("<I", 2) + first[4:] + second[4:]
        self.assertEqual(bridge.parse_sse_bin(data),
                         {"crc:11111111": 1700000700, "crc:22222222": 1700000800})

    def test_names_are_read_back_the_right_way_round(self):
        """CRC в файле лежит задом наперёд — проверяем, что читаем верно."""
        self.assertEqual(bridge.parse_sse_bin(sse_bin(crc="a1b2c3d4")),
                         {"crc:a1b2c3d4": 1700000700})

    def test_junk_returns_nothing(self):
        self.assertEqual(bridge.parse_sse_bin(b""), {})
        self.assertEqual(bridge.parse_sse_bin(b"\x01\x00\x00\x00" + b"\x00" * 23), {})
        # заголовок обещает 5 записей, а запись одна — верить нельзя
        self.assertEqual(bridge.parse_sse_bin(struct.pack("<I", 5) + sse_bin()[4:]), {})


class SnifferTest(unittest.TestCase):
    """«Нюхач»: как он решает, что за файл ему подсунули."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def sniff(self, name: str, data):
        path = self.tmp / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
        return bridge.sniff_file(path)

    def test_empty_file(self):
        self.assertEqual(self.sniff("achievements.ini", b""), ("", {}, "файл пустой"))

    def test_picture_is_not_even_read(self):
        # картинка с текстом достижений внутри — всё равно картинка
        self.assertEqual(self.sniff("achievements.png", "[A]\nB=1\n"), ("", {}, ""))

    def test_json_without_achievements(self):
        self.assertEqual(self.sniff("achievements.json", '{"language": "russian"}'),
                         ("", {}, "это json, но достижений внутри нет"))

    def test_garbage_with_speaking_name_is_reported(self):
        # файл называется achievements.ini, но внутри ерунда — об этом надо сказать
        self.assertEqual(self.sniff("achievements.ini", "просто текст\n"),
                         ("", {}, "формат не опознан"))

    def test_garbage_with_quiet_name_is_silent(self):
        # а вот на случайный файл ругаться незачем
        self.assertEqual(self.sniff("notes.txt", "просто текст\n"), ("", {}, ""))

    def test_rune_ini_is_recognised(self):
        shape, found, note = self.sniff("achievements.ini", sample_text("rune-achievements.ini"))
        self.assertEqual(shape, "INI (INI по разделам)")
        self.assertEqual(found, {"ACH_FIRST_STEPS": WHEN})
        self.assertEqual(note, "")

    def test_goldberg_json_is_recognised(self):
        shape, found, _note = self.sniff("achievements.json",
                                         sample_text("goldberg-achievements.json"))
        self.assertEqual(shape, "JSON (Goldberg/EMPRESS/OnlineFix)")
        self.assertEqual(found, {"ACH_JSON_ONE": 1700000500, "ACH_JSON_THREE": 0})

    def test_stats_bin_is_recognised(self):
        shape, found, _note = self.sniff("stats.bin", sse_bin())
        self.assertEqual(shape, "SmartSteamEmu (stats.bin)")
        self.assertEqual(found, {"crc:deadbeef": 1700000700})


class EncodingsTest(unittest.TestCase):
    """Кодировки: старые репаки пишут в cp1251, кое-кто — в UTF-16."""

    TEXT = "[Игра]\nНазвание=Тест\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def read_again(self, name: str, encoding: str) -> str:
        path = self.tmp / name
        path.write_bytes(self.TEXT.encode(encoding))
        return bridge.read_text(path)

    def test_utf8(self):
        self.assertEqual(self.read_again("u.ini", "utf-8"), self.TEXT)

    def test_utf8_with_bom(self):
        self.assertEqual(self.read_again("b.ini", "utf-8-sig"), self.TEXT)

    def test_cp1251(self):
        self.assertEqual(self.read_again("c.ini", "cp1251"), self.TEXT)

    def test_utf16(self):
        self.assertEqual(self.read_again("w.ini", "utf-16"), self.TEXT)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(bridge.read_text(self.tmp / "нет-такого.ini"), "")


class WritesTest(unittest.TestCase):
    """Запись: целиком, одним движением, и с копией прежнего содержимого."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_into_missing_folder(self):
        target = self.tmp / "GSE Saves" / "367520" / "achievements.json"
        bridge.write_text_atomic(target, '{"ACH_ONE": 1700000000}')
        self.assertEqual(target.read_text(encoding="utf-8"), '{"ACH_ONE": 1700000000}')
        self.assertFalse(target.with_name("achievements.json.tmp").exists())

    def test_previous_content_stays_as_bak(self):
        target = self.tmp / "achievements.json"
        bridge.write_text_atomic(target, "первый")
        bridge.write_text_atomic(target, "второй")
        self.assertEqual(target.read_text(encoding="utf-8"), "второй")
        self.assertEqual(target.with_name("achievements.json.bak").read_text(encoding="utf-8"),
                         "первый")

    def test_russian_survives_the_round_trip(self):
        target = self.tmp / "report.txt"
        bridge.write_text_atomic(target, "Достижений перенесено: 13\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "Достижений перенесено: 13\n")
        # на диске перевод строки записан так, как принято в системе
        self.assertEqual(bridge.read_text(target),
                         "Достижений перенесено: 13" + os.linesep)


class SmallHelpersTest(unittest.TestCase):
    """Мелочи, на которых всё держится."""

    def test_hex_to_uint_le(self):
        self.assertEqual(bridge.hex_to_uint_le("00f15365"), WHEN)
        self.assertEqual(bridge.hex_to_uint_le("00000000"), 0)
        self.assertIsNone(bridge.hex_to_uint_le("0"))       # нечётное число знаков
        self.assertIsNone(bridge.hex_to_uint_le("zz"))
        self.assertIsNone(bridge.hex_to_uint_le(""))

    def test_plausible_time(self):
        self.assertEqual(bridge.plausible_time("1700000000"), WHEN)
        self.assertEqual(bridge.plausible_time("1"), 0)     # «1» — это не дата
        self.assertEqual(bridge.plausible_time("0"), 0)
        self.assertEqual(bridge.plausible_time("не число"), 0)

    def test_looks_like_name(self):
        self.assertTrue(bridge.looks_like_name("ACH_ONE"))
        self.assertTrue(bridge.looks_like_name("_ACHIEVEMENT_BUTTON_1__NAME"))
        self.assertFalse(bridge.looks_like_name("a"))        # слишком коротко
        self.assertFalse(bridge.looks_like_name("Achievements"))
        self.assertFalse(bridge.looks_like_name("Count"))
        self.assertFalse(bridge.looks_like_name("имя по-русски"))

    def test_is_appid(self):
        self.assertTrue(bridge.is_appid("367520"))
        self.assertFalse(bridge.is_appid("480"))             # Spacewar, не игра
        self.assertFalse(bridge.is_appid("0"))
        self.assertFalse(bridge.is_appid("12"))
        self.assertFalse(bridge.is_appid("../../etc"))

    def test_parse_ini_cuts_trailing_comments(self):
        sections = bridge.parse_ini("; подпись\n[Game]\nappid = 367520  # номер игры\n\n[Empty]\n")
        self.assertEqual(sections, {"Game": {"appid": "367520"}, "Empty": {}})

    def test_norm_and_clean_title(self):
        self.assertEqual(bridge.norm("Unlock Time"), "unlocktime")
        self.assertEqual(bridge.clean_title("Hollow Knight [RePack.net]"), "Hollow Knight")

    def test_plural(self):
        self.assertEqual(bridge.plural(1, "достижение", "достижения", "достижений"),
                         "1 достижение")
        self.assertEqual(bridge.plural(13, "достижение", "достижения", "достижений"),
                         "13 достижений")
        self.assertEqual(bridge.plural(22, "достижение", "достижения", "достижений"),
                         "22 достижения")


class TablesTest(unittest.TestCase):
    """Цифры, обещанные в README, должны совпадать с таблицами в коде.

    Если таблицу почистили, а README забыли — тест упадёт. Это и нужно:
    обещать 19 эмуляторов, зная 12, хуже, чем не обещать ничего.
    """

    FAMILIES = {
        "Goldberg", "GSE", "CODEX", "RUNE", "EMPRESS", "OnlineFix", "SKIDROW",
        "SmartSteamEmu", "CreamAPI", "RLD!", "Reloaded/3DM", "DODI", "ALI213",
        "Hoodlum/DARKSiDERS", "RLE", "Razor1911", "TENOKE", "KaOs",
    }

    # Подстановки, которые мост умеет раскрывать (expand_template).
    PLACEHOLDERS = {"APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PUBLICDOC",
                    "USERDOCS", "LOCALLOW", "GAME", "APPID"}

    @staticmethod
    def family(name: str) -> str:
        return name.split(" (")[0]

    def test_at_least_fifty_search_paths(self):
        self.assertGreaterEqual(len(bridge.LOCATION_TEMPLATES), 50)

    def test_at_least_eighteen_base_folders(self):
        self.assertGreaterEqual(len(bridge.EMU_BASE_DIRS), 18)

    def test_all_promised_emulators_are_still_known(self):
        known = {self.family(name) for name, _path in bridge.LOCATION_TEMPLATES}
        known |= {self.family(name) for name, _path in bridge.EMU_BASE_DIRS}
        known.discard("эмулятор в папке игры")       # это не эмулятор, а место
        self.assertTrue(self.FAMILIES.issubset(known),
                        f"пропали из таблиц: {sorted(self.FAMILIES - known)}")
        self.assertEqual(len(known), 19,
                         f"README обещает 19 эмуляторов, а в таблицах их {len(known)}")

    def test_sovereign_is_still_a_known_config(self):
        # SOVEREIGN не в таблицах путей: он пишет свой ini рядом с игрой,
        # и узнаётся по имени файла настроек.
        self.assertIn("sovereign.ini", bridge.EMU_INI_NAMES)

    def test_every_path_uses_known_placeholders(self):
        """Опечатка в подстановке — это путь с «{STEEM}» внутри. Такое ловим."""
        paths = list(bridge.LOCATION_TEMPLATES) + list(bridge.EMU_BASE_DIRS)
        paths += [(base, base) for base in bridge.OWN_SAVE_BASES]
        for name, path in paths:
            with self.subTest(name=name, path=path):
                used = set(re.findall(r"\{(\w+)\}", path))
                self.assertTrue(used <= self.PLACEHOLDERS,
                                f"неизвестные подстановки: {sorted(used - self.PLACEHOLDERS)}")


class RepoHygieneTest(unittest.TestCase):
    """Чего в публичном репозитории быть не должно.

    Проверяются только те файлы, которые git действительно отдаёт людям:
    локальные report.txt и state.json с путями этого компьютера в клон не
    попадают, а вот bridge.py и README — попадают, и в них личных данных
    быть не должно. Историю коммитов этот тест не смотрит: он про файлы.
    """

    # Ищем не просто слово, а след личного пути или почты: иначе тест
    # начнёт ругаться на обычные английские слова.
    SUSPECTS = [
        "users\\divan", "users/divan",        # путь на этом компьютере
        "d:\\about me", "d:/about me",        # личная папка с заметками
        "blade129056",                        # личная почта
    ]
    SKIP_SUFFIXES = {".gif", ".png", ".jpg", ".jpeg", ".mp4", ".ico", ".lock"}

    @staticmethod
    def tracked_files() -> list[Path]:
        """Список файлов репозитория. Без git тест просто пропускается."""
        result = subprocess.run(["git", "ls-files", "-z"], cwd=HERE.parent,
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise unittest.SkipTest("git не ответил — сравнивать не с чем")
        return [HERE.parent / name for name in result.stdout.split("\0") if name]

    def test_no_personal_paths_in_tracked_files(self):
        root = HERE.parent
        bad = []
        for path in self.tracked_files():
            if path.suffix.lower() in self.SKIP_SUFFIXES or path == Path(__file__).resolve():
                continue      # картинки не читаем, а в этом файле искомое написано
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            for needle in self.SUSPECTS:
                if needle in text:
                    bad.append(f"{path.relative_to(root)} → {needle}")
        self.assertEqual(bad, [], f"в файлах репозитория остались личные данные: {bad}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
