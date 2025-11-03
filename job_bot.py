import json
import os
import re
import requests
from bs4 import BeautifulSoup

# ========= SETTINGS =========

# Read Telegram config from environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  # e.g. "123456789"

SEEN_FILE = "seen_jobs.json"

# Keywords for junior doctor level roles (across specialties)
KEYWORDS = [
    "junior clinical fellow",
    "junior doctor",
    "sho",
    "senior house officer",
    "f1",
    "foundation year 1",
    "f2",
    "foundation year 2",
    "f3",
    "foundation year 3",
    "ct1",
    "core trainee 1",
    "ct2",
    "core trainee 2",
    "st1",
    "specialty trainee 1",
    "st2",
    "specialty trainee 2",
    "trust grade",
]

NHS_SEARCH_URLS = [
    "https://www.jobs.nhs.uk/candidate/search/results?keyword=Junior+clinical+fellow&language=en",
    "https://www.jobs.nhs.uk/candidate/search/results?keyword=junior+doctor&language=en",
]

HEALTHJOBSUK_LIST_URL = "https://www.healthjobsuk.com/job_list/s2"


# ========= UTILS =========
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"nhs": [], "healthjobsuk": []}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f)


def send_telegram(text):
    # Safety check: make sure env vars are set
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram token or chat ID not set. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        resp = requests.post(url, json=data)
        if not resp.ok:
            print("Telegram error:", resp.text)
    except Exception as e:
        print("Telegram send failed:", e)


def format_message(job_url, data):
    lines = [
        f"New Job Found @ {data['source']}",
        "",
        f"Job Link ({job_url})",
        "",
        f"Title: {data['title']}",
        f"Employer: {data['employer']}",
        f"Specialty: {data['specialty']}",
        f"Salary: {data['salary']}",
        f"Location: {data['location']}",
    ]
    return "\n".join(lines)


# ========= NHS JOBS =========
def parse_nhs_job_details(url):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print("Failed to fetch NHS job detail:", e)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else "No title"

    employer = "Not specified"
    emp_heading = soup.find(lambda tag: tag.name in ["h2", "h3"] and "Employer name" in tag.get_text())
    if emp_heading:
        for sib in emp_heading.find_next_siblings():
            if sib.name in ["h2", "h3"]:
                break
            txt = sib.get_text(strip=True)
            if txt:
                employer = txt
                break

    salary = "Not specified"
    sal_heading = soup.find(lambda tag: tag.name in ["h3", "h4"] and "Salary" in tag.get_text())
    if sal_heading:
        for sib in sal_heading.find_next_siblings():
            if sib.name in ["h2", "h3", "h4"]:
                break
            txt = sib.get_text(strip=True)
            if txt:
                salary = txt
                break

    specialty = "Not specified"
    main_area_heading = soup.find(lambda tag: tag.name in ["h3", "h4"] and "Main area" in tag.get_text())
    if main_area_heading:
        for sib in main_area_heading.find_next_siblings():
            if sib.name in ["h2", "h3", "h4"]:
                break
            txt = sib.get_text(strip=True)
            if txt:
                specialty = txt
                break

    location = "Not specified"
    loc_heading = soup.find(lambda tag: tag.name in ["h2", "h3"] and "Job locations" in tag.get_text())
    if loc_heading:
        parts = []
        for sib in loc_heading.find_next_siblings():
            if sib.name in ["h2", "h3"]:
                break
            txt = sib.get_text(strip=True)
            if txt:
                parts.append(txt)
            if len(parts) >= 4:
                break
        if len(parts) >= 2:
            location = f"{parts[-2]}, {parts[-1]}"
        elif parts:
            location = parts[0]

    return {
        "source": "NHS",
        "title": title,
        "employer": employer,
        "specialty": specialty,
        "salary": salary,
        "location": location,
    }


def fetch_nhs_new_jobs(seen):
    base_site = "https://www.jobs.nhs.uk"
    new_jobs = []

    for search_url in NHS_SEARCH_URLS:
        try:
            r = requests.get(search_url, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print("Failed to fetch NHS search:", e)
            continue

        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/candidate/jobadvert/" not in href:
                continue

            full_url = href if href.startswith("http") else base_site + href
            m = re.search(r"/candidate/jobadvert/([^/?]+)", href)
            if not m:
                continue
            job_id = m.group(1)

            if job_id in seen["nhs"]:
                continue

            title_text = a.get_text(" ", strip=True).lower()
            if not any(k in title_text for k in KEYWORDS):
                continue

            details = parse_nhs_job_details(full_url)
            if not details:
                continue

            seen["nhs"].append(job_id)
            new_jobs.append((full_url, details))

    return new_jobs


# ========= HEALTHJOBSUK =========
def parse_trac_label(soup, label_text):
    label_node = soup.find(string=lambda t: isinstance(t, str) and t.strip() == label_text)
    if not label_node:
        return None
    parent = label_node.parent
    for sib in parent.find_next_siblings():
        txt = sib.get_text(strip=True)
        if txt:
            return txt
    return None


def parse_healthjobsuk_job_details(url):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print("Failed to fetch HealthJobsUK job detail:", e)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    title_tag = soup.find("h1") or soup.find("h2")
    title = title_tag.get_text(strip=True) if title_tag else "No title"

    specialty = parse_trac_label(soup, "Main area") or "Not specified"
    employer = parse_trac_label(soup, "Employer") or "Not specified"
    salary = parse_trac_label(soup, "Salary") or "Not specified"
    town = parse_trac_label(soup, "Town")
    location = town or "Not specified"

    return {
        "source": "HealthJobsUK",
        "title": title,
        "employer": employer,
        "specialty": specialty,
        "salary": salary,
        "location": location,
    }


def fetch_healthjobsuk_new_jobs(seen):
    site = "https://www.healthjobsuk.com"

    try:
        r = requests.get(HEALTHJOBSUK_LIST_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print("Failed to fetch HealthJobsUK list:", e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    new_jobs = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("/job/UK/"):
            continue

        text = a.get_text(" ", strip=True).lower()
        if not any(k in text for k in KEYWORDS):
            continue

        m = re.search(r"-v(\d+)", href)
        job_id = m.group(1) if m else href

        if job_id in seen["healthjobsuk"]:
            continue

        full_url = site + href
        details = parse_healthjobsuk_job_details(full_url)
        if not details:
            continue

        seen["healthjobsuk"].append(job_id)
        new_jobs.append((full_url, details))

    return new_jobs


# ========= MAIN =========
def main():
    seen = load_seen()

    nhs_jobs = fetch_nhs_new_jobs(seen)
    hj_jobs = fetch_healthjobsuk_new_jobs(seen)

    all_new = nhs_jobs + hj_jobs

    if not all_new:
        print("No new jobs this run.")
    else:
        for url, data in all_new:
            title = (data.get("title") or "").strip()
            if not title:
                title = "New junior doctor role"
            data["title"] = title

            msg = format_message(url, data)
            print("Sending job:", title)
            send_telegram(msg)

    save_seen(seen)


if __name__ == "__main__":
    main()
