import os
import re
import time
import urllib.request
import urllib.parse
import json
from typing import Tuple
from datetime import datetime, timezone, timedelta
from cudatext import *

class Command:
    def __init__(self):
        global last_start
        global fn_config

        fn_config = os.path.join(app_path(APP_DIR_SETTINGS), 'cuda_weather.ini')
        last_start = ini_read(fn_config, 'options', 'last_start', '0')

    def update(self):
        city, utc_offset = self.detect_city()
        if not city:
            print("[Weather] could not auto-detect city - falling back to Moscow")
            city = "Moscow"
            utc_offset = 0

        try:
            w = self.get_weather_wttr(city)
            data = '[Weather] ' + self.format_weather(w, utc_offset_seconds=utc_offset)
            self.out(data)
            ini_write(fn_config, 'options', 'last_start', str(self.get_unixtime()))
            ini_write(fn_config, 'options', 'data', str(data))
        except Exception as e:
            print(f"Error: {e}")

    def get(self):
        return ini_read(fn_config, 'options', 'data', '')

    def out(self, data):
        msg_status(data)
        print(data)

    def on_start(self, ed_self):
        global last_start
        global fn_config

        get_unixtime_ = self.get_unixtime()
        if (int(last_start) == 0):
            ini_write(fn_config, 'options', 'last_start', str(get_unixtime_))

        if self.is_old(get_unixtime_, last_start):
            self.update()
        else:
            self.out(self.get())

    def config(self):
        global fn_config

        file_open(fn_config)

    def get_unixtime(self):
        return int(time.time())

    def is_old(self, ts1, ts2):
        return abs(int(ts1) - int(ts2)) > 3 * 3600

    def _http_get_json(self, url: str, retries: int = 3, timeout: int = 15) -> dict:
        req = urllib.request.Request(url, headers={
            "User-Agent": "curl/7.68.0",
            "Accept": "application/json",
        })
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.load(resp)
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5 * attempt)
        raise last_err

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
                if city:
                    return city, off_fn(data)
            except Exception:
                continue

        return "", 0

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
        encoded = urllib.parse.quote(city)
        url = f"https://wttr.in/{encoded}?format=j1&lang=en"
        data = self._http_get_json(url)
        cur = data["current_condition"][0]
        desc = cur.get("weatherDesc", [{}])[0].get("value", "Unknown").strip()

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
        data = f"{w['city']}" + f" ({time_str})" + f" :: Condition: {w['condition']}" + f" :: Temperature: {w['temperature']} °C" + f" (Feels like: {w['feels_like']} °C)" + f" :: Humidity: {w['humidity']} %" + f" :: Wind: {w['wind_speed']:.1f} m/s ({w['wind_dir']})" + f" :: Pressure: {w['pressure']:.0f} mmHg" + f" :: Precipitation: {w['precip']} mm"

        return data
