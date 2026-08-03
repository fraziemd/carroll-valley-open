"""Event configuration loading for the JDCVO scoring system.

The event config (event_2026.json) is the single place that defines the
year's rounds, courses, roster file, and publish targets. Players and courses
are loaded from the files it references.
"""

import json
import os

DEFAULT_CONFIG_FILE = 'event_2026.json'


class EventConfig:
    def __init__(self, config_path=DEFAULT_CONFIG_FILE):
        self.base_dir = os.path.dirname(os.path.abspath(config_path)) or '.'
        with open(config_path) as f:
            self.raw = json.load(f)

        with open(self._path(self.raw['players_file'])) as f:
            self.players = json.load(f)['players']
        with open(self._path(self.raw['courses_file'])) as f:
            self.courses = json.load(f)['courses']

        self.event_name = self.raw['event_name']
        self.year = self.raw['year']
        self.rounds = self.raw['rounds']
        self.google_sheet_key = self.raw.get('google_sheet_key') or None
        self.data_dir = self._path(self.raw.get('data_dir', f'data_{self.year}'))

        # 'full' (every stroke the handicap says) or 'capped' (max 1 stroke/
        # hole — the old notebook rule). Defaults to 'full'.
        self.handicap_allocation = self.raw.get('handicap_allocation', 'full')
        if self.handicap_allocation not in ('capped', 'full'):
            raise ValueError(
                f"handicap_allocation must be 'capped' or 'full', got "
                f"{self.handicap_allocation!r} in {config_path}")

    def _path(self, relative):
        return os.path.join(self.base_dir, relative)

    # --- roster helpers -----------------------------------------------------

    def name_to_id(self):
        return {p['name']: pid for pid, p in self.players.items()}

    def id_to_name(self):
        return {pid: p['name'] for pid, p in self.players.items()}

    def handicaps(self):
        return {p['name']: p['handicap'] for p in self.players.values()}

    def teams(self):
        return sorted({p['team'] for p in self.players.values()})

    def partners(self, round_number):
        field = f'round_{round_number}_partner'
        return {p['name']: p[field] for p in self.players.values() if p.get(field)}

    def foursomes(self, round_number):
        field = f'round_{round_number}_foursome'
        return {p['name']: p[field] for p in self.players.values() if p.get(field)}

    # --- round helpers ------------------------------------------------------

    def round_config(self, round_number):
        return self.rounds[str(round_number)]

    def course_holes(self, round_number):
        course = self.round_config(round_number).get('course')
        return self.courses[course]['holes'] if course else None

    def round_numbers(self):
        return sorted(int(k) for k in self.rounds)
