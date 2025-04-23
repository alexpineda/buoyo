# Buoyo

Talk to your Twitter/X bookmarks with AI-powered semantic search and image analysis.

## Features

- 🔍 **Semantic Search**: Search your tweets by meaning, not just keywords
- 🖼️ **Image Analysis**: Automatically analyze tweet images with GPT-4o
- 📱 **Web Interface**: Browse and search your tweets with a clean web UI
- 📊 **Data Visualization**: See your most engaging tweets and topics

## Setup

1. Install the required dependencies:

```bash
# Using pip
pip install -e .

# Or using uv
uv install
```

2. Set up your OpenAI API key:

```bash
export OPENAI_API_KEY=your_api_key_here
```

## Tweet Collection

1. Login to Twitter/X and navigate to your bookmarks page
2. Open your browser's developer console (F12 or right-click → Inspect → Console)
3. Copy the entire `bookmark-scraper-v2.js` script and paste it into the console
4. Run the following command to start scraping:

```js
const scraper = new TweetScraper();
```

5. The script will automatically:
   - Scroll through your bookmarks
   - Collect tweet data
   - Periodically download JSON files with your bookmarks
6. To stop the scraping process at any time:

```js
clearInterval(scraper.scrollTimer);
```

7. Move the downloaded JSON files to the `data/` directory in this project

## Running the Flask App

1. Start the Flask application:

```bash
python app.py
```

2. Open your web browser and go to `http://127.0.0.1:5000`

3. Use the web interface to:
   - Process your downloaded tweets into the database
   - Search your tweets semantically
   - Analyze tweet images with GPT-4o
   - Batch process images for multiple tweets

## Options for Tweet Scraper

You can customize the scraper by passing options:

```js
const scraper = new TweetScraper({
  scrollIntervalMs: 1000, // Time between scrolls in ms
  batchSize: 100, // Download after collecting this many tweets,
  stopAtTweetId: "1877160165385904635", // Stop at this tweetId
});
```

## Advanced Usage

- **Custom Embeddings**: The system uses OpenAI's embeddings by default, but you can modify `main.py` to use your preferred embedding model
- **Batch Analysis**: For large collections, use the batch analyze feature to process images in small batches
- **Database Access**: All data is stored in a SQLite database (`tweets.db`), which you can query directly
