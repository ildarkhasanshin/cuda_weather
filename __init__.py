import os
import re
import time
import urllib.request
import urllib.parse
import json
from typing import Tuple
from datetime import datetime, timezone, timedelta
from cudatext import *
from cudatext_keys import *
import cudatext_cmd
import collections
import random

from cudax_lib import get_translation
_ = get_translation(__file__)

class Command:
    last_start = None
    fn_config = None
    title = 'Weather'
    tree = None
    h_dlg = None
    h_menu = None
    city = None
    utc_offset = None

    def __init__(self):
        self.fn_config = os.path.join(app_path(APP_DIR_SETTINGS), 'cuda_weather.ini')
        self.last_start = self.get_last_start()
        self.city = self.get_city()
        self.utc_offset = self.get_utc_offset()

    def get_last_start(self):
        return ini_read(self.fn_config, 'options', 'last_start', '0')

    def get_city(self):
        return ini_read(self.fn_config, 'options', 'city', '0')

    def get_utc_offset(self):
        return ini_read(self.fn_config, 'options', 'offset', '0')

    def update(self):
        city, utc_offset = self.detect_city()

        try:
            w = self.get_weather_wttr(city)
            data = self.format_weather(w, utc_offset_seconds=utc_offset)
            ini_write(self.fn_config, 'options', 'last_start', str(self.get_unixtime()))
            ini_write(self.fn_config, 'options', 'data', str(data))
            self.init_options()
        except Exception as e:
            print(f"Error: {e}")

    def get(self):
        data = ini_read(self.fn_config, 'options', 'data', '0')
        if data == '0':
            self.update()
            return data
        else:
            return data

    def on_start(self, ed_self):
        self.show_panel()
        get_unixtime_ = self.get_unixtime()
        if (self.last_start == '0'):
            ini_write(self.fn_config, 'options', 'last_start', str(get_unixtime_))

    def on_init_plugins_menu(self, ed_self):
        get_unixtime_ = self.get_unixtime()
        self.last_start = self.get_last_start()
        if self.is_old(get_unixtime_, self.last_start):
            self.update()

    def config(self):
        file_open(self.fn_config)

    def get_unixtime(self):
        return int(time.time())

    def is_old(self, ts1, ts2):
        return abs(int(ts1) - int(ts2)) > 3 * 3600

    def time_begin_(self):
        return time.perf_counter()

    def time_end_(self, start):
        return time.perf_counter() - start

    def _http_get_json(self, url: str, retries: int = 3, timeout: int = 15) -> dict:
        # time_begin_ = self.time_begin_()
        #
        req = urllib.request.Request(url, headers={
            "User-Agent": "curl/7.68.0",
            "Accept": "application/json",
        })
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    #
                    # print(self.time_end_(time_begin_))
                    #
                    return json.load(resp)
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5 * attempt)
        raise last_err
        #
        # print(self.time_end_(time_begin_))

    def _parse_offset_str(self, s) -> int:
        if not s or not isinstance(s, str):
            return 0
        m = re.match(r"^([+-])(\d{1,2}):?(\d{2})?$", s.strip())
        if not m:
            return 0
        sign = 1 if m.group(1) == "+" else -1
        h = int(m.group(2))
        mm = int(m.group(3) or 0)

        return sign * (h * 3600 + mm * 60)

    def detect_city(self) -> Tuple[str, int]:
        # time_begin_ = self.time_begin_()
        #
        city = None
        offset = None
        #
        self.city = self.get_city()
        self.utc_offset = self.get_utc_offset()
        #
        if self.city == '0':
            services = [
                (
                    "https://ipwho.is/",
                    lambda d: d.get("city", ""),
                    lambda d: int(d.get("timezone", {}).get("offset", 0) or 0),
                ),
                (
                    "http://ip-api.com/json/?fields=status,city,timezone,offset",
                    lambda d: d.get("city", ""),
                    lambda d: int(d.get("offset", 0) or 0),
                ),
                (
                    "https://ipapi.co/json/",
                    lambda d: d.get("city", ""),
                    lambda d: self._parse_offset_str(d.get("utc_offset", "")),
                ),
            ]
            for url, city_fn, off_fn in services:
                try:
                    data = self._http_get_json(url, retries=1, timeout=8)
                    city = city_fn(data)
                    offset = off_fn(data)
                    if city:
                        if city != self.city:
                            ini_write(self.fn_config, 'options', 'city', str(city))
                        if offset != self.utc_offset:
                            ini_write(self.fn_config, 'options', 'offset', str(offset))
                        return city, off_fn(data)
                except Exception:
                    continue
        else:
            city = self.city
            offset = self.utc_offset
        #
        if not city:
            city = 'Moscow'
        if not offset:
            offset = 0
        #
        # print(self.time_end_(time_begin_))
        #
        return city, offset

    def _parse_obs_time(self, t: str) -> Tuple[int, int]:
        parts = t.split()
        h, m = map(int, parts[0].split(":"))
        ampm = parts[1].upper() if len(parts) > 1 else ""
        if ampm == "PM" and h != 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0

        return h, m

    def _format_offset(self, offset_seconds: int) -> str:
        if not offset_seconds:
            return "UTC"
        sign = "+" if offset_seconds >= 0 else "-"
        abs_off = abs(offset_seconds)
        oh = abs_off // 3600
        om = (abs_off % 3600) // 60

        return f"UTC{sign}{oh:02d}:{om:02d}"

    def localize_observation(self, obs_time_utc: str, utc_offset_seconds: int) -> str:
        if not obs_time_utc:
            return "n/a"
        try:
            h, m = self._parse_obs_time(obs_time_utc)
            base = datetime.now(timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0)
            local_dt = base + timedelta(seconds=utc_offset_seconds)
            off_label = self._format_offset(utc_offset_seconds)
            if base == local_dt:
                return local_dt.strftime(f"%H:%M ({off_label})")
            else:
                return local_dt.strftime(f"%H:%M")
        except Exception:
            return f"{obs_time_utc} UTC"

    def get_weather_wttr(self, city: str) -> dict:
        # time_begin_ = self.time_begin_()
        #
        encoded = urllib.parse.quote(city)
        url = f"https://wttr.in/{encoded}?format=j1&lang=en"
        # url = f"https://wttr.is/{encoded}?format=j1&lang=en"
        data = self._http_get_json(url)
        # data = self._http_get_json(url, 3, 60)
        cur = data["current_condition"][0]
        desc = cur.get("weatherDesc", [{}])[0].get("value", "Unknown").strip()
        #
        # print(self.time_end_(time_begin_))
        #
        return {
            "city": city,
            "time": cur.get("observation_time", ""),
            "condition": desc,
            "temperature": float(cur["temp_C"]),
            "feels_like": float(cur["FeelsLikeC"]),
            "humidity": int(cur["humidity"]),
            "wind_speed": float(cur["windspeedKmph"]) / 3.6,
            "wind_dir": cur["winddir16Point"],
            "pressure": float(cur["pressure"]) * 0.750062,
            "precip": float(cur["precipMM"]),
        }

    def format_weather(self, w: dict, utc_offset_seconds: int) -> str:
        time_str = self.localize_observation(w["time"], utc_offset_seconds)
        data = [
            f"{w['city']} ({time_str})",
            f"Condition: {w['condition']}",
            f"Temperature: {w['temperature']} °C",
            f"Feels like: {w['feels_like']} °C",
            f"Humidity: {w['humidity']} %",
            f"Wind: {w['wind_speed']:.1f} m/s ({w['wind_dir']})",
            f"Pressure: {w['pressure']:.0f} mmHg",
            f"Precipitation: {w['precip']} mm",
        ]

        return "|".join(data)

    def init_options(self):
        sidebar_temp = None
        sidebar_temp_ = False

        lines = self.get().split('|')
        if len(lines) > 1:
            tree_proc(self.tree, TREE_ITEM_DELETE, 0)

            for i in lines:
                if '°C' in i and not sidebar_temp_:
                    sidebar_temp = re.findall(r'\d+\.\d+|\d+', i)
                    sidebar_temp_ = True
                tree_root_handle = tree_proc(self.tree, TREE_ITEM_ADD, index=-1, text=i)

            if sidebar_temp is not None:
                btn_h_ = ''
                for item in app_proc(PROC_SIDEPANEL_ENUM_ALL, ''):
                    if item['cap'] == self.title:
                        btn_h_ = item['btn_h']
                if btn_h_ != '':
                    button_proc(btn_h_, BTN_SET_OVERLAY, sidebar_temp)

    def init_panel(self):
        btn_h_ = ''
        for item in app_proc(PROC_SIDEPANEL_ENUM_ALL, ''):
            if item['cap'] == self.title:
                btn_h_ = item['btn_h']
        if btn_h_ != '':
            button_proc(btn_h_, BTN_SET_OVERLAY, '0')

        self.h_dlg = dlg_proc(0, DLG_CREATE)

        n = dlg_proc(self.h_dlg, DLG_CTL_ADD, prop='treeview')
        dlg_proc(self.h_dlg, DLG_CTL_PROP_SET, index=n, prop={
            'name': 'tree',
            'a_r': ('',']'),
            'a_b': ('',']'),
            'on_menu': 'cuda_weather.tree_on_menu',
        })

        self.tree = dlg_proc(self.h_dlg, DLG_CTL_HANDLE, index=n)
        tree_proc(self.tree, TREE_PROP_SHOW_ROOT, text='0')

        dlg_proc(self.h_dlg, DLG_SCALE)
        tree_proc(self.tree, TREE_THEME)

        app_proc(PROC_SIDEPANEL_ADD_DIALOG, (
            self.title,
            self.h_dlg,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'img', 'weather.png')
        ))

    def show_panel(self):
        self.init_panel()
        self.init_options()
        app_proc(PROC_SIDEPANEL_ACTIVATE, (self.title, True))
        self.generate_context_menu()

    def generate_context_menu(self):
        if not self.h_menu:
            self.h_menu = menu_proc(0, MENU_CREATE)
        menu_proc(self.h_menu, MENU_CLEAR)
        menu_proc(self.h_menu, MENU_ADD, command='cuda_weather.action_update', caption='Update')

    def action_update(self):
        self.update()

    def tree_on_menu(self, id_dlg, id_ctl, data='', info=''):
        self.generate_context_menu()
        menu_proc(self.h_menu, MENU_SHOW)
