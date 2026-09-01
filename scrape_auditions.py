#!/usr/bin/env python3
"""
Audition Finder — scrapes Playbill's job board for dance auditions,
pulls out the details that matter (location, dates, who they're seeking,
pay, union status), and builds a browsable dashboard as a single HTML file.

HOW TO USE
----------
1. Fill in YOUR_CRITERIA below (optional, but it's what makes the
   dashboard actually useful — it flags listings worth a closer look).
2. Run:  python3 scrape_auditions.py
3. Open the generated `auditions_dashboard.html` in any browser.

Re-run it whenever you want fresh listings — it fetches the current
board each time and rebuilds the dashboard.
"""

import json
import re
import time
import urllib.request
from urllib.parse import urljoin
from html import unescape
from datetime import datetime

# ---------------------------------------------------------------------------
# YOUR CRITERIA — confirmed with Marley directly.
# Nothing here ever hides a listing — it only affects the RANK/SCORE, so
# everything still shows up, just sorted with the best fits near the top.
# ---------------------------------------------------------------------------
YOUR_CRITERIA = {
    "height_inches": 63,          # 5'3"
    "hair_color": "dark brown",
    "target_age_min": 16,
    "target_age_max": 30,
    "gender": "female",
    "union_status": "open",       # non-union currently, but open to either
    "home_base": "Manhattan, New York, NY",
    "willing_to_travel_for_right_show": True,  # anywhere in the US for the right one
    "prefers_housing_provided": True,
    "minimum_pay": None,           # None = doesn't matter right now
    "dance_styles": ["musical theater", "jazz", "ballet", "hip hop", "tap", "ballroom", "partnering"],
    "other_skills": ["singing"],
    "target_shows_or_choreographers": [],   # e.g. ["Hamilton", "Ebony Williams"]
    "skip_unpaid": True,
    "skip_cruise_ships": True,
    "hide_past_auditions": True,
    "max_days_until_audition": 14,
    "max_days_for_unknown_date_listings": 14,
}

# How many pages of the Performer category board to pull (~50 listings/page)
PAGES_TO_FETCH = 3

BASE_LIST_URL = "https://playbill.com/jobs?category=Performer&page={page}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AuditionFinder/1.0)"}

# Keywords that suggest a listing is dance-relevant (checked in the title)
DANCE_KEYWORDS = [
    "dancer", "dancers", "dance", "choreo", "movers", "ensemble",
    "ecc", "chorus", "swing",
]


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def strip_tags(html_fragment):
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def find_listing_links(list_html):
    """Pull (title, url) pairs for each job card on a listing page."""
    links = []
    for m in re.finditer(r'<a href="(https://playbill\.com/job/[^"]+)"[^>]*>(.*?)</a>', list_html, re.S):
        url, inner = m.group(1), m.group(2)
        title_text = strip_tags(inner)
        if title_text:
            links.append((title_text, url))
    # de-duplicate while preserving order
    seen = set()
    unique = []
    for title, url in links:
        if url not in seen:
            seen.add(url)
            unique.append((title, url))
    return unique


def is_dance_related(title):
    lowered = title.lower()
    return any(kw in lowered for kw in DANCE_KEYWORDS)


def extract_section(text, label, stop_labels):
    """Grab the text between one ALL-CAPS section label and the next."""
    pattern = re.compile(
        re.escape(label) + r"(.*?)(?=" + "|".join(re.escape(s) for s in stop_labels) + r"|$)",
        re.S,
    )
    m = pattern.search(text)
    return strip_tags(m.group(1)) if m else ""


def extract_generic_fields(body, full_desc):
    """
    Pulls gender/age/height/union/salary/date/unpaid signals out of plain
    text. Shared across all sources since the underlying pattern-matching
    doesn't depend on any one site's HTML structure.
    """
    gender = "not specified"
    if re.search(r"all genders", full_desc, re.I):
        gender = "all genders"
    elif re.search(r"\bfemale\b", full_desc, re.I) and not re.search(r"\bmale\b", full_desc, re.I):
        gender = "female"
    elif re.search(r"\bmale\b", full_desc, re.I) and not re.search(r"\bfemale\b", full_desc, re.I):
        gender = "male"

    # Only treat a number range as an age range if the word "age"/"ages"
    # actually appears close by — otherwise this false-positives on
    # rehearsal times ("9-12"), schedule blocks, zip codes, etc. Also
    # sanity-check the numbers fall in a plausible human age range.
    age_range = "not specified"
    age_match = re.search(
        r"ages?\s*(?:range)?\s*[:\-]?\s*(\d{1,2})s?\s*(?:-|to|–)\s*(\d{1,2})s?",
        full_desc, re.I,
    )
    if age_match:
        lo, hi = int(age_match.group(1)), int(age_match.group(2))
        if 1 <= lo <= 99 and lo <= hi <= 99:
            age_range = f"{lo}s–{hi}s"

    height_match = re.search(r"(\d)\'(\d{1,2})\"?\s*(?:-|to|–)\s*(\d)\'(\d{1,2})\"?", full_desc)
    height = height_match.group(0) if height_match else "not specified"
    height_range_inches = None
    if height_match:
        lo = int(height_match.group(1)) * 12 + int(height_match.group(2))
        hi = int(height_match.group(3)) * 12 + int(height_match.group(4))
        height_range_inches = (lo, hi)

    is_unpaid = bool(re.search(r"\bunpaid\b|no pay|non-paying|non paying", body, re.I))

    if re.search(r"\bAEA\b|Equity", body):
        union = "Equity (AEA)"
    elif re.search(r"non-union|non union|nonunion", body, re.I):
        union = "Non-Union"
    else:
        union = "Not stated"

    salary_match = re.search(r"\$[\d,]+(?:\.\d{2})?(?:\s*[-–]\s*\$?[\d,]+(?:\.\d{2})?)?(?:\s*(?:weekly|per week|/wk))?", body)
    salary = salary_match.group(0) if salary_match else "not specified"

    audition_date, audition_date_parsed = extract_audition_date(body)

    return {
        "gender_sought": gender,
        "age_range": age_range,
        "height_range": height,
        "height_range_inches": height_range_inches,
        "union": union,
        "salary": salary,
        "audition_date": audition_date,
        "audition_date_parsed": audition_date_parsed,  # ISO string or None
        "is_unpaid": is_unpaid,
    }


