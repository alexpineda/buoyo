import os
import glob
import json
import sqlite3
import numpy as np
import pickle
import base64
from flask import Flask, request, jsonify, render_template, send_from_directory
from openai import OpenAI
from main import (
    init_db, 
    insert_tweet, 
    download_images, 
    embed_tweet_text,
    store_embedding,
    semantic_search
)
from image_analysis import (
    analyze_tweet_images,
    get_tweet_with_image_analysis,
    batch_analyze_tweet_images
)
from auto_tagger import AutoTagger, auto_tag_single_tweet, batch_auto_tag_tweets

app = Flask(__name__)

DB_NAME = "tweets.db"
IMAGE_DIR = "images"

# Ensure directories exist
os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

@app.route('/')
def index():
    """Render the main page with the search interface."""
    try:
        return render_template('index.html')
    except Exception as e:
        return f"Error rendering template: {str(e)}"

@app.route('/api/search', methods=['POST'])
def api_search():
    """API endpoint for semantic search of tweets."""
    data = request.json
    query = data.get('query', '')
    top_k = data.get('top_k', 5)
    include_analysis = data.get('include_analysis', False)
    include_images = data.get('include_images', True)  # Default to including images in search
    tag_filters = data.get('tag_filters', [])  # List of tag IDs to filter by
    
    if not query:
        return jsonify({"error": "Query is required"}), 400
    
    # Get top tweet IDs
    tweet_ids = semantic_search(query, top_k, include_images=include_images)
    
    # Filter by tags if specified
    if tag_filters:
        filtered_tweet_ids = []
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        try:
            for tweet_id in tweet_ids:
                # Check if tweet has any of the specified tags
                placeholders = ','.join(['?' for _ in tag_filters])
                c.execute(f"""
                    SELECT COUNT(*) FROM tweet_tags 
                    WHERE tweet_id = ? AND tag_id IN ({placeholders})
                """, [tweet_id] + tag_filters)
                
                if c.fetchone()[0] > 0:
                    filtered_tweet_ids.append(tweet_id)
        finally:
            conn.close()
        
        tweet_ids = filtered_tweet_ids
    
    # Fetch full tweet data
    tweets = []
    
    for tweet_id in tweet_ids:
        if include_analysis:
            # Get tweet with image analysis
            tweet = get_tweet_with_image_analysis(tweet_id)
            # Skip if tweet was not found or deleted
            if tweet.get("error"):
                continue
        else:
            # Get basic tweet data
            conn = sqlite3.connect(DB_NAME)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # Check if deleted column exists
            c.execute("PRAGMA table_info(tweets)")
            columns = c.fetchall()
            has_deleted_column = "deleted" in [column[1] for column in columns]
            
            # Query with appropriate filter based on column existence
            if has_deleted_column:
                c.execute("SELECT * FROM tweets WHERE tweet_id = ? AND (deleted IS NULL OR deleted = 0)", (tweet_id,))
            else:
                c.execute("SELECT * FROM tweets WHERE tweet_id = ?", (tweet_id,))
                
            tweet_row = c.fetchone()
            
            if not tweet_row:
                continue
                
            tweet = dict(tweet_row)
            
            # Process image paths if any
            if tweet.get('images'):
                image_paths = tweet['images'].split(',')
                tweet['image_urls'] = [f'/images/{os.path.basename(path)}' for path in image_paths if path]
            
            conn.close()
        
        tweets.append(tweet)
    
    return jsonify({"results": tweets})

@app.route('/images/<path:filename>')
def serve_image(filename):
    """Serve images from the images directory."""
    return send_from_directory(IMAGE_DIR, filename)

@app.route('/api/process', methods=['POST'])
def start_process_tweets():
    """Start a background task to process tweets."""
    from task_manager import BackgroundTask, generate_task_id
    from tweet_processor import process_tweets
    
    data = request.json or {}
    clear_db = data.get('clear_db', False)
    
    # Create and start a background task
    task_id = generate_task_id()
    task = BackgroundTask(
        task_id=task_id,
        task_type="process_tweets",
        func=process_tweets,
        clear_db=clear_db
    )
    task.start()
    
    # Return the task ID so the client can poll for status
    return jsonify({
        "status": "started",
        "task_id": task_id,
        "message": "Tweet processing started in the background"
    })

