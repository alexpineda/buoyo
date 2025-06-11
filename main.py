import os
import glob
import json
import requests
import sqlite3
from openai import OpenAI

DB_NAME = "tweets.db"
IMAGE_DIR = "images"


############################################
# 1) Initialize database and tables
############################################
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tweets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tweet_id TEXT UNIQUE,
            author_name TEXT,
            tweet_text TEXT,
            time_iso TEXT,
            post_url TEXT,
            replies INTEGER,
            reposts INTEGER,
            likes INTEGER,
            bookmarks INTEGER,
            views INTEGER,
            images TEXT,    -- comma-separated local image paths if you want
            videos TEXT     -- same concept if needed
        )
    """
    )
    # Store vectors as a BLOB or TEXT (base64 or JSON).
    # If using SQLite, BLOB can work. For a real vector DB, you'd use a specialized engine.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tweet_embeddings (
            tweet_id TEXT PRIMARY KEY,
            embedding BLOB
        )
    """
    )
    
    # Tags system for categorizing tweets
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            color TEXT DEFAULT '#007bff',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tweet_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tweet_id TEXT NOT NULL,
            tag_id INTEGER NOT NULL,
            confidence REAL DEFAULT 1.0,
            assigned_by TEXT DEFAULT 'manual',
            assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tweet_id) REFERENCES tweets (tweet_id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags (id) ON DELETE CASCADE,
            UNIQUE(tweet_id, tag_id)
        )
    """
    )
    
    conn.commit()
    conn.close()


############################################
# 2) Insert tweet into database
############################################
def insert_tweet(tweet):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Convert images list -> comma-separated if you want
    images_str = ",".join(tweet.get("localImages", []))
    videos_str = ",".join(tweet.get("localVideos", []))

    try:
        c.execute(
            """
            INSERT OR IGNORE INTO tweets (
                tweet_id, author_name, tweet_text, time_iso, post_url, replies, reposts, 
                likes, bookmarks, views, images, videos
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                tweet["tweetId"],
                tweet.get("authorName", ""),
                tweet.get("tweetText", ""),
                tweet.get("timeISO", ""),
                tweet.get("postUrl", ""),
                tweet["interaction"].get("replies", 0),
                tweet["interaction"].get("reposts", 0),
                tweet["interaction"].get("likes", 0),
                tweet["interaction"].get("bookmarks", 0),
                tweet["interaction"].get("views", 0),
                images_str,
                videos_str,
            ),
        )
        conn.commit()
    finally:
        conn.close()


############################################
# 3) Download tweet images
############################################
def download_images(tweet):
    local_paths = []
    for url in tweet.get("images", []):
        filename = url.split("/")[-1]
        local_path = os.path.join(IMAGE_DIR, filename)
        try:
            r = requests.get(url, timeout=10)
            with open(local_path, "wb") as f:
                f.write(r.content)
            local_paths.append(local_path)
        except Exception as e:
            print(f"Failed to download {url}: {e}")
    return local_paths


############################################
# 4) Create embeddings for tweet text
############################################
def embed_tweet_text(tweet_id, text):
    try:
        # Initialize client
        client = OpenAI()
        # Create embeddings using new API
        response = client.embeddings.create(
            input=text, 
            model="text-embedding-ada-002"
        )
        # Extract embedding data with new response structure
        return response.data[0].embedding
    except Exception as e:
        print(f"Embedding error for tweet {tweet_id}: {e}")
        return None


def store_embedding(tweet_id, embedding):
    if embedding is None:
        return
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Convert embedding (list of floats) to a BLOB or JSON string
    import pickle

    embedding_blob = pickle.dumps(embedding)
    try:
        c.execute(
            """
            INSERT OR REPLACE INTO tweet_embeddings (tweet_id, embedding) VALUES (?, ?)
        """,
            (tweet_id, embedding_blob),
        )
        conn.commit()
    finally:
        conn.close()


############################################
# 5) Search by semantic similarity
############################################
import numpy as np
import pickle