def extract_audition_date(body):
    """
    Finds the audition date in either 'Month Day, Year' (Playbill,
    Dance/NYC) or 'M/D/YYYY' (BroadwayWorld) format. Returns both the
    display string and a parsed date (as an ISO string, or None if no
    date was found or it didn't parse) — the parsed version is what the
    upcoming/passed filtering actually runs on.
    """
    month_match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
        body,
    )
    if month_match:
        try:
            parsed = datetime.strptime(
                f"{month_match.group(1)} {month_match.group(2)} {month_match.group(3)}", "%B %d %Y"
            ).date()
            return month_match.group(0), parsed.isoformat()
        except ValueError:
            pass

    numeric_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", body)
    if numeric_match:
        try:
            parsed = datetime.strptime(numeric_match.group(0), "%m/%d/%Y").date()
            return numeric_match.group(0), parsed.isoformat()
        except ValueError:
            pass

    return "see listing", None


def parse_job_detail(html_doc, title, url):
    text = html_doc

    location = ""
    loc_match = re.search(r'CATEGORY:.*?</h3>\s*(.*?)\s*(?:<h3|<div class="job-details|Job Details)', text, re.S)
    if loc_match:
        location = strip_tags(loc_match.group(1))

    body_match = re.search(r"Job Details(.*?)Related Jobs", text, re.S)
    body = strip_tags(body_match.group(1)) if body_match else strip_tags(text)

    labels_in_order = [
        "DESCRIPTION", "PREPARATION", "LOCATION", "PERSONNEL", "OTHER DATES",
        "OTHER", "SALARY", "DURATION", "HOW TO APPLY",
    ]
    sections = {}
    for i, label in enumerate(labels_in_order):
        stops = labels_in_order[i + 1:] + ["Related Jobs"]
        section_text = extract_section(body, label, stops)
        if section_text:
            sections[label.title()] = section_text

    full_desc = sections.get("Description", body[:1500])
    fields = extract_generic_fields(body, full_desc)

    return {
        "source": "Playbill",
        "title": title,
        "url": url,
        "location": location or "see listing",
        "description_excerpt": full_desc[:600],
        "full_text_for_matching": (body + " " + full_desc).lower(),
        **fields,
    }


def score_job(job, criteria):
    """
    Weighted ranking, not a filter — every listing is scored and everything
    still shows up. Score roughly maps to tiers:
      50+   -> "Strong Match"
      20-49 -> "Worth a Look"
      <20   -> "Long Shot"
    Unpaid listings get their own bottom tier when skip_unpaid is set,
    regardless of score, since that's a hard preference, not a soft one.
    """
    score = 0
    reasons = []
    text = job["full_text_for_matching"]

    # Gender
    gender_pref = (criteria.get("gender") or "").lower()
    sought = job["gender_sought"]
    if gender_pref:
        if sought == "all genders" or sought == "not specified":
            pass  # neutral — could go either way
        elif gender_pref in sought:
            score += 15
            reasons.append("gender match")
        else:
            score -= 30
            reasons.append("gender likely doesn't match")

    # Age — she has a target range (e.g. 16-30), not a single number.
    # We consider it a fit if the listing's stated range overlaps hers at all.
    age_min = criteria.get("target_age_min")
    age_max = criteria.get("target_age_max")
    if age_min is not None and age_max is not None and job["age_range"] != "not specified":
        nums = re.findall(r"\d+", job["age_range"])
        if len(nums) == 2:
            lo, hi = int(nums[0]), int(nums[1])
            if lo <= age_max and hi >= age_min:  # ranges overlap
                score += 15
                reasons.append("age range fits")
            else:
                score -= 8

    # Height
    target_height = criteria.get("height_inches")
    if target_height and job["height_range_inches"]:
        lo, hi = job["height_range_inches"]
        if lo <= target_height <= hi:
            score += 10
            reasons.append("height fits")
        else:
            score -= 10
            reasons.append("height likely outside range")

    # Hair color — only ever a bonus, since most listings won't mention it
    hair = (criteria.get("hair_color") or "").lower()
    if hair and hair in text:
        score += 5
        reasons.append("hair color mentioned")

    # Housing provided — bonus only, since most listings won't mention it
    if criteria.get("prefers_housing_provided") and re.search(r"housing (?:is )?(?:provided|included)", text, re.I):
        score += 8
        reasons.append("housing provided")

    # Location — nudges ranking without penalizing hard, since she'll
    # travel for the right show
    home = (criteria.get("home_base") or "")
    home_city = home.split(",")[-2].strip() if "," in home else home
    if home_city and home_city.lower() in job["location"].lower():
        score += 15
        reasons.append("local to home base")
    elif not criteria.get("willing_to_travel_for_right_show", True):
        score -= 15

    # Dance styles / other skills
    for style in criteria.get("dance_styles", []):
        if style.lower() in text:
            score += 6
            reasons.append(f"{style} listed")
    for skill in criteria.get("other_skills", []):
        if skill.lower() in text:
            score += 8
            reasons.append(f"{skill} valued")

    # Named shows/choreographers/companies to always flag
    for name in criteria.get("target_shows_or_choreographers", []):
        if name.lower() in text:
            score += 25
            reasons.append(f"matches watchlist: {name}")

    # Union — informational only, no scoring impact when she's open to both
    union_pref = criteria.get("union_status", "open")
    if union_pref == "AEA" and job["union"] != "Equity (AEA)":
        score -= 10
    elif union_pref == "non-union" and job["union"] == "Equity (AEA)":
        score -= 5

    is_cruise_ship = bool(re.search(r"cruise ship|virgin voyages|royal caribbean|carnival cruise|norwegian cruise", text, re.I))
    unpaid_flag = job["is_unpaid"] and criteria.get("skip_unpaid")
    cruise_flag = is_cruise_ship and criteria.get("skip_cruise_ships")
    if cruise_flag:
        reasons.append("cruise ship contract")

    if unpaid_flag or cruise_flag:
        tier = "Low Priority (Unpaid)" if unpaid_flag else "Low Priority (Cruise Ship)"
    elif score >= 35:
        tier = "Strong Match"
    elif score >= 10:
        tier = "Worth a Look"
    else:
        tier = "Long Shot"

    return score, tier, reasons


