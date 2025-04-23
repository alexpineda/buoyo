/*
 * Twitter Scraper with:
 *  - Single "Scraper" class
 *  - Ability to stop at a particular tweetId (stopAtTweetId)
 *  - Ability to skip a list of already-scraped tweetIds (skipTweetIds)
 *  - Uses tweetId for unique tracking
 *  - Enhanced numeric parsing (K/M)
 *  - REMOVED image/video download; we just keep the remote URLs
 */

class TweetScraper {
  constructor(options = {}) {
    // Options
    this.scrollIntervalMs = options.scrollIntervalMs || 1000;
    this.scrollStep = options.scrollStep || 800;
    this.batchSize = options.batchSize || 100;

    // The tweetId at which to stop. If we see it, we end the scraper
    this.stopAtTweetId = options.stopAtTweetId || null;

    // Skip a list of IDs we already have
    this.skipTweetIds = new Set(options.skipTweetIds || []);

    // Internal state
    this.tweets = [];
    this.processedTweetIds = new Set();
    this.batchIndex = 0;
    this.stuckCount = 0;
    this.previousHeight = 0;

    this.initHud();

    // Mark skipTweetIds as processed so we don't re-capture them
    for (const tid of this.skipTweetIds) {
      this.processedTweetIds.add(tid);
    }

    // Start up
    this.initObserver();
    this.initScroll();
    // Grab any already visible tweets
    this.updateTweets();
  }

  initHud() {
    const hud = document.createElement("div");
    hud.id = "tweet-scraper-hud";
    hud.style.cssText = `
      position:fixed;bottom:8px;left:8px;z-index:99999;
      background:#000a;color:#0f0;font:12px/1 monospace;
      padding:6px 8px;border-radius:4px;pointer-events:none`;
    document.body.appendChild(hud);
    this.hud = hud;
    this.updateHud();
  }

  updateHud() {
    if (!this.hud) return;
    this.hud.textContent = `Batch: ${this.batchIndex}  |  In-batch: ${this.tweets.length}`;
  }

  // MutationObserver for DOM changes
  initObserver() {
    this.observer = new MutationObserver((mutations) => {
      for (let mutation of mutations) {
        // Any media block injected? → touch its ancestor <article> again
        if (mutation.addedNodes.length > 0) {
          mutation.addedNodes.forEach((n) => {
            // Ensure node is an element before calling closest
            if (n.nodeType === Node.ELEMENT_NODE) {
              const art = n.closest?.('article[data-testid="tweet"]');
              // Pass as an array
              if (art) this.updateTweets([art]);
            }
          });
        }
      }
    });
    this.observer.observe(document.body, { childList: true, subtree: true });
  }

  // Periodic scrolling
  initScroll() {
    this.scrollTimer = setInterval(() => {
      const currentHeight = document.documentElement.scrollHeight;
      window.scrollBy(0, this.scrollStep);

      // If stuck, scroll further
      if (currentHeight === this.previousHeight) {
        this.stuckCount++;
        if (this.stuckCount >= 3) {
          window.scrollBy(0, this.scrollStep * 3);
          this.stuckCount = 0;
        }
      } else {
        this.stuckCount = 0;
      }

      this.previousHeight = currentHeight;

      // Optional chunked downloading
      if (this.tweets.length >= this.batchSize) {
        this.downloadTweetsAsJson(this.tweets);
        this.tweets = [];
        this.batchIndex++;
      }
    }, this.scrollIntervalMs);
  }

  // Traverse the DOM for tweet elements
  updateTweets(seedEls = null) {
    // Use seedEls if provided, otherwise query the whole document
    const tweetEls =
      seedEls || document.querySelectorAll('article[data-testid="tweet"]');
    for (const el of tweetEls) {
      const tweetId = this.extractTweetId(el);
      if (!tweetId) continue;

      // If we've already processed this tweetId...
      if (this.processedTweetIds.has(tweetId)) {
        // Check if it's because media arrived late (seedEls implies this)
        // and if we already have the tweet text data stored.
        const already = this.tweets.find((t) => t.tweetId === tweetId);
        if (seedEls && already) {
          const { images, videos } = this.extractMedia(el);
          // Only update if new media was actually found
          if (images.length && !already.images.length) already.images = images;
          if (videos.length && !already.videos.length) already.videos = videos;
        }
        // Skip further processing for already handled tweets
        continue;
      }

      // If we see our stopAtTweetId, end the entire process
      if (tweetId === this.stopAtTweetId) {
        console.log(`Reached stopAtTweetId = ${tweetId}, stopping scraper.`);
        this.cleanup();
        return;
      }

      // Extract tweet info
      const tweetData = this.extractTweetData(el, tweetId);
      if (!tweetData) continue;

      this.enrichWithVideoFromAPI(tweetData);

      this.processedTweetIds.add(tweetId);
      this.tweets.push(tweetData);
      this.updateHud();
      console.log(
        `Captured tweet ${tweetId}, total so far: ${this.tweets.length}`
      );
    }
  }

