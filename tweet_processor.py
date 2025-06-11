import os
import glob
import json
import sqlite3
from openai import OpenAI
import time
from task_manager import update_task_progress
from auto_tagger import auto_tag_single_tweet

DB_NAME = "tweets.db"
IMAGE_DIR = "images"

def init_db():
    """Initialize database tables"""
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
    conn.commit()
    conn.close()

def insert_tweet(tweet):
    """Insert tweet into database"""
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
        return True
    except Exception as e:
        print(f"Error inserting tweet: {e}")
        return False
    finally:
        conn.close()

def download_images(tweet):
    """Download images for a tweet"""
    import requests
    
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

def embed_tweet_text(tweet_id, text):
    """Create embeddings for tweet text"""
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
    """Store embedding in database"""
    if embedding is None:
        return False
        
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
        return True
    except Exception as e:
        print(f"Error storing embedding: {e}")
        return False
    finally:
        conn.close()

def count_tweets_in_db():
    """Count tweets in database"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM tweets")
    count = c.fetchone()[0]
    conn.close()
    return count

def process_tweets(task_id, clear_db=False):
    """Process tweets from JSON files with progress tracking"""
    # Initialize counters
    processed = 0
    failed = 0
    total_files = 0
    total_tweets = 0
    
    # Ensure directories exist
    os.makedirs(IMAGE_DIR, exist_ok=True)
    
    # Initialize database
    init_db()
    
    # Clear database if requested
    if clear_db:
        update_task_progress(
            task_id, 
            message="Clearing database...",
            current_file="N/A",
            current_tweet="N/A"
        )
        
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("DELETE FROM tweets")
        c.execute("DELETE FROM tweet_embeddings")
        try:
            c.execute("DELETE FROM image_analysis")
        except sqlite3.OperationalError:
            # Table might not exist yet
            pass
        conn.commit()
        conn.close()
    
    # First pass: count total tweets in all files
    update_task_progress(
        task_id, 
        progress=0,
        message="Counting tweets in JSON files...",
        current_file="N/A",
        current_tweet="N/A"
    )
    
    json_files = glob.glob("data/*.json")
    total_files = len(json_files)
    
    for i, fpath in enumerate(json_files):
        try:
            update_task_progress(
                task_id,
                message=f"Counting tweets in file {i+1}/{total_files}",
                current_file=os.path.basename(fpath)
            )
            
            with open(fpath, "r", encoding="utf-8") as f:
                tweets_data = json.load(f)
                total_tweets += len(tweets_data)
        except Exception as e:
            print(f"Error counting tweets in {fpath}: {e}")
    
    update_task_progress(
        task_id,
        message=f"Found {total_tweets} tweets in {total_files} files",
        total_files=total_files,
        total_tweets=total_tweets,
        total=total_tweets
    )
    
    # Second pass: process tweets
    for file_index, fpath in enumerate(json_files):
        file_name = os.path.basename(fpath)
        update_task_progress(
            task_id,
            message=f"Processing file {file_index+1}/{total_files}: {file_name}",
            current_file=file_name,
            total_files=total_files
        )
        
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                tweets_data = json.load(f)
                
                for tweet_index, tweet in enumerate(tweets_data):
                    tweet_id = tweet.get("tweetId", "unknown")
                    update_task_progress(
                        task_id,
                        progress=processed + failed,
                        message=f"Processing tweet {processed+failed+1}/{total_tweets}",
                        current_tweet=tweet_id,
                        processed=processed,
                        failed=failed
                    )
                    
                    try:
                        # Download images
                        local_imgs = download_images(tweet)
                        tweet["localImages"] = local_imgs
                        
                        # Insert tweet record in DB
                        insert_tweet(tweet)
                        
                        # Create and store embedding
                        embedding = embed_tweet_text(tweet_id, tweet.get("tweetText", ""))
                        store_embedding(tweet_id, embedding)
                        
                        # Auto-tag the tweet
                        try:
                            auto_tag_single_tweet(tweet_id)
                        except Exception as e:
                            print(f"Error auto-tagging tweet {tweet_id}: {e}")
                        
                        processed += 1
                        
                        # Add a small delay to avoid overloading and allow UI updates
                        time.sleep(0.01)
                    except Exception as e:
                        print(f"Error processing tweet {tweet_id}: {e}")
                        failed += 1
        except Exception as e:
            print(f"Error processing file {fpath}: {e}")
    
    # Get total tweets in database
    total_in_db = count_tweets_in_db()
    
    # Final update
    update_task_progress(
        task_id,
        progress=total_tweets,
        total=total_tweets,
        message="Processing complete",
        processed=processed,
        failed=failed,
        total_files=total_files,
        total_tweets=total_tweets,
        total_tweets_in_db=total_in_db
    )
    
    return {
        "status": "success",
        "processed": processed,
        "failed": failed,
        "total_files": total_files,
        "total_tweets_in_files": total_tweets,
        "total_tweets_in_db": total_in_db
    }