def scrape_playbill():
    all_jobs = []
    for page in range(1, PAGES_TO_FETCH + 1):
        print(f"[Playbill] Fetching listings page {page}...")
        try:
            list_html = fetch(BASE_LIST_URL.format(page=page))
        except Exception as e:
            print(f"  couldn't fetch page {page}: {e}")
            continue
        links = find_listing_links(list_html)
        dance_links = [(t, u) for t, u in links if is_dance_related(t)]
        print(f"  found {len(links)} listings, {len(dance_links)} look dance-related")

        for title, url in dance_links:
            print(f"  fetching: {title[:60]}")
            try:
                detail_html = fetch(url)
            except Exception as e:
                print(f"    skipped ({e})")
                continue
            job = parse_job_detail(detail_html, title, url)
            all_jobs.append(job)
            time.sleep(0.5)  # be polite to Playbill's servers
    return all_jobs


DANCE_NYC_LIST_URLS = [
    "https://www.dance.nyc/for-artists/listings/Category-Auditions",
    "https://www.dance.nyc/for-artists/listings/2/Category-Auditions",
]


def scrape_dance_nyc():
    """
    Dance/NYC's Auditions board — fully public, dedicated entirely to dance,
    no login required. Every listing here is dance-relevant by definition
    (it's the Auditions category), so no keyword filtering needed.
    """
    all_jobs = []
    detail_urls = []
    for list_url in DANCE_NYC_LIST_URLS:
        print(f"[Dance/NYC] Fetching {list_url} ...")
        try:
            list_html = fetch(list_url)
        except Exception as e:
            print(f"  couldn't fetch listing page: {e}")
            continue
        # Links may be absolute (https://www.dance.nyc/...) or relative
        # (/for-artists/listings/...) depending on how the site renders them,
        # and may use single or double quotes — so we accept both here and
        # normalize to a full URL afterward.
        found = re.findall(
            r'''href=['"]((?:https://www\.dance\.nyc)?/for-artists/listings/20\d\d/\d\d/[^'"?]+/)['"]''',
            list_html,
        )
        detail_urls.extend(urljoin("https://www.dance.nyc", u) for u in found)
        if not found:
            print(f"  0 matches on this page — first 300 chars of what was fetched, for debugging:")
            print(f"  {strip_tags(list_html)[:300]!r}")

    seen = set()
    detail_urls = [u for u in detail_urls if not (u in seen or seen.add(u))]
    print(f"[Dance/NYC] {len(detail_urls)} unique listings found")

    for url in detail_urls:
        print(f"  fetching: {url}")
        try:
            detail_html = fetch(url)
        except Exception as e:
            print(f"    skipped ({e})")
            continue

        title_match = re.search(r"<title>(.*?)(?:\s*\|\s*Dance/NYC)?</title>", detail_html, re.S)
        title = strip_tags(title_match.group(1)) if title_match else url

        body_match = re.search(r"<article.*?>(.*?)</article>", detail_html, re.S)
        body = strip_tags(body_match.group(1)) if body_match else strip_tags(detail_html)
        full_desc = body[:1500]
        fields = extract_generic_fields(body, full_desc)

        location_match = re.search(r"Location\s*(.*?)(?:Date|$)", body)
        location = strip_tags(location_match.group(1))[:80] if location_match else "see listing"

        all_jobs.append({
            "source": "Dance/NYC",
            "title": title,
            "url": url,
            "location": location or "see listing",
            "description_excerpt": full_desc[:600],
            "full_text_for_matching": (body + " " + full_desc).lower(),
            **fields,
        })
        time.sleep(0.5)
    return all_jobs


