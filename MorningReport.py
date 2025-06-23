"""
Premarket Report Generator Module

A comprehensive module for scraping financial news from Moneycontrol,
generating AI-powered premarket reports, and sending them via email.

Author: Your Name
Created: 2025
"""

import os
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from typing import List, Dict, Optional

import requests
import markdown
from bs4 import BeautifulSoup
from google import genai


class PremarketReportGenerator:
    """
    A class to scrape financial news, generate premarket reports, and send them via email.
    """
    
    def __init__(self, gemini_api_key: str, email_config: Optional[Dict[str, str]] = None):
        """
        Initialize the report generator.
        
        Args:
            gemini_api_key (str): API key for Google Gemini
            email_config (dict, optional): Email configuration with keys:
                - from_email: Sender email address
                - app_password: Gmail app password
                - to_email: Recipient email address
        """
        self.gemini_client = genai.Client(api_key=gemini_api_key)
        self.email_config = email_config or {}
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    def get_article(self, article_url: str) -> Dict[str, Optional[str]]:
        """
        Scrape detailed information from a Moneycontrol article page.

        Args:
            article_url (str): URL of the article

        Returns:
            dict: Dictionary containing article metadata and content

        Raises:
            RuntimeError: If article cannot be fetched or parsed
        """
        try:
            response = requests.get(article_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content.decode("utf-8", "ignore"), "html.parser")
        except Exception as e:
            raise RuntimeError(f"Failed to fetch or parse article: {e}")

        article = {}

        # Extract title
        title_tag = soup.find(attrs={"class": "article_title"})
        article["title"] = title_tag.string.strip() if title_tag and title_tag.string else None

        # Extract summary
        summary_tag = soup.find(attrs={"class": "article_desc"})
        article["summary"] = summary_tag.string.strip() if summary_tag and summary_tag.string else None

        # Extract timestamp
        time_tag = soup.find(attrs={"class": "article_schedule"})
        time_date_string = ""
        if time_tag:
            for element in time_tag.contents:
                if hasattr(element, 'string') and element.string and element.string.strip():
                    time_date_string += element.string.strip()
        article["timestamp"] = time_date_string or None

        # Extract author
        author_tag = soup.select_one(".content_block span")
        article["author"] = author_tag.string.strip() if author_tag and author_tag.string else None

        # Extract image URL
        img_tag = soup.select_one(".article_image img")
        article["img_url"] = img_tag["data-src"] if img_tag and img_tag.has_attr("data-src") else None

        # Extract article content
        content_tags = soup.select(".content_wrapper > p")
        content = [c.get_text(strip=True) for c in content_tags if c.get_text(strip=True)]
        article["content"] = " ".join(content) if content else None

        # Extract tags
        tag_links = soup.select(".tags_first_line > a")
        article["tags"] = [tag.get_text(strip=True).lstrip("#") for tag in tag_links if tag.get_text(strip=True)]

        return article

    def get_news_links(self, pages: int = 10) -> List[str]:
        """
        Scrape news article links from Moneycontrol's 'stocks' section.

        Args:
            pages (int): Number of paginated pages to scrape

        Returns:
            list: Filtered list of unique article URLs from the 'markets' subsection
        """
        base_url = "https://www.moneycontrol.com/news/business/stocks/page-{}/"
        target_prefix = "https://www.moneycontrol.com/news/"
        market_prefix = "https://www.moneycontrol.com/news/business/markets/"

        links = []

        for i in range(1, pages + 1):
            url = base_url.format(i)
            try:
                response = requests.get(url, headers=self.headers, timeout=10)
                response.raise_for_status()
            except requests.RequestException as e:
                print(f"❌ Failed to fetch page {i}: {e}")
                continue

            soup = BeautifulSoup(response.text, 'html.parser')
            containers = soup.find_all(class_='clearfix')

            for container in containers:
                for a in container.find_all('a', href=True):
                    full_url = urljoin(url, a['href'])
                    if full_url.startswith(target_prefix):
                        links.append(full_url)

        # Remove duplicates and filter only 'markets' section links
        filtered_links = [
            link for link in set(links)
            if link.startswith(market_prefix) and link != market_prefix
        ]

        return filtered_links

    def scrape_articles_multithreaded(self, links: List[str], max_workers: int = 10) -> List[Dict]:
        """
        Fetch articles concurrently using multithreading.

        Args:
            links (list): List of URLs to scrape
            max_workers (int): Number of threads to use

        Returns:
            list: List of successfully fetched article results
        """
        all_articles = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_url = {executor.submit(self.get_article, link): link for link in links}
            
            for future in as_completed(future_to_url):
                link = future_to_url[future]
                try:
                    article = future.result()
                    all_articles.append(article)
                    print(f"✅ Completed: {link}")
                except Exception as e:
                    print(f"❌ Failed: {link} | Reason: {e}")
        
        return all_articles

    def parse_article_timestamp(self, timestamp: str) -> Optional[datetime]:
        """
        Parse a Moneycontrol-style timestamp into a datetime object.

        Args:
            timestamp (str): The raw timestamp string from the article

        Returns:
            datetime | None: A datetime object if parsing is successful, else None
        """
        try:
            if not timestamp:
                return None

            # Normalize string
            timestamp = timestamp.strip()
            # Remove trailing time zone and slashes, e.g., "/ 09:30 IST"
            timestamp = re.sub(r"/\s*\d{2}:\d{2}\s*IST", "", timestamp)

            # Extract date and (optional) time
            match = re.search(r'([A-Za-z]+\s+\d{1,2},\s+\d{4})/?\s*(\d{2}:\d{2})?', timestamp)
            if not match:
                return None

            date_part = match.group(1)
            time_part = match.group(2) or "00:00"

            full_string = f"{date_part} {time_part}"
            return datetime.strptime(full_string, "%B %d, %Y %H:%M")
        
        except Exception as e:
            print(f"❌ Error parsing timestamp: {timestamp} | {e}")
            return None

    def filter_recent_articles(self, articles: List[Dict], hours: int = 24) -> List[Dict]:
        """
        Filter articles published in the last `hours` hours.

        Args:
            articles (list): List of article dicts (each containing a 'timestamp' key)
            hours (int): Time range to filter in hours (default 24)

        Returns:
            list: Articles published within the given time window
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = []

        for article in articles:
            dt = self.parse_article_timestamp(article.get("timestamp", ""))
            if dt and dt >= cutoff:
                recent.append(article)

        return recent

    def format_articles_to_string(self, articles: List[Dict]) -> str:
        """
        Takes a list of article dicts and returns a formatted string.

        Args:
            articles (list): List of article dictionaries

        Returns:
            str: Formatted string combining the articles
        """
        result = []
        for article in articles:
            title = article.get("title", "No Title")
            timestamp = article.get("timestamp", "No Timestamp")
            content = article.get("content", "No Content")

            # Skip articles with no content
            if not content or not isinstance(content, str) or content.strip() == "":
                continue

            formatted = f"📰 {title}\n🕒 {timestamp}\n\n{content.strip()}\n{'-' * 80}"
            result.append(formatted)

        return "\n\n".join(result)

    def create_morning_report(self, recent_articles_text: str) -> str:
        """
        Generate a comprehensive premarket report using AI.

        Args:
            recent_articles_text (str): Formatted string of recent articles

        Returns:
            str: Generated premarket report
        """
        prompt = f"""
            You are a financial analyst generating a comprehensive Premarket Report for equity traders in India.

            Based on the following raw market news and updates, write a concise, actionable, and well-structured report suitable to be read by traders before the Indian stock market opens.

            The report should include:
            - 🔔 A crisp summary of global cues (GIFT Nifty, US markets, crude, gold, dollar index, bond yields, Asian markets)
            - 📊 Domestic market setup: Nifty/Sensex close, support/resistance levels, VIX, FII/DII flows, PCR
            - 🔍 Stocks in Focus with reason (news impact, earnings, regulatory update, deals, etc.)
            - 💹 Top Trading Ideas (stock, CMP, buy/sell, target, SL)
            - 📢 Corporate actions or events (dividends, bonus, board meetings, SME listings)
            - 🧾 Bulk/Block deals or fund flow highlights
            - ⚠️ Risks to watch (macro, geopolitical, etc.)
            - ✅ A strategy summary to guide the trading day

            Remove any fluff before the first emoji section like 🔔 or 📊.
            Format the output using emojis and headers to make it engaging and scannable. Keep the tone clear, professional, and trader-friendly.

            Here is the raw input: {recent_articles_text} """

        try:
            print('==================> ', recent_articles_text[:500])
            response = self.gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            return response.text
        except Exception as e:
            raise RuntimeError(f"Failed to generate report: {e}")

    def send_email_report(self, report_text: str, subject: str = "📈 Premarket Report", 
                         to_email: Optional[str] = None) -> None:
        """
        Send the generated report via email.

        Args:
            report_text (str): The report content to send
            subject (str): Email subject line
            to_email (str, optional): Recipient email address

        Raises:
            ValueError: If email configuration is missing
            RuntimeError: If email sending fails
        """
        # Use provided email or fall back to config
        to_email = to_email or self.email_config.get('to_email')
        from_email = self.email_config.get('from_email')
        app_password = self.email_config.get('app_password')

        if not all([from_email, app_password, to_email]):
            raise ValueError("Email configuration is incomplete. Provide from_email, app_password, and to_email.")

        # Convert markdown to HTML
        report_html = markdown.markdown(report_text)

        # Construct the email
        msg = MIMEMultipart("alternative")
        msg['From'] = from_email
        msg['To'] = to_email
        msg['Subject'] = subject

        # Attach HTML version
        msg.attach(MIMEText(report_html, "html"))

        # Send the email via Gmail SMTP
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(from_email, app_password)
                server.send_message(msg)
            print("✅ Email sent successfully.")
        except Exception as e:
            raise RuntimeError(f"Failed to send email: {e}")

    def generate_and_send_report(self, pages: int = 10, hours: int = 24, 
                               subject: str = "📈 Daily Premarket Report",
                               to_email: Optional[str] = None) -> str:
        """
        Complete workflow: scrape articles, generate report, and send via email.

        Args:
            pages (int): Number of pages to scrape
            hours (int): Hours to look back for recent articles
            subject (str): Email subject line
            to_email (str, optional): Recipient email address

        Returns:
            str: The generated report text
        """
        print("🔍 Fetching news links...")
        links = self.get_news_links(pages)
        print(f"📄 Found {len(links)} article links")

        print("📰 Scraping articles...")
        articles = self.scrape_articles_multithreaded(links)
        print(f"✅ Successfully scraped {len(articles)} articles")

        print(f"⏰ Filtering articles from last {hours} hours...")
        recent_articles = self.filter_recent_articles(articles, hours)
        print(f"📊 Found {len(recent_articles)} recent articles")

        if not recent_articles:
            print("⚠️ No recent articles found. Cannot generate report.")
            return ""

        print("📝 Formatting articles...")
        articles_text = self.format_articles_to_string(recent_articles)

        print("🤖 Generating AI report...")
        report = self.create_morning_report(articles_text)

        print("📧 Sending email report...")
        self.send_email_report(report, subject, to_email)

        return report


def main():
    """
    Example usage of the PremarketReportGenerator.
    """
    # Configuration
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    EMAIL_CONFIG = {
        'from_email': os.getenv('FROM_EMAIL', 'mdnishan006@gmail.com'),
        'app_password': os.getenv('APP_PASSWORD', 'mciu itco kbmp mnvd'),
        'to_email': os.getenv('TO_EMAIL', 'mdnishan006@gmail.com')
    }

    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is required")

    # Initialize the generator
    generator = PremarketReportGenerator(
        gemini_api_key=GEMINI_API_KEY,
        email_config=EMAIL_CONFIG
    )

    # Generate and send the report
    try:
        report = generator.generate_and_send_report(
            pages=10,
            hours=24,
            subject="📈 Daily Premarket Report"
        )
        print("🎉 Report generated and sent successfully!")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()