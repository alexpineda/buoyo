import os
import base64
import sqlite3
import json
import pickle
from openai import OpenAI

# Initialize OpenAI client
client = OpenAI()

def analyze_tweet_images(tweet_id):
    """
    Analyze images associated with a tweet using GPT-4o.
    Updates the database with the image descriptions.
    
    Args:
        tweet_id: The ID of the tweet to analyze
    
    Returns:
        dict: Analysis results including image descriptions
    """
    # Connect to database
    conn = sqlite3.connect("tweets.db")
    c = conn.cursor()
    
    # Get image paths for this tweet
    c.execute("SELECT images FROM tweets WHERE tweet_id = ?", (tweet_id,))
    result = c.fetchone()
    
    if not result or not result[0]:
        conn.close()
        return {"error": "No images found for this tweet"}
    
    image_paths = result[0].split(',')
    
    # Create analysis_results table if it doesn't exist
    c.execute("""
    CREATE TABLE IF NOT EXISTS image_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tweet_id TEXT,
        image_path TEXT,
        description TEXT,
        embedding BLOB,
        UNIQUE(tweet_id, image_path)
    )
    """)
    conn.commit()
    
    analysis_results = []
    
    # Process each image
    for image_path in image_paths:
        if not image_path:
            continue
            
        # Check if analysis already exists
        c.execute(
            "SELECT description FROM image_analysis WHERE tweet_id = ? AND image_path = ?", 
            (tweet_id, image_path)
        )
        existing = c.fetchone()
        
        if existing and existing[0]:
            analysis_results.append({
                "image_path": image_path,
                "description": existing[0]
            })
            continue
        
        # Read image and encode as base64
        try:
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
                
            # Call GPT-4o for image analysis
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an assistant specialized in analyzing images. Describe what you see in the image in a concise but comprehensive paragraph."},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Describe what's in this image in detail."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]}
                ],
                max_tokens=300
            )
            
            # Extract description
            description = response.choices[0].message.content
            
            # Create embedding for the description
            embedding = None
            try:
                # Use the same OpenAI model for consistency with tweet text embeddings
                embedding_response = client.embeddings.create(
                    input=description,
                    model="text-embedding-ada-002"
                )
                embedding = embedding_response.data[0].embedding
                
                # Serialize embedding
                embedding_blob = pickle.dumps(embedding) if embedding else None
            except Exception as e:
                print(f"Error creating embedding for image analysis: {e}")
                embedding_blob = None
            
            # Store in database with embedding
            c.execute(
                "INSERT OR REPLACE INTO image_analysis (tweet_id, image_path, description, embedding) VALUES (?, ?, ?, ?)",
                (tweet_id, image_path, description, embedding_blob)
            )
            conn.commit()
            
            analysis_results.append({
                "image_path": image_path,
                "description": description
            })
            
        except Exception as e:
            print(f"Error analyzing image {image_path}: {e}")
            analysis_results.append({
                "image_path": image_path,
                "error": str(e)
            })
    
    conn.close()
    
    return {
        "tweet_id": tweet_id,
        "analysis": analysis_results
    }

def get_tweet_with_image_analysis(tweet_id):
    """
    Get a tweet with its image analysis data.
    
    Args:
        tweet_id: The ID of the tweet
        
    Returns:
        dict: Tweet data with image analysis
    """
    conn = sqlite3.connect("tweets.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Check if deleted column exists
    c.execute("PRAGMA table_info(tweets)")
    columns = c.fetchall()
    has_deleted_column = "deleted" in [column[1] for column in columns]
    
    # Get tweet data, excluding deleted tweets
    if has_deleted_column:
        c.execute("SELECT * FROM tweets WHERE tweet_id = ? AND (deleted IS NULL OR deleted = 0)", (tweet_id,))
    else:
        c.execute("SELECT * FROM tweets WHERE tweet_id = ?", (tweet_id,))
        
    tweet_row = c.fetchone()
    
    if not tweet_row:
        conn.close()
        return {"error": "Tweet not found or has been deleted"}
    
    tweet = dict(tweet_row)
    
    # Get image analysis
    c.execute(
        "SELECT image_path, description FROM image_analysis WHERE tweet_id = ?", 
        (tweet_id,)
    )
    image_analysis = [dict(row) for row in c.fetchall()]
    
    # Add image_urls for frontend display
    if tweet.get('images'):
        image_paths = tweet['images'].split(',')
        tweet['image_urls'] = [f'/images/{os.path.basename(path)}' for path in image_paths if path]
    
    # Add image analysis to tweet
    tweet['image_analysis'] = image_analysis
    
    conn.close()
    return tweet

def batch_analyze_tweet_images(limit=10, deleted_filter=""):
    """
    Analyze images for tweets that haven't been analyzed yet.
    
    Args:
        limit: Maximum number of tweets to process
        deleted_filter: SQL filter to exclude deleted tweets
        
    Returns:
        dict: Summary of the batch analysis
    """
    # Connect to database
    conn = sqlite3.connect("tweets.db")
    c = conn.cursor()
    
    # Create the image_analysis table if it doesn't exist
    c.execute("""
    CREATE TABLE IF NOT EXISTS image_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tweet_id TEXT,
        image_path TEXT,
        description TEXT,
        embedding BLOB,
        UNIQUE(tweet_id, image_path)
    )
    """)
    conn.commit()
    
    # Count total tweets with images that need analysis, excluding deleted
    c.execute(f"""
    SELECT COUNT(*) FROM tweets t
    WHERE t.images != ''
    {deleted_filter}
    AND NOT EXISTS (
        SELECT 1 FROM image_analysis ia 
        WHERE ia.tweet_id = t.tweet_id
    )
    """)
    total_remaining = c.fetchone()[0]
    
    # Count total tweets that already have analysis
    try:
        if deleted_filter:
            c.execute(f"""
            SELECT COUNT(DISTINCT ia.tweet_id) 
            FROM image_analysis ia
            JOIN tweets t ON ia.tweet_id = t.tweet_id
            WHERE 1=1 {deleted_filter}
            """)
        else:
            c.execute("""
            SELECT COUNT(DISTINCT tweet_id) FROM image_analysis
            """)
        total_analyzed = c.fetchone()[0]
    except sqlite3.OperationalError:
        # Table might not exist yet
        total_analyzed = 0
    
    # Find tweets with images that haven't been analyzed yet, excluding deleted
    c.execute(f"""
    SELECT t.tweet_id, t.images FROM tweets t
    WHERE t.images != ''
    {deleted_filter}
    AND NOT EXISTS (
        SELECT 1 FROM image_analysis ia 
        WHERE ia.tweet_id = t.tweet_id
    )
    LIMIT ?
    """, (limit,))
    
    tweets_to_analyze = c.fetchall()
    conn.close()
    
    results = {
        "total_processed": 0,
        "successful": 0,
        "failed": 0,
        "tweet_ids": [],
        "total_remaining": total_remaining,
        "total_analyzed": total_analyzed,
        "total_tweets_with_images": total_analyzed + total_remaining
    }
    
    for tweet_id, _ in tweets_to_analyze:
        try:
            analyze_tweet_images(tweet_id)
            results["successful"] += 1
            results["tweet_ids"].append(tweet_id)
        except Exception as e:
            print(f"Error processing tweet {tweet_id}: {e}")
            results["failed"] += 1
        
        results["total_processed"] += 1
    
    # Update the counts after processing
    results["total_analyzed"] += results["successful"]
    results["total_remaining"] -= results["successful"]
    
    # Print debug info to server console
    print("Batch analyze results:", results)
    
    return results