BROADWAYWORLD_LIST_URLS = [
    "https://www.broadwayworld.com/theatre-auditions/",
    "https://www.broadwayworld.com/theatre-auditions/?strt=101&show=100",
]


def scrape_broadwayworld():
    """
    BroadwayWorld's Equity auditions board, sourced directly from Actors'
    Equity Association. Public, no login. We only fetch the detail pages
    for listings whose type mentions 'Dancer' to avoid fetching every
    Equity Principal Actor posting on the board.
    """
    all_jobs = []
    candidate_urls = []
    for list_url in BROADWAYWORLD_LIST_URLS:
        print(f"[BroadwayWorld] Fetching {list_url} ...")
        try:
            list_html = fetch(list_url)
        except Exception as e:
            print(f"  couldn't fetch listing page: {e}")
            continue

        # Links may be absolute or relative, and may use single or double
        # quotes, so we accept both and normalize afterward. Each match
        # also gets a window of surrounding text checked for "Dancer"
        # (the role-type text near each link, e.g. "Equity Ensemble
        # Dancers (All genders)") — checking a bit before AND after the
        # link since we don't know exactly which side that text sits on.
        matches = list(re.finditer(
            r'''href=['"]((?:https://www\.broadwayworld\.com)?/equity-audition/[^'"]+)['"]''',
            list_html,
        ))
        for m in matches:
            url = urljoin("https://www.broadwayworld.com", m.group(1))
            window = list_html[max(0, m.start() - 400):m.end() + 400]
            if re.search(r"dancer", window, re.I):
                candidate_urls.append(url)
        if not matches:
            print(f"  0 audition links matched on this page — first 300 chars of what was fetched, for debugging:")
            print(f"  {strip_tags(list_html)[:300]!r}")

    seen = set()
    candidate_urls = [u for u in candidate_urls if not (u in seen or seen.add(u))]
    print(f"[BroadwayWorld] {len(candidate_urls)} dancer-type listings found")

    for url in candidate_urls:
        print(f"  fetching: {url}")
        try:
            detail_html = fetch(url)
        except Exception as e:
            print(f"    skipped ({e})")
            continue

        title_match = re.search(r"<title>(.*?)(?:\s*\|\s*BroadwayWorld)?</title>", detail_html, re.S)
        title = strip_tags(title_match.group(1)) if title_match else url

        body = strip_tags(detail_html)
        full_desc = body[:1500]
        fields = extract_generic_fields(body, full_desc)

        loc_match = re.search(r"·\s*([A-Za-z .]+,\s*[A-Z]{2}|Video Submission|Virtual)\s*·", body)
        location = loc_match.group(1).strip() if loc_match else "see listing"

        all_jobs.append({
            "source": "BroadwayWorld",
            "title": title,
            "url": url,
            "location": location,
            "description_excerpt": full_desc[:600],
            "full_text_for_matching": (body + " " + full_desc).lower(),
            **fields,
        })
        time.sleep(0.5)
    return all_jobs


SEEN_LISTINGS_FILE = "seen_listings.json"
NEW_WINDOW_DAYS = 3

# When the same audition shows up on more than one site, we keep only one
# copy, in this priority order. Playbill first since it tends to have the
# richest listing detail; BroadwayWorld next since it's Equity-sourced and
# usually well-structured; Dance/NYC last only for tie-breaking purposes.
SOURCE_PRIORITY = {"Playbill": 0, "BroadwayWorld": 1, "Dance/NYC": 2}


def normalize_for_matching(job):
    """
    Builds a loose matching key so the same audition posted on different
    sites (e.g. 'Purple Rain - NYC ECC Dancers (08.31.26)' on Playbill vs
    'PURPLE RAIN' on BroadwayWorld) is recognized as the same listing —
    while NOT merging genuinely separate postings for the same show, like
    a site's own separate 'Dancers' call and 'Singers' call, or a
    Broadway company's call vs. its national Tour call.

    Show name comes from before a ' - ' separator (or the whole title if
    there's none). Role type (dancer/singer) and tour-vs-not are checked
    in the title first, falling back to the full listing text if the
    title alone doesn't say — since some sites put the role type in a
    separate field rather than the headline.
    """
    title = job["title"]
    location = job["location"]
    body_text = job.get("full_text_for_matching", "").lower()
    title_lower = title.lower()

    show_part = title.split(" - ")[0]
    show_part = re.sub(r"\([^)]*\)", " ", show_part)  # drop parenthetical details
    key_title = re.sub(r"[^a-z0-9 ]", " ", show_part.lower())
    key_title = re.sub(r"\s+", " ", key_title).strip()

    def find_role(text):
        if "dancer" in text:
            return "dancer"
        if "singer" in text:
            return "singer"
        return ""

    role = find_role(title_lower) or find_role(body_text)
    variant = "tour" if "tour" in title_lower or "tour" in body_text else ""
    city = location.split(",")[0].strip().lower() if location else ""
    return f"{key_title}|{variant}|{role}|{city}"


def deduplicate_jobs(jobs):
    """
    Collapses listings that appear to be the exact same audition posted on
    more than one site, keeping the copy from whichever site ranks highest
    in SOURCE_PRIORITY. Crucially, this only ever merges entries that come
    from DIFFERENT sources — if a matching-key collision happens between
    two listings from the same single source, they're left alone and both
    kept, since a site posting multiple real, distinct listings that
    happen to share a loose key (rare, but possible) is not the same
    problem as the same posting being duplicated across sites.
    """
    groups = {}
    for job in jobs:
        key = normalize_for_matching(job)
        groups.setdefault(key, []).append(job)

    result = []
    for group in groups.values():
        sources_in_group = set(j["source"] for j in group)
        if len(sources_in_group) == 1:
            result.extend(group)  # same source only — trust its own granularity
        else:
            best_rank = min(SOURCE_PRIORITY.get(j["source"], 9) for j in group)
            result.extend(j for j in group if SOURCE_PRIORITY.get(j["source"], 9) == best_rank)
    return result


