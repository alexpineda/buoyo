import os
import glob
import json
import sqlite3
import threading
import time
from datetime import datetime
from collections import defaultdict

# Task status constants
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"

# Global storage for task status
# In a production app, you'd use Redis or another persistent store
tasks = {}
task_counters = defaultdict(dict)
task_results = {}

class BackgroundTask:
    """Simple background task manager"""
    
    def __init__(self, task_id, task_type, func, **kwargs):
        self.task_id = task_id
        self.task_type = task_type
        self.func = func
        self.kwargs = kwargs
        self.thread = None
        self.start_time = None
        self.end_time = None
        
        # Initialize task status
        tasks[task_id] = {
            "id": task_id,
            "type": task_type,
            "status": PENDING,
            "progress": 0,
            "total": 0,
            "message": "Task queued",
            "created_at": datetime.now().isoformat(),
        }
        
        # Initialize task counter storage
        task_counters[task_id] = {
            "processed": 0,
            "failed": 0,
            "total_files": 0,
            "total_tweets": 0,
            "current_file": "",
            "current_tweet": "",
        }
    
    def start(self):
        """Start the background task in a new thread"""
        self.thread = threading.Thread(target=self._run_task)
        self.thread.daemon = True
        self.thread.start()
        
        # Update task status
        tasks[self.task_id]["status"] = RUNNING
        tasks[self.task_id]["message"] = "Task started"
        self.start_time = datetime.now()
        tasks[self.task_id]["started_at"] = self.start_time.isoformat()
        
        return self.task_id
    
    def _run_task(self):
        """Execute the task function and update status"""
        try:
            # Run the actual task function with the provided arguments
            result = self.func(task_id=self.task_id, **self.kwargs)
            
            # Update task status on completion
            tasks[self.task_id]["status"] = COMPLETED
            tasks[self.task_id]["progress"] = 100
            tasks[self.task_id]["message"] = "Task completed successfully"
            task_results[self.task_id] = result
            
        except Exception as e:
            # Update task status on failure
            tasks[self.task_id]["status"] = FAILED
            tasks[self.task_id]["message"] = f"Task failed: {str(e)}"
            print(f"Task {self.task_id} failed: {e}")
        
        # Set completion time
        self.end_time = datetime.now()
        tasks[self.task_id]["completed_at"] = self.end_time.isoformat()
        
        # Calculate duration
        duration = (self.end_time - self.start_time).total_seconds()
        tasks[self.task_id]["duration"] = duration

def generate_task_id():
    """Generate a unique task ID"""
    return f"task_{int(time.time())}_{os.getpid()}"

def get_task_status(task_id):
    """Get the current status of a task"""
    if task_id not in tasks:
        return None
    
    task_status = tasks[task_id].copy()
    
    # Add counters to the status
    if task_id in task_counters:
        counters = task_counters[task_id]
        task_status.update({
            "processed": counters.get("processed", 0),
            "failed": counters.get("failed", 0),
            "total_files": counters.get("total_files", 0),
            "total_tweets": counters.get("total_tweets", 0),
            "current_file": counters.get("current_file", ""),
            "current_tweet": counters.get("current_tweet", ""),
        })
    
    # Add results if task is completed
    if task_status["status"] == COMPLETED and task_id in task_results:
        task_status["result"] = task_results[task_id]
    
    return task_status

def update_task_progress(task_id, progress=None, total=None, message=None, **counters):
    """Update the progress of a task"""
    if task_id not in tasks:
        return False
    
    if progress is not None:
        tasks[task_id]["progress"] = progress
    
    if total is not None:
        tasks[task_id]["total"] = total
    
    if message is not None:
        tasks[task_id]["message"] = message
    
    # Update counters if provided
    if counters and task_id in task_counters:
        for key, value in counters.items():
            task_counters[task_id][key] = value
    
    # Calculate progress percentage if total is known
    if total is not None and total > 0 and progress is not None:
        percentage = min(100, int((progress / total) * 100))
        tasks[task_id]["percentage"] = percentage
    
    return True