@app.route('/api/tasks/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """Get the status of a background task."""
    from task_manager import get_task_status
    
    status = get_task_status(task_id)
    if status is None:
        return jsonify({"error": "Task not found"}), 404
    
    return jsonify(status)
    
@app.route('/api/topics', methods=['GET'])
def get_topics():
    """Get topic analysis of tweets."""
    from topic_analysis import analyze_topics
    
    # Get the number of topics from query parameter, default to 10
    num_topics = request.args.get('num_topics', 10, type=int)
    
    # Only analyze non-deleted tweets
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Check if deleted column exists
    c.execute("PRAGMA table_info(tweets)")
    columns = c.fetchall()
    has_deleted_column = "deleted" in [column[1] for column in columns]
    
    # Set the filter condition based on deleted column existence
    deleted_filter = ""
    if has_deleted_column:
        deleted_filter = "AND (deleted IS NULL OR deleted = 0)"
    
    conn.close()
    
    topics = analyze_topics(num_topics=num_topics, deleted_filter=deleted_filter)
    return jsonify(topics)

@app.route('/api/topics/visualization', methods=['GET'])
def get_topic_visualization():
    """Get topic visualization data."""
    from topic_analysis import get_topic_visualization_data
    
    # Only include non-deleted tweets
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Check if deleted column exists
    c.execute("PRAGMA table_info(tweets)")
    columns = c.fetchall()
    has_deleted_column = "deleted" in [column[1] for column in columns]
    
    # Set the filter condition based on deleted column existence
    deleted_filter = ""
    if has_deleted_column:
        deleted_filter = "AND (deleted IS NULL OR deleted = 0)"
    
    conn.close()
    
    visualization_data = get_topic_visualization_data(deleted_filter=deleted_filter)
    return jsonify(visualization_data)
    
@app.route('/api/tweets/<tweet_id>', methods=['DELETE'])
def delete_tweet(tweet_id):
    """Soft delete a tweet from the database by marking it as deleted."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        # First, check if tweets table has a deleted column
        c.execute("PRAGMA table_info(tweets)")
        columns = c.fetchall()
        column_names = [column[1] for column in columns]
        
        # Add deleted column if it doesn't exist
        if "deleted" not in column_names:
            c.execute("ALTER TABLE tweets ADD COLUMN deleted INTEGER DEFAULT 0")
            conn.commit()
        
        # Mark tweet as deleted instead of actually deleting it
        c.execute("UPDATE tweets SET deleted = 1 WHERE tweet_id = ?", (tweet_id,))
        
        # Commit changes
        conn.commit()
        
        # Check if update was successful
        if c.rowcount > 0:
            return jsonify({"status": "success", "message": f"Tweet {tweet_id} deleted successfully"})
        else:
            return jsonify({"status": "warning", "message": f"Tweet {tweet_id} not found"}), 404
            
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/analyze-images', methods=['POST'])
def api_analyze_images():
    """Analyze images for a specific tweet using GPT-4o."""
    data = request.json
    tweet_id = data.get('tweet_id')
    
    if not tweet_id:
        return jsonify({"error": "Tweet ID is required"}), 400
    
    # Check if the tweet is deleted
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        # Check if deleted column exists
        c.execute("PRAGMA table_info(tweets)")
        columns = c.fetchall()
        has_deleted_column = "deleted" in [column[1] for column in columns]
        
        if has_deleted_column:
            c.execute("SELECT deleted FROM tweets WHERE tweet_id = ?", (tweet_id,))
            result = c.fetchone()
            if result and result[0] == 1:
                return jsonify({"error": "Cannot analyze images for a deleted tweet"}), 400
    finally:
        conn.close()
    
    # Analyze images for this tweet
    result = analyze_tweet_images(tweet_id)
    return jsonify(result)

@app.route('/api/batch-analyze-images', methods=['POST'])
def api_batch_analyze():
    """Run batch analysis of tweet images."""
    data = request.json
    limit = data.get('limit', 10)
    
    # Check if deleted column exists to make sure we only analyze non-deleted tweets
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        c.execute("PRAGMA table_info(tweets)")
        columns = c.fetchall()
        has_deleted_column = "deleted" in [column[1] for column in columns]
        
        # Create a filter for non-deleted tweets
        deleted_filter = ""
        if has_deleted_column:
            deleted_filter = "AND (t.deleted IS NULL OR t.deleted = 0)"
    finally:
        conn.close()
    
    # Run batch analysis
    result = batch_analyze_tweet_images(limit, deleted_filter)
    return jsonify(result)

@app.route('/api/tweet/<tweet_id>', methods=['GET'])
def get_tweet(tweet_id):
    """Get a single tweet with its image analysis."""
    tweet = get_tweet_with_image_analysis(tweet_id)
    
    # Check if tweet is deleted
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        # Check if deleted column exists
        c.execute("PRAGMA table_info(tweets)")
        columns = c.fetchall()
        has_deleted_column = "deleted" in [column[1] for column in columns]
        
        if has_deleted_column:
            c.execute("SELECT deleted FROM tweets WHERE tweet_id = ?", (tweet_id,))
            result = c.fetchone()
            if result and result[0] == 1:
                return jsonify({"error": "Tweet not found or has been deleted"}), 404
    except Exception as e:
        print(f"Error checking tweet deletion status: {e}")
    finally:
        conn.close()
        
    return jsonify(tweet)

@app.route('/api/stats')
def get_stats():
    """Get database statistics."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Check if deleted column exists
    c.execute("PRAGMA table_info(tweets)")
    columns = c.fetchall()
    has_deleted_column = "deleted" in [column[1] for column in columns]
    
    # Count active tweets (not deleted)
    if has_deleted_column:
        c.execute("SELECT COUNT(*) FROM tweets WHERE deleted IS NULL OR deleted = 0")
    else:
        c.execute("SELECT COUNT(*) FROM tweets")
    tweet_count = c.fetchone()[0]
    
    # Count deleted tweets if column exists
    deleted_count = 0
    if has_deleted_column:
        c.execute("SELECT COUNT(*) FROM tweets WHERE deleted = 1")
        deleted_count = c.fetchone()[0]
    
    # Count embeddings
    c.execute("SELECT COUNT(*) FROM tweet_embeddings")
    embedding_count = c.fetchone()[0]
    
    # Count tweets with images (active only)
    if has_deleted_column:
        c.execute("SELECT COUNT(*) FROM tweets WHERE images != '' AND (deleted IS NULL OR deleted = 0)")
    else:
        c.execute("SELECT COUNT(*) FROM tweets WHERE images != ''")
    with_images = c.fetchone()[0]
    
    # Count analyzed images
    try:
        if has_deleted_column:
            c.execute("""
                SELECT COUNT(DISTINCT i.tweet_id) 
                FROM image_analysis i
                JOIN tweets t ON i.tweet_id = t.tweet_id
                WHERE t.deleted IS NULL OR t.deleted = 0
            """)
        else:
            c.execute("SELECT COUNT(DISTINCT tweet_id) FROM image_analysis")
        analyzed_count = c.fetchone()[0]
    except sqlite3.OperationalError:
        # Table might not exist yet
        analyzed_count = 0
    
    conn.close()
    
    stats = {
        "total_tweets": tweet_count,
        "tweets_with_embeddings": embedding_count,
        "tweets_with_images": with_images,
        "tweets_with_image_analysis": analyzed_count
    }
    
    # Add deleted count if available
    if has_deleted_column:
        stats["soft_deleted_tweets"] = deleted_count
    
    return jsonify(stats)

# ============================================
# Auto-Tagging API Endpoints
# ============================================

@app.route('/api/auto-tag/<tweet_id>', methods=['POST'])
def auto_tag_tweet(tweet_id):
    """Auto-tag a single tweet using AI analysis."""
    try:
        assigned_tags = auto_tag_single_tweet(tweet_id)
        
        if assigned_tags:
            return jsonify({
                "status": "success",
                "tweet_id": tweet_id,
                "tags_assigned": assigned_tags,
                "count": len(assigned_tags)
            })
        else:
            return jsonify({
                "status": "success",
                "tweet_id": tweet_id,
                "tags_assigned": [],
                "count": 0,
                "message": "No suitable tags identified for this tweet"
            })
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/auto-tag/batch', methods=['POST'])
def batch_auto_tag():
    """Batch auto-tag multiple tweets."""
    data = request.json or {}
    limit = data.get('limit', 50)
    
    if limit > 200:
        return jsonify({"error": "Limit cannot exceed 200 tweets"}), 400
    
    try:
        # Store progress in a simple way (could be enhanced with proper task queue)
        progress_data = {"current": 0, "total": 0, "tweet_id": ""}
        
        def progress_callback(current, total, tweet_id):
            progress_data.update({"current": current, "total": total, "tweet_id": tweet_id})
        
        stats = batch_auto_tag_tweets(limit, progress_callback)
        
        return jsonify({
            "status": "success",
            "stats": stats
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/auto-tag/untagged-count', methods=['GET'])
def get_untagged_count():
    """Get count of tweets without tags."""
    try:
        tagger = AutoTagger()
        untagged_tweets = tagger.get_untagged_tweets(1000)  # Get up to 1000 to count
        
        return jsonify({
            "untagged_count": len(untagged_tweets),
            "has_untagged": len(untagged_tweets) > 0
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================
# Tag Management API Endpoints
# ============================================

@app.route('/api/tags', methods=['GET'])
def get_tags():
    """Get all tags with usage counts."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        c.execute("""
            SELECT t.id, t.name, t.description, t.color, t.created_at,
                   COUNT(tt.tweet_id) as tweet_count
            FROM tags t
            LEFT JOIN tweet_tags tt ON t.id = tt.tag_id
            GROUP BY t.id, t.name, t.description, t.color, t.created_at
            ORDER BY t.name
        """)
        
        tags = []
        for row in c.fetchall():
            tags.append({
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "color": row[3],
                "created_at": row[4],
                "tweet_count": row[5]
            })
            
        return jsonify(tags)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/tags', methods=['POST'])
def create_tag():
    """Create a new tag."""
    data = request.json
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    color = data.get('color', '#007bff')
    
    if not name:
        return jsonify({"error": "Tag name is required"}), 400
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        c.execute("""
            INSERT INTO tags (name, description, color) 
            VALUES (?, ?, ?)
        """, (name, description, color))
        
        tag_id = c.lastrowid
        conn.commit()
        
        return jsonify({
            "id": tag_id,
            "name": name,
            "description": description,
            "color": color,
            "tweet_count": 0
        }), 201
        
    except sqlite3.IntegrityError:
        return jsonify({"error": "Tag name already exists"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/tags/<int:tag_id>', methods=['PUT'])
def update_tag(tag_id):
    """Update an existing tag."""
    data = request.json
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    color = data.get('color', '#007bff')
    
    if not name:
        return jsonify({"error": "Tag name is required"}), 400
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        c.execute("""
            UPDATE tags 
            SET name = ?, description = ?, color = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (name, description, color, tag_id))
        
        if c.rowcount == 0:
            return jsonify({"error": "Tag not found"}), 404
            
        conn.commit()
        return jsonify({"message": "Tag updated successfully"})
        
    except sqlite3.IntegrityError:
        return jsonify({"error": "Tag name already exists"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/tags/<int:tag_id>', methods=['DELETE'])
def delete_tag(tag_id):
    """Delete a tag and all its associations."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        # Check if tag exists
        c.execute("SELECT name FROM tags WHERE id = ?", (tag_id,))
        if not c.fetchone():
            return jsonify({"error": "Tag not found"}), 404
        
        # Delete tag (CASCADE will remove tweet_tags entries)
        c.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        conn.commit()
        
        return jsonify({"message": "Tag deleted successfully"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/tweets/<tweet_id>/tags', methods=['GET'])
def get_tweet_tags(tweet_id):
    """Get all tags for a specific tweet."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        c.execute("""
            SELECT t.id, t.name, t.description, t.color, tt.confidence, tt.assigned_by, tt.assigned_at
            FROM tags t
            JOIN tweet_tags tt ON t.id = tt.tag_id
            WHERE tt.tweet_id = ?
            ORDER BY t.name
        """, (tweet_id,))
        
        tags = []
        for row in c.fetchall():
            tags.append({
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "color": row[3],
                "confidence": row[4],
                "assigned_by": row[5],
                "assigned_at": row[6]
            })
            
        return jsonify(tags)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/tweets/<tweet_id>/tags', methods=['POST'])
def add_tweet_tag(tweet_id):
    """Add a tag to a tweet."""
    data = request.json
    tag_id = data.get('tag_id')
    confidence = data.get('confidence', 1.0)
    assigned_by = data.get('assigned_by', 'manual')
    
    if not tag_id:
        return jsonify({"error": "Tag ID is required"}), 400
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        # Check if tweet exists
        c.execute("SELECT tweet_id FROM tweets WHERE tweet_id = ?", (tweet_id,))
        if not c.fetchone():
            return jsonify({"error": "Tweet not found"}), 404
        
        # Check if tag exists
        c.execute("SELECT id FROM tags WHERE id = ?", (tag_id,))
        if not c.fetchone():
            return jsonify({"error": "Tag not found"}), 404
        
        # Add tag to tweet
        c.execute("""
            INSERT INTO tweet_tags (tweet_id, tag_id, confidence, assigned_by)
            VALUES (?, ?, ?, ?)
        """, (tweet_id, tag_id, confidence, assigned_by))
        
        conn.commit()
        return jsonify({"message": "Tag added to tweet successfully"}), 201
        
    except sqlite3.IntegrityError:
        return jsonify({"error": "Tweet already has this tag"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/tweets/<tweet_id>/tags/<int:tag_id>', methods=['DELETE'])
def remove_tweet_tag(tweet_id, tag_id):
    """Remove a tag from a tweet."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        c.execute("""
            DELETE FROM tweet_tags 
            WHERE tweet_id = ? AND tag_id = ?
        """, (tweet_id, tag_id))
        
        if c.rowcount == 0:
            return jsonify({"error": "Tag assignment not found"}), 404
            
        conn.commit()
        return jsonify({"message": "Tag removed from tweet successfully"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    # Initialize database on startup
    init_db()
    app.run(debug=True)