def load_seen_dates(path=SEEN_LISTINGS_FILE):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen_dates(seen_dates, path=SEEN_LISTINGS_FILE):
    with open(path, "w") as f:
        json.dump(seen_dates, f, indent=2)


def apply_first_seen_dates(jobs):
    """
    Stamps every job with the date it was first ever found on the board,
    using a small record file that persists between runs. A listing found
    again on a later run keeps its original date rather than getting a
    new one — that's what makes "first appeared" stable over time instead
    of just meaning "today."
    """
    seen_dates = load_seen_dates()
    today = datetime.now().strftime("%Y-%m-%d")
    today_date = datetime.now().date()

    for job in jobs:
        first_seen = seen_dates.get(job["url"])
        if not first_seen:
            first_seen = today
            seen_dates[job["url"]] = today
        job["first_seen"] = first_seen
        seen_date = datetime.strptime(first_seen, "%Y-%m-%d").date()
        job["is_new"] = (today_date - seen_date).days < NEW_WINDOW_DAYS

    save_seen_dates(seen_dates)
    return jobs


def within_audition_window(job, criteria):
    """
    Decides whether a listing stays on the board.

    If it has a real, parseable audition date: drop it once that date has
    passed, and drop it if it's further out than max_days_until_audition.

    If it has NO parseable date (audition_date_parsed is None — sites show
    this as 'see listing' for rolling or invitation-only calls), there's
    no date to filter by, so instead we use its own first-seen date as a
    timer: once it's been sitting on the board for more than
    max_days_for_unknown_date_listings, it gets dropped too, so these
    don't accumulate forever. This means apply_first_seen_dates() must
    run BEFORE this filter, since it relies on job['first_seen'].
    """
    today = datetime.now().date()
    parsed = job.get("audition_date_parsed")

    if not parsed:
        first_seen = job.get("first_seen")
        max_days_unknown = criteria.get("max_days_for_unknown_date_listings")
        if first_seen and max_days_unknown is not None:
            seen_date = datetime.strptime(first_seen, "%Y-%m-%d").date()
            if (today - seen_date).days > max_days_unknown:
                return False
        return True

    audition_date = datetime.strptime(parsed, "%Y-%m-%d").date()

    if criteria.get("hide_past_auditions") and audition_date < today:
        return False

    max_days = criteria.get("max_days_until_audition")
    if max_days is not None and (audition_date - today).days > max_days:
        return False

    return True


def scrape_all():
    all_jobs = []
    all_jobs.extend(scrape_playbill())
    all_jobs.extend(scrape_dance_nyc())
    all_jobs.extend(scrape_broadwayworld())

    all_jobs = deduplicate_jobs(all_jobs)
    all_jobs = apply_first_seen_dates(all_jobs)  # must run before the window filter below
    all_jobs = [j for j in all_jobs if within_audition_window(j, YOUR_CRITERIA)]

    for job in all_jobs:
        score, tier, reasons = score_job(job, YOUR_CRITERIA)
        job["match_score"] = score
        job["match_tier"] = tier
        job["match_reasons"] = reasons
        del job["full_text_for_matching"]

    tier_order = {"Strong Match": 0, "Worth a Look": 1, "Long Shot": 2, "Low Priority (Unpaid)": 3, "Low Priority (Cruise Ship)": 4}
    source_order = {"Playbill": 0, "Dance/NYC": 1, "BroadwayWorld": 2}
    all_jobs.sort(key=lambda j: (source_order.get(j["source"], 9), tier_order.get(j["match_tier"], 9), -j["match_score"]))
    return all_jobs


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Marley's Audition Board</title>
<style>
__CSS__
</style>
</head>
<body>
<header class="marquee">
  <div class="marquee-inner">
    <h1>Marley's Audition Board</h1>
    <p class="subhead">Dance auditions, pulled fresh daily — generated __DATE__</p>
  </div>
</header>

<div class="tabs-row">
  <nav class="source-tabs" id="sourceTabs"></nav>
  <button class="criteria-toggle" id="criteriaToggle">Criteria used ⓘ</button>
</div>

<section class="criteria-panel" id="criteriaPanel" hidden>
  <h2>Criteria currently being used</h2>
  <p class="criteria-note">These are what the ranking above is based on. If any of this should change, tell your person and they can update it.</p>
  <dl class="criteria-grid">
    __CRITERIA_ROWS__
  </dl>
</section>

<div class="controls">
  <input id="search" type="text" placeholder="Search by show, city, choreographer...">
  <select id="unionFilter">
    <option value="">Any union status</option>
    <option value="Equity (AEA)">Equity (AEA) only</option>
    <option value="Non-Union">Non-Union only</option>
  </select>
  <select id="genderFilter">
    <option value="">Any gender call</option>
    <option value="all genders">All genders</option>
    <option value="female">Female</option>
    <option value="male">Male</option>
  </select>
  <select id="tierFilter">
    <option value="">All tiers</option>
    <option value="Strong Match">Strong Match only</option>
    <option value="Worth a Look">Strong Match + Worth a Look</option>
  </select>
  <label class="new-only-toggle"><input type="checkbox" id="newOnlyFilter"> ✦ New only</label>
  <span id="countLabel"></span>
