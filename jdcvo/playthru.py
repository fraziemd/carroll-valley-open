"""Requests-based scraper for PlayThru (golfplaythru.com) leaderboards.

The PlayThru leaderboard page is fully server-rendered, so a plain HTTP GET
returns all golfer names and hole-by-hole scores. No browser or Selenium is
required. The parsing logic is a faithful port of the 2025 notebook's
scrape_golf_scores() and produces the same entry schema:

    {'name': str, 'scoring_style': str, 'golfer_id': str,
     'hole_scores': {str(hole): int}, 'total_score': int, 'timestamp': str}

Note: hole_scores keys are STRINGS to match the 2025 saved-JSON format that
the scoring engine and validation tests consume.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
FETCH_TIMEOUT_SECONDS = 30


def fetch_leaderboard_html(url):
    """Fetch the raw HTML of a PlayThru leaderboard page."""
    response = requests.get(url, headers={'User-Agent': USER_AGENT},
                            timeout=FETCH_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def parse_leaderboard(html, scoring_style):
    """Parse PlayThru leaderboard HTML into score entries."""
    soup = BeautifulSoup(html, 'html.parser')
    golfer_elements = soup.find_all('div', class_=re.compile(r'golfer.*click'))

    entries = []
    for golfer_element in golfer_elements:
        golfer_name = golfer_element.get_text(strip=True)

        golfer_id = None
        for cls in golfer_element.get('class', []):
            if cls.startswith('golfer') and 'click' in cls:
                golfer_id = cls.replace('click', '')
                break
        if not golfer_id:
            continue

        scorecard = soup.find('div', class_=re.compile(f'{golfer_id}hide'))
        if not scorecard:
            continue

        hole_scores = {}
        for div in scorecard.find_all('div', style=re.compile(r'width:11%')):
            p_elements = div.find_all('p', class_='text-center')
            if len(p_elements) >= 2:
                hole_num = p_elements[0].get_text(strip=True)
                score = p_elements[1].get_text(strip=True)
                if hole_num.isdigit() and score.isdigit():
                    hole_scores[hole_num] = int(score)

        total_score = 0
        total_element = scorecard.find('p', style=re.compile(r'text-align: right'))
        if total_element:
            match = re.search(r'(\d+)\s+Total', total_element.get_text(strip=True))
            if match:
                total_score = int(match.group(1))

        entries.append({
            'name': golfer_name,
            'scoring_style': scoring_style,
            'golfer_id': golfer_id,
            'hole_scores': hole_scores,
            'total_score': total_score,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        })

    return entries


def scrape(url, scoring_style):
    """Fetch and parse a PlayThru leaderboard. Returns a list of entries."""
    return parse_leaderboard(fetch_leaderboard_html(url), scoring_style)