def semantic_search(query, top_k=5, include_images=True):
    # 1) Embed the query
    query_emb = embed_tweet_text("query", query)
    if not query_emb:
        return []

    # Query as numpy array for calculations
    query_emb_np = np.array(query_emb)
    
    # Dictionary to track best score per tweet
    tweet_scores = {}
    
    # 2) Retrieve tweet text embeddings
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Check if deleted column exists in tweets table
    c.execute("PRAGMA table_info(tweets)")
    columns = c.fetchall()
    has_deleted_column = "deleted" in [column[1] for column in columns]
    
    # 2a) Get tweet text embeddings for non-deleted tweets
    if has_deleted_column:
        c.execute("""
            SELECT te.tweet_id, te.embedding 
            FROM tweet_embeddings te
            JOIN tweets t ON te.tweet_id = t.tweet_id
            WHERE t.deleted IS NULL OR t.deleted = 0
        """)
    else:
        c.execute("SELECT tweet_id, embedding FROM tweet_embeddings")
    
    tweet_rows = c.fetchall()
    
    # Calculate similarity for each tweet text
    for tweet_id, emb_blob in tweet_rows:
        emb = pickle.loads(emb_blob)
        emb_np = np.array(emb)
        
        # Calculate cosine similarity
        sim = np.dot(query_emb_np, emb_np) / (
            np.linalg.norm(query_emb_np) * np.linalg.norm(emb_np)
        )
        
        # Store the score (text embeddings get full weight)
        tweet_scores[tweet_id] = sim
    
    # 2b) If enabled, get image description embeddings
    if include_images:
        try:
            if has_deleted_column:
                c.execute("""
                    SELECT ia.tweet_id, ia.embedding 
                    FROM image_analysis ia
                    JOIN tweets t ON ia.tweet_id = t.tweet_id
                    WHERE ia.embedding IS NOT NULL
                    AND (t.deleted IS NULL OR t.deleted = 0)
                """)
            else:
                c.execute("""
                    SELECT tweet_id, embedding FROM image_analysis 
                    WHERE embedding IS NOT NULL
                """)
            
            image_rows = c.fetchall()
            
            # Process each image embedding
            for tweet_id, emb_blob in image_rows:
                if emb_blob:
                    emb = pickle.loads(emb_blob)
                    emb_np = np.array(emb)
                    
                    # Calculate cosine similarity
                    sim = np.dot(query_emb_np, emb_np) / (
                        np.linalg.norm(query_emb_np) * np.linalg.norm(emb_np)
                    )
                    
                    # Update the tweet's score if this image score is higher
                    # This ensures a tweet appears high in results if EITHER
                    # its text OR image content matches well
                    if tweet_id in tweet_scores:
                        tweet_scores[tweet_id] = max(tweet_scores[tweet_id], sim * 0.9)  # Images get 90% weight
                    else:
                        tweet_scores[tweet_id] = sim * 0.9  # Images get 90% weight
        except sqlite3.OperationalError:
            # Image analysis table might not exist yet
            pass
    
    conn.close()
    
    # 3) Convert scores dictionary to list and sort
    scores_list = [(tweet_id, score) for tweet_id, score in tweet_scores.items()]
    scores_list.sort(key=lambda x: x[1], reverse=True)
    
    # 4) Return top K tweet IDs
    top_results = [s[0] for s in scores_list[:top_k]]
    return top_results


############################################
# 6) Putting it all together
############################################
def main():
    os.makedirs(IMAGE_DIR, exist_ok=True)
    init_db()

    # Parse each json file in data/ folder
    for fpath in glob.glob("data/*.json"):
        with open(fpath, "r", encoding="utf-8") as f:
            tweets_data = json.load(f)

        for tweet in tweets_data:
            # Download images
            local_imgs = download_images(tweet)
            tweet["localImages"] = local_imgs

            # Insert tweet record in DB
            insert_tweet(tweet)

            # Optionally embed
            embedding = embed_tweet_text(tweet["tweetId"], tweet.get("tweetText", ""))
            store_embedding(tweet["tweetId"], embedding)

    # Example query
    results = semantic_search("Give me tweets about a gen ai video model")
    print("Search results:", results)


if __name__ == "__main__":
    main()