</div>

<p class="legend">Ranked, not filtered — every audition is shown. Cards are grouped by fit so the best matches float to the top, but nothing is hidden.</p>

<main id="board"></main>

<template id="tierHeaderTemplate">
  <h2 class="tier-header"></h2>
</template>

<template id="cardTemplate">
  <article class="card">
    <div class="card-top">
      <h2 class="show-title"></h2>
      <div class="badge-row">
        <span class="badge new-badge" hidden>New within 3 days</span>
        <span class="badge union-badge"></span>
      </div>
    </div>
    <p class="location"></p>
    <dl class="facts">
      <div><dt>Seeking</dt><dd class="gender"></dd></div>
      <div><dt>Ages</dt><dd class="age"></dd></div>
      <div><dt>Height</dt><dd class="height"></dd></div>
      <div><dt>Pay</dt><dd class="salary"></dd></div>
      <div><dt>Audition date</dt><dd class="date"></dd></div>
    </dl>
    <p class="excerpt"></p>
    <div class="match-tags"></div>
    <div class="card-footer">
      <a class="view-link" target="_blank" rel="noopener">View full listing →</a>
      <span class="first-seen"></span>
    </div>
  </article>
</template>

<script>
const JOBS = __JOBS_JSON__;

const board = document.getElementById('board');
const cardTemplate = document.getElementById('cardTemplate');
const tierHeaderTemplate = document.getElementById('tierHeaderTemplate');
const searchInput = document.getElementById('search');
const unionFilter = document.getElementById('unionFilter');
const genderFilter = document.getElementById('genderFilter');
const tierFilter = document.getElementById('tierFilter');
const newOnlyFilter = document.getElementById('newOnlyFilter');
const countLabel = document.getElementById('countLabel');
const sourceTabsEl = document.getElementById('sourceTabs');

const SOURCE_ORDER = ['Playbill', 'Dance/NYC', 'BroadwayWorld'];
const presentSources = new Set(JOBS.map(j => j.source));
const SOURCES = SOURCE_ORDER.filter(s => presentSources.has(s));
let activeSource = SOURCES[0] || '';

function buildTabs() {
  sourceTabsEl.innerHTML = '';
  SOURCES.forEach(source => {
    const count = JOBS.filter(j => j.source === source).length;
    const btn = document.createElement('button');
    btn.className = 'source-tab' + (source === activeSource ? ' active' : '');
    btn.textContent = `${source} (${count})`;
    btn.addEventListener('click', () => {
      activeSource = source;
      buildTabs();
      render();
    });
    sourceTabsEl.appendChild(btn);
  });
}

const TIER_ORDER = ['Strong Match', 'Worth a Look', 'Long Shot', 'Low Priority (Unpaid)', 'Low Priority (Cruise Ship)'];
const TIER_LABELS = {
  'Strong Match': 'Strong Match',
  'Worth a Look': 'Worth a Look',
  'Long Shot': 'Long Shot',
  'Low Priority (Unpaid)': 'Low Priority — Unpaid',
  'Low Priority (Cruise Ship)': 'Low Priority — Cruise Ship',
};

function passesTierFilter(tier, filterValue) {
  if (!filterValue) return true;
  if (filterValue === 'Strong Match') return tier === 'Strong Match';
  if (filterValue === 'Worth a Look') return tier === 'Strong Match' || tier === 'Worth a Look';
  return true;
}

function render() {
  const q = searchInput.value.toLowerCase();
  const union = unionFilter.value;
  const gender = genderFilter.value;
  const tierChoice = tierFilter.value;
  const newOnly = newOnlyFilter.checked;

  const filtered = JOBS.filter(job => {
    if (job.source !== activeSource) return false;
    if (q && !(job.title + job.location + job.description_excerpt).toLowerCase().includes(q)) return false;
    if (union && job.union !== union) return false;
    if (gender && job.gender_sought !== gender) return false;
    if (!passesTierFilter(job.match_tier, tierChoice)) return false;
    if (newOnly && !job.is_new) return false;
    return true;
  });

  board.innerHTML = '';

  TIER_ORDER.forEach(tier => {
    const group = filtered.filter(j => j.match_tier === tier);
    if (group.length === 0) return;

    const headerNode = tierHeaderTemplate.content.cloneNode(true);
    headerNode.querySelector('.tier-header').textContent = `${TIER_LABELS[tier]} (${group.length})`;
    board.appendChild(headerNode);

    group.forEach(job => {
      const node = cardTemplate.content.cloneNode(true);
      node.querySelector('.show-title').textContent = job.title;
      const newBadge = node.querySelector('.new-badge');
      if (job.is_new) {
        newBadge.hidden = false;
      }
      const unionBadge = node.querySelector('.union-badge');
      unionBadge.textContent = job.union;
      unionBadge.classList.toggle('equity', job.union === 'Equity (AEA)');
      unionBadge.classList.toggle('non-union', job.union === 'Non-Union');
      node.querySelector('.location').textContent = job.location;
      node.querySelector('.gender').textContent = job.gender_sought;
      node.querySelector('.age').textContent = job.age_range;
      node.querySelector('.height').textContent = job.height_range;
      node.querySelector('.salary').textContent = job.salary;
      node.querySelector('.date').textContent = job.audition_date;
      node.querySelector('.excerpt').textContent = job.description_excerpt;
      node.querySelector('.view-link').href = job.url;
      node.querySelector('.first-seen').textContent = `First appeared on board: ${job.first_seen}`;
      const tagWrap = node.querySelector('.match-tags');
      job.match_reasons.forEach(r => {
        const tag = document.createElement('span');
        tag.className = 'tag';
        tag.textContent = r;
        tagWrap.appendChild(tag);
      });
      board.appendChild(node);
    });
  });

  countLabel.textContent = filtered.length + (filtered.length === 1 ? ' audition' : ' auditions');
}

