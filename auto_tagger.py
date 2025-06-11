"""
Auto-tagging system for tweets using OpenAI GPT for content analysis.
Automatically creates and assigns relevant tags based on tweet content.
"""

import sqlite3
import json
from openai import OpenAI
from typing import List, Dict, Tuple
import re

DB_NAME = "tweets.db"

class AutoTagger:
    def __init__(self):
        self.client = OpenAI()
    
    def analyze_tweet_content(self, tweet_text: str, image_descriptions: List[str] = None) -> List[str]:
        """
        Analyze tweet content and return suggested tags.
        
        Args:
            tweet_text: The main tweet text
            image_descriptions: List of image analysis descriptions
            
        Returns:
            List of suggested tag names
        """
        # Combine tweet text and image descriptions
        content_parts = [tweet_text]
        if image_descriptions:
            content_parts.extend(image_descriptions)
        
        full_content = " ".join(content_parts)
        
        prompt = f"""
        Analyze this tweet content and suggest 2-5 relevant tags that categorize its main topics, themes, or subject matter.

        Content: "{full_content}"

        Guidelines:
        - Tags should be single words or short phrases (1-3 words max)
        - Focus on main topics, industries, technologies, or themes
        - Use common, searchable terms (e.g., "AI", "startup", "investing", "crypto", "tech", "business")
        - Avoid overly specific or niche terms
        - Don't include author names or personal identifiers
        - Prefer broader categories that could apply to multiple tweets

        Return only a JSON array of tag names, nothing else:
        """
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a content categorization expert. Return only valid JSON arrays of tag names."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=100,
                temperature=0.3
            )
            
            content = response.choices[0].message.content.strip()
            
            # Extract JSON array from response
            json_match = re.search(r'\[.*\]', content)
            if json_match:
                tags = json.loads(json_match.group())
                # Clean and validate tags
                clean_tags = []
                for tag in tags:
                    if isinstance(tag, str) and 1 <= len(tag.strip()) <= 50:
                        clean_tags.append(tag.strip().lower())
                return clean_tags[:5]  # Max 5 tags
            
        except Exception as e:
            print(f"Error analyzing tweet content: {e}")
        
        return []
    
    def get_or_create_tag(self, tag_name: str, description: str = "") -> int:
        """
        Get existing tag ID or create new tag and return its ID.
        
        Args:
            tag_name: Name of the tag
            description: Optional description
            
        Returns:
            Tag ID
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        try:
            # Check if tag exists
            c.execute("SELECT id FROM tags WHERE LOWER(name) = LOWER(?)", (tag_name,))
            result = c.fetchone()
            
            if result:
                return result[0]
            
            # Create new tag
            c.execute("""
                INSERT INTO tags (name, description) 
                VALUES (?, ?)
            """, (tag_name, description))
            
            tag_id = c.lastrowid
            conn.commit()
            return tag_id
            
        except Exception as e:
            print(f"Error creating/getting tag '{tag_name}': {e}")
            return None
        finally:
            conn.close()
    
    def assign_tag_to_tweet(self, tweet_id: str, tag_id: int, confidence: float = 0.8) -> bool:
        """
        Assign a tag to a tweet with confidence score.
        
        Args:
            tweet_id: Tweet ID
            tag_id: Tag ID
            confidence: Confidence score (0.0 to 1.0)
            
        Returns:
            Success status
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        try:
            c.execute("""
                INSERT OR IGNORE INTO tweet_tags (tweet_id, tag_id, confidence, assigned_by)
                VALUES (?, ?, ?, 'auto')
            """, (tweet_id, tag_id, confidence))
            
            conn.commit()
            return c.rowcount > 0
            
        except Exception as e:
            print(f"Error assigning tag {tag_id} to tweet {tweet_id}: {e}")
            return False
        finally:
            conn.close()
    
    def auto_tag_tweet(self, tweet_id: str) -> List[str]:
        """
        Automatically analyze and tag a single tweet.
        
        Args:
            tweet_id: Tweet ID to analyze
            
        Returns:
            List of tags that were assigned
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        try:
            # Get tweet content
            c.execute("""
                SELECT tweet_text 
                FROM tweets 
                WHERE tweet_id = ? AND (deleted IS NULL OR deleted = 0)
            """, (tweet_id,))
            
            tweet_result = c.fetchone()
            if not tweet_result:
                return []
            
            tweet_text = tweet_result[0]
            
            # Get image descriptions if available
            c.execute("""
                SELECT description 
                FROM image_analysis 
                WHERE tweet_id = ?
            """, (tweet_id,))
            
            image_descriptions = [row[0] for row in c.fetchall() if row[0]]
            
        except Exception as e:
            print(f"Error fetching tweet data for {tweet_id}: {e}")
            return []
        finally:
            conn.close()
        
        # Analyze content and get suggested tags
        suggested_tags = self.analyze_tweet_content(tweet_text, image_descriptions)
        
        if not suggested_tags:
            return []
        
        # Create/get tags and assign them
        assigned_tags = []
        for tag_name in suggested_tags:
            tag_id = self.get_or_create_tag(tag_name)
            if tag_id and self.assign_tag_to_tweet(tweet_id, tag_id):
                assigned_tags.append(tag_name)
        
        return assigned_tags
    
    def get_untagged_tweets(self, limit: int = 100) -> List[str]:
        """
        Get tweets that don't have any tags assigned.
        
        Args:
            limit: Maximum number of tweets to return
            
        Returns:
            List of tweet IDs
        """
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        try:
            c.execute("""
                SELECT t.tweet_id 
                FROM tweets t
                LEFT JOIN tweet_tags tt ON t.tweet_id = tt.tweet_id
                WHERE tt.tweet_id IS NULL 
                AND (t.deleted IS NULL OR t.deleted = 0)
                ORDER BY t.id DESC
                LIMIT ?
            """, (limit,))
            
            return [row[0] for row in c.fetchall()]
            
        except Exception as e:
            print(f"Error getting untagged tweets: {e}")
            return []
        finally:
            conn.close()
    
    def batch_auto_tag(self, limit: int = 50, progress_callback=None) -> Dict[str, int]:
        """
        Automatically tag multiple untagged tweets.
        
        Args:
            limit: Maximum number of tweets to process
            progress_callback: Optional callback function for progress updates
            
        Returns:
            Dictionary with processing statistics
        """
        untagged_tweets = self.get_untagged_tweets(limit)
        
        stats = {
            "processed": 0,
            "tagged": 0,
            "errors": 0,
            "total_tags_created": 0
        }
        
        for i, tweet_id in enumerate(untagged_tweets):
            try:
                if progress_callback:
                    progress_callback(i + 1, len(untagged_tweets), tweet_id)
                
                assigned_tags = self.auto_tag_tweet(tweet_id)
                
                stats["processed"] += 1
                if assigned_tags:
                    stats["tagged"] += 1
                    stats["total_tags_created"] += len(assigned_tags)
                    
            except Exception as e:
                print(f"Error processing tweet {tweet_id}: {e}")
                stats["errors"] += 1
        
        return stats

def auto_tag_single_tweet(tweet_id: str) -> List[str]:
    """
    Convenience function to auto-tag a single tweet.
    
    Args:
        tweet_id: Tweet ID to tag
        
    Returns:
        List of assigned tag names
    """
    tagger = AutoTagger()
    return tagger.auto_tag_tweet(tweet_id)

def batch_auto_tag_tweets(limit: int = 50, progress_callback=None) -> Dict[str, int]:
    """
    Convenience function to batch auto-tag tweets.
    
    Args:
        limit: Maximum number of tweets to process
        progress_callback: Optional progress callback
        
    Returns:
        Processing statistics
    """
    tagger = AutoTagger()
    return tagger.batch_auto_tag(limit, progress_callback)