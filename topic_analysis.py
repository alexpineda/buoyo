import sqlite3
import pickle
import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from collections import Counter
from openai import OpenAI

DB_NAME = "tweets.db"

def get_all_embeddings(deleted_filter=""):
    """Get all tweet text and image embeddings from database"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Get tweet text embeddings with optional filter for deleted tweets
    if deleted_filter:
        c.execute(f"""
            SELECT te.tweet_id, te.embedding 
            FROM tweet_embeddings te
            JOIN tweets t ON te.tweet_id = t.tweet_id
            WHERE 1=1 {deleted_filter}
        """)
    else:
        c.execute("SELECT tweet_id, embedding FROM tweet_embeddings")
    
    tweet_embeddings = []
    tweet_ids = []
    
    for tweet_id, emb_blob in c.fetchall():
        embedding = pickle.loads(emb_blob)
        tweet_embeddings.append(embedding)
        tweet_ids.append(tweet_id)
    
    # Get image description embeddings - optional, but can enrich topic analysis
    try:
        if deleted_filter:
            c.execute(f"""
                SELECT ia.tweet_id, ia.embedding 
                FROM image_analysis ia
                JOIN tweets t ON ia.tweet_id = t.tweet_id
                WHERE ia.embedding IS NOT NULL
                {deleted_filter}
            """)
        else:
            c.execute("SELECT tweet_id, embedding FROM image_analysis WHERE embedding IS NOT NULL")
            
        for tweet_id, emb_blob in c.fetchall():
            if emb_blob:
                embedding = pickle.loads(emb_blob)
                tweet_embeddings.append(embedding)
                tweet_ids.append(tweet_id)
    except sqlite3.OperationalError:
        # Table might not exist yet
        pass
    
    conn.close()
    
    return tweet_ids, tweet_embeddings

def get_tweet_text_by_id(tweet_ids):
    """Get tweet text for a list of tweet IDs"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Check if deleted column exists
    c.execute("PRAGMA table_info(tweets)")
    columns = c.fetchall()
    has_deleted_column = "deleted" in [column[1] for column in columns]
    
    result = {}
    for tweet_id in tweet_ids:
        if has_deleted_column:
            c.execute("SELECT tweet_text FROM tweets WHERE tweet_id = ? AND (deleted IS NULL OR deleted = 0)", (tweet_id,))
        else:
            c.execute("SELECT tweet_text FROM tweets WHERE tweet_id = ?", (tweet_id,))
            
        row = c.fetchone()
        if row and row[0]:
            result[tweet_id] = row[0]
    
    conn.close()
    return result

def analyze_topics(num_topics=10, deleted_filter=""):
    """Analyze topics in the bookmarked tweets using clustering"""
    # Get embeddings, with optional filter for non-deleted tweets
    tweet_ids, embeddings = get_all_embeddings(deleted_filter)
    
    if not embeddings:
        return {"error": "No embeddings found in database"}
    
    # Convert to numpy array
    embeddings_array = np.array(embeddings)
    
    # Use K-means clustering to find topics
    kmeans = KMeans(n_clusters=num_topics, random_state=42)
    cluster_labels = kmeans.fit_predict(embeddings_array)
    
    # Group tweet IDs by cluster
    clusters = {}
    for i, cluster_id in enumerate(cluster_labels):
        if cluster_id not in clusters:
            clusters[cluster_id] = []
        clusters[cluster_id].append(tweet_ids[i])
    
    # Get tweet text for each cluster
    cluster_texts = {}
    for cluster_id, cluster_tweet_ids in clusters.items():
        # Deduplicate tweet IDs (same tweet might appear multiple times due to image embeddings)
        unique_tweet_ids = list(set(cluster_tweet_ids))
        texts = get_tweet_text_by_id(unique_tweet_ids)
        cluster_texts[cluster_id] = {
            "tweet_ids": unique_tweet_ids,
            "texts": list(texts.values()),
            "count": len(unique_tweet_ids)
        }
    
    # Get topic names using OpenAI
    labeled_clusters = name_topics(cluster_texts)
    
    # Count tweets per topic and sort by popularity
    topic_counts = [(topic, data["count"]) for topic, data in labeled_clusters.items()]
    topic_counts.sort(key=lambda x: x[1], reverse=True)
    
    return {
        "topics": labeled_clusters,
        "topic_counts": topic_counts,
        "total_tweets": len(set(tweet_ids))
    }

def name_topics(cluster_texts, max_samples=5):
    """Use OpenAI to name the topics based on sample tweets"""
    client = OpenAI()
    labeled_clusters = {}
    
    for cluster_id, data in cluster_texts.items():
        # Limit to a few examples to keep prompt size reasonable
        sample_texts = data["texts"][:max_samples]
        
        if not sample_texts:
            continue
            
        try:
            # Create prompt with sample tweets
            prompt = "Based on these tweets, what's the main topic or theme? Give a short 2-3 word label:\n\n"
            for i, text in enumerate(sample_texts):
                prompt += f"{i+1}. {text}\n"
            
            # Get topic name from OpenAI
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",  # Using cheaper model for topic labeling
                messages=[
                    {"role": "system", "content": "You're an expert at identifying themes and topics in text. Your answers must be brief - provide only the topic label (2-3 words max)."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=20
            )
            
            topic = response.choices[0].message.content.strip()
            
            # Add to labeled clusters
            labeled_clusters[topic] = {
                "tweet_ids": data["tweet_ids"],
                "sample_texts": sample_texts,
                "count": data["count"]
            }
            
        except Exception as e:
            print(f"Error getting topic name for cluster {cluster_id}: {e}")
            labeled_clusters[f"Topic {cluster_id+1}"] = {
                "tweet_ids": data["tweet_ids"],
                "sample_texts": sample_texts,
                "count": data["count"]
            }
    
    return labeled_clusters

def get_topic_visualization_data(deleted_filter=""):
    """Generate 2D visualization data for topic clusters"""
    tweet_ids, embeddings = get_all_embeddings(deleted_filter)
    
    if not embeddings:
        return {"error": "No embeddings found in database"}
    
    # Convert to numpy array
    embeddings_array = np.array(embeddings)
    
    # Reduce dimensions to 2D using PCA
    pca = PCA(n_components=2)
    coords_2d = pca.fit_transform(embeddings_array)
    
    # Cluster into topics
    num_topics = min(10, len(embeddings) // 5) if len(embeddings) > 10 else 3
    kmeans = KMeans(n_clusters=num_topics, random_state=42)
    cluster_labels = kmeans.fit_predict(embeddings_array)
    
    # Create visualization data points
    viz_data = []
    for i, (tweet_id, coord, cluster) in enumerate(zip(tweet_ids, coords_2d, cluster_labels)):
        viz_data.append({
            "tweet_id": tweet_id,
            "x": float(coord[0]),  # Convert np.float to Python float for JSON
            "y": float(coord[1]),
            "cluster": int(cluster)  # Convert np.int to Python int for JSON
        })
    
    return {
        "points": viz_data,
        "num_clusters": num_topics
    }