[searchInput, unionFilter, genderFilter, tierFilter, newOnlyFilter].forEach(el => {
  el.addEventListener('input', render);
  el.addEventListener('change', render);
});

buildTabs();
render();

const criteriaToggle = document.getElementById('criteriaToggle');
const criteriaPanel = document.getElementById('criteriaPanel');
criteriaToggle.addEventListener('click', () => {
  const isHidden = criteriaPanel.hasAttribute('hidden');
  if (isHidden) {
    criteriaPanel.removeAttribute('hidden');
    criteriaToggle.classList.add('active');
    criteriaToggle.textContent = 'Hide criteria ⓘ';
  } else {
    criteriaPanel.setAttribute('hidden', '');
    criteriaToggle.classList.remove('active');
    criteriaToggle.textContent = 'Criteria used ⓘ';
  }
});
</script>
</body>
</html>
"""

CSS = """
:root {
  --curtain: #5b2a86;
  --curtain-dark: #3d1c5c;
  --spotlight: #b98ee8;
  --paper: #f5f1fa;
  --ink: #241c30;
  --ink-soft: #6a5f7a;
  --card: #fdfbff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: 'Iowan Old Style', 'Georgia', serif;
}
.marquee {
  background: linear-gradient(180deg, var(--curtain) 0%, var(--curtain-dark) 100%);
  color: var(--paper);
  padding: 2.5rem 1.5rem 2rem;
  text-align: center;
  border-bottom: 6px solid var(--spotlight);
}
.marquee h1 {
  font-size: clamp(2rem, 5vw, 3.2rem);
  margin: 0;
  letter-spacing: 0.02em;
  font-weight: 700;
}
.marquee .subhead {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  color: #e8d9c2;
  margin: 0.5rem 0 0;
  font-size: 0.95rem;
}
.tabs-row {
  max-width: 900px;
  margin: 1.2rem auto 0;
  padding: 0 1.5rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.source-tabs {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.source-tab {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.85rem;
  font-weight: 600;
  padding: 0.5rem 1rem;
  border: 1px solid #d3c3e8;
  border-radius: 100px;
  background: var(--card);
  color: var(--ink-soft);
  cursor: pointer;
}
.source-tab.active {
  background: var(--curtain);
  border-color: var(--curtain);
  color: var(--paper);
}
.source-tab:hover:not(.active) { border-color: var(--curtain); color: var(--ink); }
.controls {
  max-width: 900px;
  margin: 1.5rem auto 0;
  padding: 0 1.5rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: center;
  font-family: 'Helvetica Neue', Arial, sans-serif;
}
.controls input[type="text"], .controls select {
  padding: 0.5rem 0.7rem;
  border: 1px solid #d3c3e8;
  border-radius: 4px;
  background: var(--card);
  font-size: 0.9rem;
  color: var(--ink);
}
#search { flex: 1; min-width: 180px; }
#countLabel { margin-left: auto; font-size: 0.85rem; color: var(--ink-soft); }
.legend {
  max-width: 900px;
  margin: 0.6rem auto 0;
  padding: 0 1.5rem;
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.8rem;
  color: var(--ink-soft);
  font-style: italic;
}
.criteria-toggle {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.82rem;
  font-weight: 600;
  padding: 0.5rem 1rem;
  border: 1px dashed #b593dd;
  border-radius: 100px;
  background: transparent;
  color: var(--curtain);
  cursor: pointer;
}
.criteria-toggle:hover { background: var(--card); }
.criteria-toggle.active { background: var(--spotlight); border-style: solid; border-color: var(--spotlight); color: #2e1a47; }
.criteria-panel {
  max-width: 900px;
  margin: 0.6rem auto 0;
  padding: 1rem 1.5rem;
}
.criteria-panel h2 {
  font-size: 1rem;
  margin: 0 0 0.2rem;
  color: var(--curtain);
}
.criteria-note {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.8rem;
  color: var(--ink-soft);
  margin: 0 0 0.8rem;
}
.criteria-grid {
  background: var(--card);
  border: 1px solid #ddd0ee;
  border-radius: 6px;
  padding: 0.9rem 1.2rem;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.6rem 1.5rem;
  margin: 0;
  font-family: 'Helvetica Neue', Arial, sans-serif;
}
.criteria-grid > div { margin: 0; }
.criteria-grid dt {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--ink-soft);
  margin-bottom: 0.1rem;
}
.criteria-grid dd { margin: 0; font-size: 0.92rem; }
main#board {
  max-width: 900px;
  margin: 1rem auto 4rem;
  padding: 0 1.5rem;
  display: grid;
  gap: 1rem;
}
.tier-header {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 1.15rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #fff;
  background: var(--curtain);
  padding: 0.65rem 1rem;
  margin: 1.6rem 0 0.8rem;
  border-radius: 4px;
  position: sticky;
  top: 0;
  z-index: 5;
  box-shadow: 0 2px 6px rgba(0,0,0,0.15);
}
.tier-header:first-child { margin-top: 0; }
.card {
  background: var(--card);
  border: 1px solid #ddd0ee;
  border-left: 4px solid var(--curtain);
  border-radius: 3px;
  padding: 1.1rem 1.3rem;
}
.card-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.75rem;
}
.show-title { font-size: 1.25rem; margin: 0; }
.badge-row { display: flex; gap: 0.4rem; flex-shrink: 0; }
.badge {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.72rem;
  padding: 0.2rem 0.55rem;
  border-radius: 100px;
  background: #ece3f6;
  color: var(--ink-soft);
  white-space: nowrap;
}
.badge.equity { background: var(--spotlight); color: #2e1a47; font-weight: 600; }
.badge.non-union { background: #ddeee0; color: #1f4a30; font-weight: 600; }
.badge.new-badge { background: var(--curtain-dark); color: #f0e4ff; font-weight: 600; }
.new-only-toggle {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.85rem;
  color: var(--ink-soft);
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.location {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  color: var(--ink-soft);
  margin: 0.2rem 0 0.8rem;
  font-size: 0.9rem;
}
dl.facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 0.6rem;
  margin: 0 0 0.8rem;
  font-family: 'Helvetica Neue', Arial, sans-serif;
}
dl.facts div { margin: 0; }
dl.facts dt {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ink-soft);
  margin-bottom: 0.1rem;
}
dl.facts dd { margin: 0; font-size: 0.9rem; }
.excerpt {
  font-size: 0.92rem;
  line-height: 1.5;
  color: #3a3345;
  margin: 0 0 0.8rem;
}
.match-tags { display: flex; gap: 0.4rem; margin-bottom: 0.6rem; flex-wrap: wrap; }
.tag {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.72rem;
  background: #e6dbf5;
  color: #4a2d70;
  padding: 0.15rem 0.5rem;
  border-radius: 100px;
}
.view-link {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.85rem;
  color: var(--curtain);
  text-decoration: none;
  font-weight: 600;
}
.view-link:hover { text-decoration: underline; }
.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.first-seen {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  font-size: 0.72rem;
  color: var(--ink-soft);
}
"""


def format_criteria_rows(criteria):
    """Turns YOUR_CRITERIA into plain-language rows for the dashboard."""

    def height_label(inches):
        if not inches:
            return "not set"
        feet, remainder = divmod(inches, 12)
        return f"{feet}'{remainder}\""

    def yn(value):
        return "yes" if value else "no"

    def age_range_label(criteria):
        lo, hi = criteria.get("target_age_min"), criteria.get("target_age_max")
        if lo is None or hi is None:
            return "not set"
        return f"{lo}–{hi}"

    rows = [
        ("Height", height_label(criteria.get("height_inches"))),
        ("Hair color", criteria.get("hair_color") or "not set"),
        ("Target age range", age_range_label(criteria)),
        ("Gender", criteria.get("gender") or "not set"),
        ("Union status", {"AEA": "Equity (AEA) only", "non-union": "Non-union only", "open": "Open to either"}.get(criteria.get("union_status"), "Open to either")),
        ("Home base", criteria.get("home_base") or "not set"),
        ("Willing to travel for the right show", yn(criteria.get("willing_to_travel_for_right_show"))),
        ("Prefers housing provided", yn(criteria.get("prefers_housing_provided"))),
        ("Minimum pay", criteria.get("minimum_pay") or "no minimum set"),
        ("Dance styles", ", ".join(criteria.get("dance_styles", [])) or "none set"),
        ("Other skills valued", ", ".join(criteria.get("other_skills", [])) or "none set"),
        ("Shows/choreographers always flagged", ", ".join(criteria.get("target_shows_or_choreographers", [])) or "none set"),
        ("Skip unpaid listings", yn(criteria.get("skip_unpaid"))),
        ("Skip cruise ship contracts", yn(criteria.get("skip_cruise_ships"))),
        ("Hide auditions with a passed date", yn(criteria.get("hide_past_auditions"))),
        ("Only show auditions within", f"{criteria.get('max_days_until_audition')} days" if criteria.get("max_days_until_audition") is not None else "no limit"),
        ("Remove undated (\"see listing\") postings after", f"{criteria.get('max_days_for_unknown_date_listings')} days on the board" if criteria.get("max_days_for_unknown_date_listings") is not None else "never"),
    ]
    return "\n    ".join(
        f"<div><dt>{label}</dt><dd>{value}</dd></div>" for label, value in rows
    )


def build_dashboard(jobs, out_path="index.html"):
    html_out = DASHBOARD_TEMPLATE.replace("__CSS__", CSS)
    html_out = html_out.replace("__JOBS_JSON__", json.dumps(jobs))
    html_out = html_out.replace("__DATE__", datetime.now().strftime("%B %d, %Y"))
    html_out = html_out.replace("__CRITERIA_ROWS__", format_criteria_rows(YOUR_CRITERIA))
    with open(out_path, "w") as f:
        f.write(html_out)
    print(f"\nWrote {len(jobs)} listings to {out_path}")
    print("Open that file in your browser to see the dashboard.")


if __name__ == "__main__":
    jobs = scrape_all()
    build_dashboard(jobs)
    with open("auditions_data.json", "w") as f:
        json.dump(jobs, f, indent=2)
