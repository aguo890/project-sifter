import os
import requests
import json
from dotenv import load_dotenv
import sqlite3
from playwright.sync_api import sync_playwright
import trafilatura

load_dotenv()  # Loads variables from .env file

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")


def analyze_with_deepseek(job_text, resume_text):
    """Takes job and resume text, returns a JSON analysis from the LLM."""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }

    prompt = f"""
    You are an expert career coach and AI Engineering hiring manager. Analyze the job description and compare it to my resume. Provide a concise, structured analysis in JSON format.

    MY RESUME:
    ---
    {resume_text}
    ---

    JOB DESCRIPTION:
    ---
    {job_text}
    ---

    Based on the comparison, provide a JSON object with ONLY the following keys:
    "job_title", "company_name", "match_score_10", "key_strengths", "potential_gaps", "summary_for_email", "keywords_to_add".
    """

    data = {
        "model": "deepseek-chat",  # Use deepseek-chat for better JSON adherence
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,  # Lower temp for more deterministic, factual output
        "response_format": {"type": "json_object"},
    }

    try:
        response = requests.post(
            "https://api.deepseek.com/chat/completions", headers=headers, json=data
        )
        response.raise_for_status()  # Raise an exception for bad status codes

        # The API wraps the JSON in a larger structure, we need to extract it
        result_content = response.json()["choices"][0]["message"]["content"]
        return json.loads(result_content)

    except requests.exceptions.RequestException as e:
        print(f"Error calling Deepseek API: {e}")
    except json.JSONDecodeError:
        print(f"Error: Failed to decode JSON from API. Response was: {result_content}")

    return None


DATABASE_FILE = "jobs.db"


def setup_database():
    """Creates the database and table if they don't exist."""
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_urls (
            url TEXT PRIMARY KEY,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.commit()
    conn.close()


def is_url_new(url):
    """Checks if a URL is already in our database."""
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT url FROM seen_urls WHERE url = ?", (url,))
    result = c.fetchone()
    conn.close()
    return result is None


def add_url_to_db(url):
    """Adds a new URL to the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO seen_urls (url) VALUES (?)", (url,))
    conn.commit()
    conn.close()


def get_clean_text_from_url(url):
    """Uses Trafilatura to get the main content from a URL."""
    try:
        downloaded = trafilatura.fetch_url(url)
        clean_text = trafilatura.extract(downloaded)
        return clean_text
    except Exception as e:
        print(f"Error extracting text from {url}: {e}")
        return None


def find_new_job_links(target_page_url):
    """Uses Playwright to find all job links on a page and return only new ones."""
    new_links = []
    print(f"Scraping {target_page_url} for job links...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(target_page_url, wait_until="domcontentloaded")

        # !!! IMPORTANT: THIS SELECTOR MUST BE CUSTOMIZED FOR EACH SITE !!!
        # Inspect the job site to find the right selector for job postings.
        # This is an example for a generic site where jobs are in <a> tags
        # and their links contain the word "/jobs/" or "/careers/".
        links = page.locator('a[href*="/jobs/"], a[href*="/careers/"]').all()

        for link_locator in links:
            href = link_locator.get_attribute("href")
            # Resolve relative URLs to absolute URLs
            if href and not href.startswith("http"):
                from urllib.parse import urljoin

                href = urljoin(target_page_url, href)

            if href and is_url_new(href):
                new_links.append(href)

        browser.close()

    # Return unique links
    return list(set(new_links))


if __name__ == "__main__":
    setup_database()

    with open("resume.txt", "r") as f:
        my_resume = f.read()

    TARGET_URL = "https://jobs.cisco.com/jobs/SearchJobs/internship"  # Example, find one that lists jobs

    new_job_urls = find_new_job_links(TARGET_URL)
    print(f"Found {len(new_job_urls)} new job postings.")

    for url in new_job_urls:
        print(f"--- Processing new job: {url} ---")
        job_description = get_clean_text_from_url(url)

        if job_description:
            analysis = analyze_with_deepseek(job_description, my_resume)
            if analysis:
                print("Analysis complete:")
                print(json.dumps(analysis, indent=2))
                add_url_to_db(url)
                print(f"Successfully processed and saved {url} to database.")