  // Try to parse the tweetId from the tweet's URL
  extractTweetId(tweetEl) {
    // More robust: the <time> tag's parent <a> always points to the status URL
    const postLink = tweetEl.querySelector("time")?.parentElement;
    if (!postLink || !postLink.href) return null;
    const parts = postLink.href.split("/");
    const candidate = parts[parts.length - 1].split("?")[0];
    return /^\d+$/.test(candidate) ? candidate : null;
  }

  // Extract text, images, videos, interactions, etc.
  extractTweetData(el, tweetId) {
    const authorName = el.querySelector('[data-testid="User-Name"]')?.innerText;
    const timeEl = el.querySelector("time");
    if (!timeEl) return null;
    const timeISO = timeEl.getAttribute("datetime");
    const tweetText = el.querySelector('[data-testid="tweetText"]')?.innerText;

    // Attempt to find the specific link structure for the post URL
    const postLinks = el.querySelectorAll('a[href*="/status/"]');
    let postUrl = "";
    // Find the link whose href ends with the tweetId
    for (const link of postLinks) {
      if (link.href.endsWith(tweetId)) {
        postUrl = link.href;
        break;
      }
    }
    // Fallback if the specific structure isn't found (less reliable)
    if (!postUrl) {
      const genericLink = el.querySelector(
        ".css-175oi2r.r-18u37iz.r-1q142lx a"
      );
      postUrl = genericLink?.href || "";
    }

    const interaction = this.extractInteractionData(el);
    const { images, videos } = this.extractMedia(el);

    return {
      tweetId,
      authorName,
      tweetText,
      timeISO,
      postUrl,
      interaction,
      images,
      videos,
    };
  }

  // Robust media extractor
  extractMedia(root) {
    // imgs with real media urls (within links usually)
    const imgNodes = root.querySelectorAll(
      'a[href*="/photo/"] img[src], [data-testid="tweetPhoto"] img[src]'
    );
    // divs that use background-image (rare but happens in lists)
    const bgNodes = root.querySelectorAll(
      '[data-testid="tweetPhoto"][style*="background-image"]'
    );

    const imgs = [...imgNodes].map((n) => n.src);

    const bgImgs = [...bgNodes]
      .map((n) => {
        const m = n.style.backgroundImage.match(/url\\("?(.*?)"?\\)/); // Escaped quotes
        return m ? m[1] : null;
      })
      .filter(Boolean);

    // Ensure video sources are directly within a video tag
    const videos = [...root.querySelectorAll("video > source[src]")].map(
      (v) => v.src
    );

    // Combine and deduplicate image URLs
    return { images: [...new Set([...imgs, ...bgImgs])], videos };
  }

  // Parse interactions (replies, reposts, likes, bookmarks, views)
  extractInteractionData(el) {
    let replies = 0,
      reposts = 0,
      likes = 0,
      bookmarks = 0,
      views = 0;
    const label = [...el.querySelectorAll("[aria-label]")]
      .map((node) => node.getAttribute("aria-label"))
      .find((txt) => txt && /replies|reposts|likes|bookmarks|views/i.test(txt));

    if (label) {
      replies = this.extractNumberForKeyword(label, "replies");
      reposts = this.extractNumberForKeyword(label, "reposts");
      likes = this.extractNumberForKeyword(label, "likes");
      bookmarks = this.extractNumberForKeyword(label, "bookmarks");
      views = this.extractNumberForKeyword(label, "views");
    }
    return { replies, reposts, likes, bookmarks, views };
  }

  // Example: "123 replies" or "1.2K likes"
  extractNumberForKeyword(text, keyword) {
    const regex = new RegExp(`(\\d+[\\d,\\.]*\\s?[KM]?)\\s+${keyword}`, "i");
    const match = text.match(regex);
    if (!match) return 0;
    return this.parseNumberString(match[1]);
  }

  // Convert "1.2K" -> 1200, "2M" -> 2000000, "10,500" -> 10500
  parseNumberString(str) {
    let normalized = str.replace(/,/g, "");
    if (normalized.toUpperCase().endsWith("K")) {
      return Math.round(parseFloat(normalized) * 1000);
    } else if (normalized.toUpperCase().endsWith("M")) {
      return Math.round(parseFloat(normalized) * 1000000);
    }
    return parseInt(normalized, 10) || 0;
  }

  // Download tweets array as JSON
  downloadTweetsAsJson(tweetsArray) {
    try {
      const jsonData = JSON.stringify(tweetsArray, null, 2);
      const blob = new Blob([jsonData], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "tweets.json";
      link.setAttribute("data-automated-download", "true");
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("JSON Download error:", err);
    }
  }

  // Flush leftovers + teardown
  cleanup() {
    if (this.tweets.length) this.downloadTweetsAsJson(this.tweets);
    clearInterval(this.scrollTimer);
    if (this.observer) {
      this.observer.disconnect();
    }
    this.hud?.remove();
    console.log("Scraper stopped.");
  }
}

const stopAtId = confirm("Enter a tweet ID to stop at (optional):") || null;
var scraper = new TweetScraper({
  stopAtTweetId: stopAtId,
});
